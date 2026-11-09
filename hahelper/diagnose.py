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
                         "sudo -u postgres psql -p <porta-postgres> -c \"SELECT pg_wal_replay_resume();\""))
    if cfg.preferred and leader and leader != cfg.preferred:
        out.append(F("info", "Patroni", f"Pemimpin saat ini {leader}, bukan pemimpin utama {cfg.preferred}",
                     "Bila memakai skrip failback otomatis, peran akan dikembalikan setelah node itu sehat."))

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
            out.append(F("ok", "VIP", f"Alamat {v} dipegang {holder}",
                         "" if holder == leader else "Boleh berbeda dengan pemimpin; HAProxy tetap meneruskan ke pemimpin."))
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
        "time": s["time"], "poll_ms": s["poll_ms"],
    }
    out.sort(key=lambda f: -RANK[f["level"]])
    return summary, out
