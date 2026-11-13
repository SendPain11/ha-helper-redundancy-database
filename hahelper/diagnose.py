"""Mengubah data mentah menjadi ringkasan dan temuan berbahasa Indonesia."""
from .pgwire import lsn_to_int

LEADER_ROLES = ("leader", "standby_leader", "master", "primary")
RANK = {"ok": 0, "info": 1, "warn": 2, "crit": 3}


def F(level, area, title, detail="", action=""):
    return {"level": level, "area": area, "title": title, "detail": detail, "action": action}


def mb(b):
    return None if b is None else round(b / 1048576, 2)


def summarize(cfg, s):
    """Mengembalikan (ringkasan, temuan)."""
    out = []
    pat, et, hap, prod = s["patroni"], s["etcd"], s["haproxy"], s["produksi"]
    cl = pat.get("cluster")
    members = {m["name"]: m for m in (cl or {}).get("members", [])}
    nodes = []
    prod_lsn = prod.get("lsn_int") if prod.get("ok") else None

    for name, ip in cfg.db_nodes:
        n, m = pat["nodes"].get(name, {}), members.get(name, {})
        replayed = n.get("replayed") if n.get("ok") else None
        rep_int = replayed if isinstance(replayed, int) else (lsn_to_int(replayed) if replayed else None)
        role = m.get("role") or (n.get("role") if n.get("ok") else None)
        tags = dict(m.get("tags") or {})
        tags.update(n.get("tags") or {} if n.get("ok") else {})
        nodes.append({
            "name": name, "ip": ip, "reachable": n.get("ok", False), "role": role,
            "state": m.get("state") or n.get("state"), "timeline": m.get("timeline") or n.get("timeline"),
            "lag_mb": mb(m.get("lag")) if isinstance(m.get("lag"), (int, float)) else None,
            "behind_prod_mb": mb(prod_lsn - rep_int) if (prod_lsn is not None and rep_int is not None) else None,
            "replayed_int": rep_int, "replayed_ts": n.get("replayed_ts"), "replay_paused": n.get("replay_paused"),
            "nofailover": bool(tags.get("nofailover")), "in_cluster": name in members,
            "error": None if n.get("ok") else n.get("error"),
        })

    leaders = [x for x in nodes if x["role"] in LEADER_ROLES]
    leader = leaders[0]["name"] if len(leaders) == 1 else None
    paused = bool((cl or {}).get("pause")) or any(pat["nodes"].get(n, {}).get("pause") for n, _ in cfg.db_nodes)

    # ---------------- Patroni
    if not cl:
        out.append(F("crit", "Patroni", "Tidak ada node Patroni yang bisa dihubungi dari PC ini",
                     "Semua node DB tidak menjawab di porta %d." % cfg.p_patroni,
                     "Cek jaringan dan ufw: ufw allow from <IP-PC> to any port %d proto tcp" % cfg.p_patroni))
    elif not leaders:
        out.append(F("crit", "Patroni", "Cluster TANPA pemimpin",
                     "Tidak ada node berperan Leader/Standby Leader. Wajar beberapa detik saat perpindahan; "
                     "bila menetap, aplikasi tidak bisa menulis.", "patronictl -c /etc/patroni/config.yml list"))
    elif len(leaders) > 1:
        out.append(F("crit", "Patroni", "Lebih dari satu pemimpin: " + ", ".join(x["name"] for x in leaders),
                     "Kondisi berbahaya (split-brain).", "patronictl -c /etc/patroni/config.yml list"))
    else:
        out.append(F("ok", "Patroni", f"Pemimpin: {leader} ({leaders[0]['role'].replace('_', ' ')}), TL {leaders[0]['timeline']}"))
    if paused:
        out.append(F("warn", "Patroni", "Mode pemeliharaan (pause) AKTIF",
                     "Failover otomatis tidak berjalan selama mode ini aktif.",
                     "patronictl -c /etc/patroni/config.yml resume --wait"))
    tls = {x["timeline"] for x in nodes if x["timeline"] is not None and x["in_cluster"]}
    if len(tls) > 1:
        out.append(F("warn", "Patroni", "Timeline berbeda antar node: " + ", ".join(map(str, sorted(tls))),
                     "Node dengan TL berbeda belum mengikuti pemimpin."))
    for x in nodes:
        if not x["reachable"] and not x["in_cluster"]:
            out.append(F("crit" if x["name"] == leader else "warn", "Patroni", f"{x['name']} tidak terjangkau dan tidak ada di cluster",
                         x["error"] or "", "Node mati atau Patroni berhenti. Cek: systemctl status patroni"))
        elif not x["reachable"]:
            out.append(F("info", "Patroni", f"{x['name']}: REST 8008 tidak terjangkau dari PC ini", x["error"] or "",
                         "Node tercatat di cluster. Kemungkinan ufw belum mengizinkan IP PC ke 8008."))
        if x["role"] and x["role"] not in LEADER_ROLES and x["state"] not in ("running", "streaming"):
            out.append(F("warn", "Patroni", f"{x['name']} berstatus {x['state']}", "Replica tidak sedang mengikuti pemimpin."))
        if x["lag_mb"] is not None and x["lag_mb"] > cfg.lag_mb_warn:
            out.append(F("warn", "Replikasi", f"{x['name']} tertinggal {x['lag_mb']} MB dari pemimpin"))
        if x["replay_paused"]:
            out.append(F("warn", "Replikasi", f"Penerapan data di {x['name']} DIJEDA",
                         "Mungkin sisa pengecekan data yang menjeda penerapan lalu terhenti.",
                         "sudo -u postgres psql -p 5433 -c \"SELECT pg_wal_replay_resume();\""))
    if cfg.preferred and leader and leader != cfg.preferred:
        out.append(F("info", "Patroni", f"Pemimpin saat ini {leader}, bukan pemimpin utama {cfg.preferred}",
                     "Bila skrip pengembalian pemimpin dipasang, peran dikembalikan setelah node utama sehat kembali."))

    # ---------------- streaming vs produksi
    if prod.get("off"):
        pass
    elif not prod.get("ok"):
        out.append(F("warn", "Produksi", "Tidak bisa tersambung ke produksi", prod.get("error", ""),
                     "Cek alamat/sandi di config.ini [produksi]. Setelah cutover, ganti ke alamat pengelolaan produksi."))
    else:
        if prod.get("recovery"):
            out.append(F("info", "Produksi", "Alamat produksi di config menunjuk server yang sedang mode standby"))
        if prod.get("read_only"):
            out.append(F("info", "Produksi", "Produksi dalam mode hanya-baca (default_transaction_read_only = on)",
                         "Normal saat cutover."))
        for x in nodes:
            b = x["behind_prod_mb"]
            if b is not None and b > cfg.lag_mb_warn:
                out.append(F("warn", "Replikasi", f"{x['name']} tertinggal {b} MB dari produksi",
                             "Tekan 'Cek streaming & data' untuk mengukur waktu menyusul."))

    # ---------------- pemimpin & replikasi (dibaca lewat jalur tulis HAProxy)
    u = s.get("utama") or {}
    mode = s.get("mode", "sebelum")
    lead_role = leaders[0]["role"] if len(leaders) == 1 else None
    if not u.get("ok"):
        out.append(F("warn", "Pemimpin", "Tidak bisa membaca database lewat jalur tulis HAProxy",
                     u.get("error", ""), f"Cek [cluster] alamat_haproxy/sandi di config.ini dan ufw porta {cfg.p_tulis}."))
    else:
        if mode == "sesudah":
            if lead_role == "standby_leader":
                out.append(F("crit", "Pemimpin", "Cluster MASIH STANDBY: aplikasi tidak bisa menulis",
                             "Mode sesudah cutover ([produksi] aktif = no), tetapi pemimpin berperan Standby Leader.",
                             "Hari-H Langkah 5: patronictl edit-config, hapus blok standby_cluster"))
            elif u["recovery"] or u["read_only"]:
                out.append(F("crit", "Pemimpin", f"Jalur tulis menuju server HANYA-BACA ({u['server']})",
                             "Aplikasi tidak bisa menyimpan data.",
                             "patronictl -c /etc/patroni/config.yml list ; cek HAProxy jalur tulis"))
            else:
                out.append(F("ok", "Pemimpin", f"Database utama {leader or u['server']} bisa menulis",
                             f"Koneksi {u['conns']} dari maksimal {u['max_conn']}"))
            # arsip WAL = dasar backup
            ar = u["arsip"]
            if u["archive_mode"] not in ("on", "always"):
                out.append(F("warn", "Backup", "Arsip WAL TIDAK aktif",
                             "Backup point-in-time tidak berjalan.",
                             "patronictl edit-config: archive_mode 'on' (perlu restart bergilir)"))
            elif ar["gagal_umur_detik"] is not None and (ar["umur_detik"] is None or ar["gagal_umur_detik"] < ar["umur_detik"]):
                out.append(F("crit", "Backup", f"Arsip WAL GAGAL ({ar['gagal_wal']})",
                             "Backup macet dan WAL menumpuk di disk pemimpin. Wajar hanya beberapa menit "
                             "di Hari-H antara Langkah 5 dan 6B.",
                             f"sudo -u postgres pgbackrest --stanza={cfg.stanza_cluster} check ; tail -20 /var/log/pgbackrest/*.log"))
            elif ar["umur_detik"] is None:
                out.append(F("warn", "Backup", "Belum ada satu pun WAL yang terarsip",
                             "", f"sudo -u postgres pgbackrest --stanza={cfg.stanza_cluster} check"))
            elif ar["umur_detik"] > 3600:
                out.append(F("warn", "Backup", f"Arsip WAL terakhir {ar['umur_detik'] // 60} menit lalu",
                             "Biasanya beberapa menit sekali pada database yang sibuk."))
            else:
                out.append(F("ok", "Backup", f"Arsip WAL berjalan, terakhir {ar['umur_detik'] // 60} menit lalu",
                             f"{ar['wal_terakhir']} ; total {ar['jumlah']}"))
        elif lead_role == "leader":
            out.append(F("warn", "Pemimpin", "Cluster sudah menjadi UTAMA, tetapi config masih memantau produksi",
                         "Dashboard masih membandingkan dengan produksi lama.",
                         "sudo ha-helper config -> [produksi] aktif = no, lalu sudo ha-helper sandi"))
        if u["max_conn"] and u["conns"] > 0.8 * u["max_conn"]:
            out.append(F("warn", "Pemimpin", f"Koneksi {u['conns']} dari maksimal {u['max_conn']} (>80%)",
                         "Aplikasi bisa ditolak bila penuh."))
        # replikasi pemimpin -> replica, dilihat dari sisi pemimpin
        streaming = {r["nama"]: r for r in u["replikasi"]}
        for x in nodes:
            if x["name"] == leader or not x["in_cluster"]:
                continue
            r = streaming.get(x["name"])
            if not r:
                out.append(F("warn", "Replikasi", f"{x['name']} TIDAK menerima replikasi dari pemimpin",
                             "Tidak tercatat di pg_stat_replication pemimpin.",
                             "journalctl -u patroni -n 30 --no-pager  (di node itu)"))
            elif r["status"] != "streaming":
                out.append(F("warn", "Replikasi", f"{x['name']} berstatus {r['status']} (belum streaming)"))
    for x in nodes:
        r = next((r for r in (u.get("replikasi") or []) if r["nama"] == x["name"]), None)
        x["rep_status"] = r["status"] if r else None
        x["rep_jeda"] = r["jeda_detik"] if r else None

    # ---------------- etcd
    em = et["members"]
    reach = [n for n, r in em.items() if r["ok"]]
    healthy = [n for n, r in em.items() if r["ok"] and r.get("healthy")]
    total = len(cfg.etcd_nodes)
    if not reach:
        out.append(F("info", "etcd", "etcd tidak terjangkau dari PC ini",
                     "Bagian etcd tidak bisa dinilai.", "Bila perlu: ufw allow from <IP-PC> to any port %d proto tcp (di 5 anggota)" % cfg.p_etcd))
    else:
        quorum = total // 2 + 1
        if not healthy:
            out.append(F("crit", "etcd", f"etcd KEHILANGAN KUORUM: anggota yang terjangkau ({len(reach)}) semua menjawab tidak sehat",
                         "Patroni tidak bisa mempertahankan pemimpin.", "ETCDCTL_API=3 etcdctl endpoint health --cluster"))
        elif len(healthy) < total:
            sick = [n for n in em if n not in healthy]
            out.append(F("warn", "etcd", f"etcd sehat {len(healthy)} dari {total}",
                         "Tidak sehat / tidak terjangkau: " + ", ".join(sick) + f". Masih aman selama ≥ {quorum} sehat."))
        else:
            out.append(F("ok", "etcd", f"etcd sehat {total}/{total}, pemimpin etcd: {et.get('leader') or '?'}"))
        terms = {em[n].get("raft_term") for n in reach if em[n].get("raft_term")}
        if len(terms) > 1:
            out.append(F("warn", "etcd", "RAFT TERM berbeda antar anggota", "Ada anggota yang belum sinkron."))
        big = max((em[n].get("db_size", 0) for n in reach), default=0)
        if big > 1.5 * 1024 ** 3:
            out.append(F("warn", "etcd", f"Ukuran data etcd {round(big/1048576)} MB mendekati batas 2 GB",
                         "", "ETCDCTL_API=3 etcdctl defrag (satu per satu)"))

    # ---------------- HAProxy
    hn = hap["nodes"]
    for name, _ip in cfg.db_nodes:
        r = hn.get(name, {})
        if not r.get("ok"):
            out.append(F("warn", "HAProxy", f"Statistik HAProxy {name} tidak terbaca", r.get("error", ""),
                         "Cek haproxy berjalan dan ufw porta 7000 untuk IP PC."))
            continue
        w = r["proxies"].get("pgsql_tulis") or {}
        up = [sv["name"] for sv in w.get("servers", []) if (sv["status"] or "").startswith("UP")]
        if not up:
            out.append(F("crit", "HAProxy", f"{name}: jalur tulis (5432) TANPA server",
                         "Aplikasi yang lewat node ini tidak bisa menulis. Wajar beberapa detik saat perpindahan."))
        elif len(up) > 1:
            out.append(F("crit", "HAProxy", f"{name}: {len(up)} server UP di jalur tulis", "Seharusnya tepat satu."))
        elif leader and up[0] != leader:
            out.append(F("warn", "HAProxy", f"{name}: jalur tulis ke {up[0]}, pemimpin {leader}",
                         "Wajar beberapa detik setelah perpindahan."))
    for v, holder in hap["vip"].items():
        if holder == "TIDAK TERJANGKAU":
            out.append(F("warn", "VIP", f"Alamat {v} tidak terjangkau dari PC",
                         "Aplikasi yang memakai alamat ini mungkin juga tidak bisa tersambung.", "systemctl status keepalived  (di ketiga node)"))
        elif holder:
            if holder == leader or not leader:
                out.append(F("ok", "VIP", f"Alamat {v} dipegang {holder}" + (" (node pemimpin)" if holder == leader else "")))
            else:
                out.append(F("info", "VIP", f"Alamat {v} dipegang {holder}, pemimpin {leader}",
                             "keepalived memindahkan alamat ke node pemimpin dalam +/- 6 detik. Bila menetap: HAProxy di node "
                             "pemimpin mati (wajar, HAProxy node ini tetap meneruskan ke pemimpin) atau chk_leader belum terpasang.",
                             "journalctl -u keepalived -n 20 --no-pager"))
        else:
            out.append(F("warn", "VIP", f"Pemegang alamat {v} tidak cocok dengan node mana pun"))

    level = max((f["level"] for f in out), key=lambda l: RANK[l], default="ok")
    summary = {
        "level": level, "leader": leader, "paused": paused, "nodes": nodes,
        "etcd": {"members": em, "leader": et.get("leader"), "healthy": len(healthy), "total": total},
        "haproxy": {n: ({"ok": r["ok"], "error": r.get("error"),
                         "tulis": (r.get("proxies", {}).get("pgsql_tulis") or {}).get("servers", []),
                         "baca": (r.get("proxies", {}).get("pgsql_baca") or {}).get("servers", [])}) for n, r in hn.items()},
        "vip": hap["vip"], "produksi": {k: v for k, v in prod.items() if k != "lsn_int"},
        "mode": s.get("mode", "sebelum"), "utama": s.get("utama"),
        "time": s["time"], "poll_ms": s["poll_ms"],
    }
    out.sort(key=lambda f: -RANK[f["level"]])
    return summary, out
