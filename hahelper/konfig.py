"""Cek keseragaman konfigurasi antar node. Hanya membaca.

Dua sumber:
1. Laporan pelapor di tiap node (hahelper-lapor.py, cron 5 menit): ringkasan + sidik md5 berkas, tanpa sandi.
2. Yang bisa dibaca helper langsung dari luar (tiap 60 detik): pengaturan PostgreSQL pemimpin vs replica
   (dan produksi bila masih aktif), versi Patroni tiap node, daftar anggota etcd menurut tiap anggota,
   daftar server HAProxy di tiap node.
"""
import hmac
import json
import os
import re
import secrets
import threading
import time

from . import backup, collect
from .diagnose import F
from .pgwire import Conn

BASI = 15 * 60          # laporan lebih tua dari ini dianggap basi
JARAK_JAUH_DETIK = 60

# kunci yang MEMANG boleh berbeda antar node (hanya ditampilkan)
WAJAR = {"sistem.hostname", "sistem.ip", "etcd.nama", "keepalived.prioritas", "keepalived.hari_h_prioritas",
         "ufw.aturan_md5", "ufw.jumlah_aturan", "keepalived.state",
         "etcd.default_md5"}  # /etc/default/etcd memang beda per node (ETCD_NAME, alamat node sendiri)
# kunci yang hanya informasi (tidak dinilai)
INFO = {"hosts.nama", "hosts.jumlah_baris", "backup.nas_terpasang", "ufw.status", "ufw.tanpa_komentar",
        "ufw.tanpa_sumber", "ufw.ip_ganda", "etcd.layanan", "patroni.layanan", "haproxy.layanan",
        "keepalived.layanan", "sistem.jam_sinkron", "etcd.initial_cluster_state", "backup.cron_jadwal"}

LABEL = {
    "sistem.os": "Sistem operasi", "sistem.zona_waktu": "Zona waktu", "sistem.jam_sinkron": "Jam tersinkron (NTP)",
    "sistem.python": "Python", "sistem.hostname": "Hostname", "sistem.ip": "Alamat IP",
    "hosts.isi_md5": "/etc/hosts (sidik)", "hosts.jumlah_baris": "/etc/hosts baris node", "hosts.nama": "/etc/hosts nama",
    "etcd.versi": "etcd versi", "etcd.layanan": "etcd layanan", "etcd.nama": "etcd nama anggota",
    "etcd.initial_cluster": "etcd initial-cluster (nama)", "etcd.initial_cluster_md5": "etcd initial-cluster (sidik)",
    "etcd.initial_cluster_state": "etcd initial-cluster-state", "etcd.cluster_token": "etcd cluster token",
    "etcd.compaction": "etcd auto-compaction",
    "etcd.default_initial_cluster": "/etc/default/etcd ETCD_INITIAL_CLUSTER", "etcd.default_nama_disebut": "/etc/default/etcd nama yang disebut",
    "etcd.sumber_konfig": "etcd membaca konfigurasi dari", "etcd.data_dir": "etcd data-dir",
    "etcd.default_initial_cluster": "/etc/default/etcd ETCD_INITIAL_CLUSTER (nama)", "etcd.default_md5": "/etc/default/etcd (sidik)",
    "etcd.unit": "etcd unit systemd membaca",
    "patroni.versi": "Patroni versi", "patroni.layanan": "Patroni layanan", "patroni.scope": "Patroni scope",
    "patroni.namespace": "Patroni namespace", "patroni.etcd_hosts": "Patroni → etcd hosts",
    "patroni.data_dir": "Patroni data_dir", "patroni.bin_dir": "Patroni bin_dir", "patroni.porta_pg": "Porta PostgreSQL",
    "patroni.porta_rest": "Porta REST Patroni", "patroni.tags": "Patroni tags", "postgresql.versi": "PostgreSQL versi",
    "haproxy.versi": "HAProxy versi", "haproxy.layanan": "HAProxy layanan", "haproxy.cfg_md5": "haproxy.cfg (sidik)",
    "haproxy.server": "HAProxy daftar server",
    "keepalived.layanan": "keepalived layanan", "keepalived.prioritas": "keepalived prioritas",
    "keepalived.vrid": "keepalived VRID", "keepalived.vip": "keepalived VIP sekarang",
    "keepalived.tanpa_prioritas_md5": "keepalived.conf (sidik, tanpa prioritas/IP sendiri)",
    "keepalived.hari_h_vip": "keepalived Hari-H VIP", "keepalived.hari_h_prioritas": "keepalived Hari-H prioritas",
    "keepalived.hari_h_md5": "keepalived Hari-H (sidik, tanpa prioritas/IP sendiri)",
    "backup.pgbackrest_versi": "pgBackRest versi", "backup.pgbackrest_conf_md5": "pgbackrest.conf (sidik)",
    "backup.stanza": "pgBackRest stanza", "backup.retensi_full": "Retensi full", "backup.cron_md5": "Cron postgres (sidik)",
    "backup.cron_jadwal": "Cron backup", "backup.nas_fstab_md5": "fstab NAS (sidik)", "backup.nas_terpasang": "NAS terpasang",
    "ufw.status": "ufw status", "ufw.jumlah_aturan": "ufw jumlah aturan", "ufw.tanpa_komentar": "ufw aturan tanpa komentar",
    "ufw.tanpa_sumber": "ufw aturan tanpa IP sumber (Anywhere)", "ufw.ip_ganda": "ufw IP dengan >1 aturan",
    "ufw.aturan_md5": "ufw aturan (sidik)",
}
URUT = ["sistem", "hosts", "etcd", "patroni", "postgresql", "haproxy", "keepalived", "backup", "ufw"]

PG_SET = ("server_version", "max_connections", "shared_buffers", "work_mem", "maintenance_work_mem",
          "effective_cache_size", "wal_level", "max_wal_senders", "max_replication_slots", "wal_keep_size",
          "max_wal_size", "hot_standby", "hot_standby_feedback", "max_worker_processes", "max_locks_per_transaction",
          "max_prepared_transactions", "archive_mode", "archive_command", "TimeZone", "server_encoding",
          "lc_collate", "lc_ctype", "data_checksums", "listen_addresses", "password_encryption",
          "track_commit_timestamp", "wal_log_hints", "synchronous_commit")
PG_HARUS_SAMA = {"server_version", "max_connections", "max_wal_senders", "max_worker_processes",
                 "max_locks_per_transaction", "max_prepared_transactions", "wal_level", "TimeZone", "server_encoding",
                 "lc_collate", "lc_ctype", "data_checksums", "shared_buffers", "archive_mode", "archive_command",
                 "max_replication_slots", "hot_standby", "wal_log_hints"}

# standby (cluster sebelum cutover) wajib bernilai >= produksi, kalau tidak replikasi berhenti
PG_MIN_STANDBY = {"max_connections", "max_worker_processes", "max_wal_senders", "max_prepared_transactions",
                  "max_locks_per_transaction"}
# harus sama dengan produksi (data dibentuk sama)
PG_PROD_SAMA = {"TimeZone", "server_encoding", "lc_collate", "lc_ctype"}


def _aman(nama):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(nama))[:60] or "tanpa-nama"


def _pg(host, port, user, pw, db):
    with Conn(host, port, user, pw, db, timeout=8) as c:
        addr = c.query("SELECT coalesce(host(inet_server_addr()),'lokal'), pg_is_in_recovery()::text")[0]
        names = ",".join("'%s'" % n for n in PG_SET)
        rows = c.query(f"SELECT name, setting || coalesce(unit,'') FROM pg_settings WHERE name IN ({names})")
    return {"server": addr[0], "recovery": addr[1] == "true", "set": dict(rows)}


class Konfig:
    def __init__(self, cfg, datadir, mon):
        self.cfg, self.mon = cfg, mon
        self.dir = os.path.join(datadir, "konfig")
        os.makedirs(self.dir, exist_ok=True)
        self.token = getattr(cfg, "konfig_token", "") or self._token_file(datadir)
        self.lock = threading.Lock()
        self.jauh = {"waktu": None}
        self.hasil = {"baris": [], "node": [], "temuan": []}
        self.backup = {}
        self.cekpenuh = None

    # ------------------------------------------------------------------ token
    @staticmethod
    def _token_file(datadir):
        p = os.path.join(datadir, "konfig-token")
        try:
            with open(p) as f:
                t = f.read().strip()
            if t:
                return t
        except OSError:
            pass
        t = secrets.token_hex(16)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t + "\n")
        return t

    # ------------------------------------------------------------------ terima laporan
    def _kenali(self, ip, laporan):
        """Nama node menurut config.ini dari IP pengirim (atau IP yang dilaporkan)."""
        semua = dict((ip_, n) for n, ip_ in self.cfg.etcd_nodes + self.cfg.db_nodes)
        if ip in semua:
            return semua[ip]
        for x in str(laporan.get("isi", {}).get("sistem.ip", "")).split():
            if x in semua:
                return semua[x]
        return None

    def terima(self, ip, token, laporan):
        if not token or not hmac.compare_digest(str(token), self.token):
            return 403, {"error": "token salah"}
        if not isinstance(laporan, dict) or not isinstance(laporan.get("isi"), dict):
            return 400, {"error": "format laporan salah"}
        nama = self._kenali(ip, laporan)
        if not nama:
            return 403, {"error": f"IP {ip} bukan node cluster di config.ini"}
        isi = {str(k)[:60]: (v if isinstance(v, (int, float)) else str(v)[:400]) for k, v in list(laporan["isi"].items())[:200]}
        data = {"node": nama, "ip": ip, "host": str(laporan.get("host", ""))[:60], "diterima": time.time(),
                "pelapor": str(laporan.get("pelapor", ""))[:10], "isi": isi}
        b = laporan.get("backup")
        if isinstance(b, dict) and len(json.dumps(b)) < 200000:
            data["backup"] = b
        p = os.path.join(self.dir, _aman(nama) + ".json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)
        return 200, {"ok": True, "node": nama}

    def _laporan(self):
        out = {}
        for n, _ in self._semua_node():
            p = os.path.join(self.dir, _aman(n) + ".json")
            try:
                with open(p, encoding="utf-8") as f:
                    out[n] = json.load(f)
            except (OSError, ValueError):
                pass
        return out

    def _semua_node(self):
        seen, out = set(), []
        for n, ip in self.cfg.db_nodes + self.cfg.etcd_nodes:
            if n not in seen:
                seen.add(n)
                out.append((n, ip))
        return out

    # ------------------------------------------------------------------ cek dari luar
    def _cek_jauh(self):
        c, j = self.cfg, {"waktu": time.time()}
        # PostgreSQL
        src = []
        if c.prod_on:
            src.append(("Produksi", (c.prod_host, c.prod_port, c.prod_user, c.prod_pass, c.db)))
        src += [("Jalur tulis", (c.cl_host, c.p_tulis, c.cl_user, c.cl_pass, c.db)),
                ("Jalur baca", (c.cl_host, c.p_baca, c.cl_user, c.cl_pass, c.db))]
        fut = [(lbl, collect.POOL.submit(collect._try, _pg, *a)) for lbl, a in src]
        j["pg"] = [{"label": lbl, **f.result()} for lbl, f in fut]
        # Patroni versi & tag tiap node
        pv = {}
        for n, ip in c.db_nodes:
            try:
                _, body = collect.http(f"http://{ip}:{c.p_patroni}/patroni", c.timeout)
                d = json.loads(body)
                pv[n] = {"versi": (d.get("patroni") or {}).get("version"), "pg": d.get("server_version"),
                         "tags": ", ".join(f"{k}={v}" for k, v in sorted((d.get("tags") or {}).items())) or "-"}
            except Exception as e:  # noqa: BLE001
                pv[n] = {"error": f"{type(e).__name__}"}
        j["patroni"] = pv
        # Anggota etcd menurut tiap anggota
        em = {}
        for n, ip in c.etcd_nodes:
            try:
                _, body = collect.http(f"http://{ip}:{c.p_etcd}/v3/cluster/member/list", c.timeout, data=b"{}")
                ms = json.loads(body).get("members", [])
                em[n] = sorted(f"{m.get('name') or '(belum mulai)'}={','.join(m.get('peerURLs', []))}" for m in ms)
            except Exception as e:  # noqa: BLE001
                em[n] = {"error": f"{type(e).__name__}"}
        j["etcd"] = em
        # Server HAProxy menurut tiap node (dari putaran pemantau terakhir)
        st = self.mon.state or {}
        hp = {}
        for n, h in (st.get("haproxy") or {}).items():
            if n.startswith("VIP ") or not h.get("ok"):
                continue
            hp[n] = {"tulis": sorted(x["name"] for x in h.get("tulis", [])), "baca": sorted(x["name"] for x in h.get("baca", []))}
        j["haproxy"] = hp
        with self.lock:
            self.jauh = j

    # ------------------------------------------------------------------ banding
    def nilai(self):
        lap, sekarang = self._laporan(), time.time()
        cfg = self.cfg
        db_names = {n for n, _ in cfg.db_nodes}
        etcd_ip = {ip for _, ip in cfg.etcd_nodes} | {n for n, _ in cfg.etcd_nodes}
        nodes, temuan = [], []
        for n, ip in self._semua_node():
            r = lap.get(n)
            jenis = "database" if n in db_names else "witness"
            if not r:
                nodes.append({"node": n, "ip": ip, "jenis": jenis, "umur": None, "host": None})
                continue
            umur = int(sekarang - r["diterima"])
            nodes.append({"node": n, "ip": ip, "jenis": jenis, "umur": umur, "host": r.get("host"), "pelapor": r.get("pelapor")})
            if umur > BASI:
                temuan.append(F("warn", "Konfigurasi", f"{n}: laporan konfigurasi basi",
                                f"Laporan terakhir {umur // 60} menit lalu; pelapor di node itu berhenti mengirim.", "cat /var/log/hahelper-lapor.log  (di node itu)"))
        segar = {n: r for n, r in lap.items() if sekarang - r["diterima"] <= BASI}
        if not lap:
            temuan.append(F("info", "Konfigurasi", "Belum ada node yang melaporkan konfigurasi",
                            "Pasang pelapor di tiap node agar keseragaman konfigurasi ikut dicek.",
                            "bash pasang-lapor.sh <URL-helper> <token>"))
        kurang = [x["node"] for x in nodes if x["umur"] is None]
        if lap and kurang:
            temuan.append(F("info", "Konfigurasi", "Belum melapor: " + ", ".join(kurang)))

        # --- baris perbandingan
        kunci = sorted({k for r in segar.values() for k in r["isi"]},
                       key=lambda k: (URUT.index(k.split(".")[0]) if k.split(".")[0] in URUT else 99, k))
        baris = []
        for k in kunci:
            punya = {n: r["isi"][k] for n, r in segar.items() if k in r["isi"]}
            vals = {json.dumps(v) for v in punya.values()}
            if k in WAJAR:
                status = "WAJAR"
            elif k in INFO:
                status = "INFO"
            else:
                status = "SAMA" if len(vals) <= 1 else "BEDA"
            row = {"kunci": k, "label": LABEL.get(k, k), "nilai": punya, "status": status, "catatan": "", "salah": []}
            if status == "BEDA":
                hit = {}
                for n, v in punya.items():
                    hit.setdefault(json.dumps(v), []).append(n)
                kel = sorted(hit.values(), key=len)
                beda_node = [n for g in kel[:-1] for n in g] if len(kel[-1]) > len(kel[-2]) else [n for g in kel for n in g]
                row["salah"] = beda_node
                row["catatan"] = "Berbeda di: " + ", ".join(beda_node)
                pendek = lambda v: (str(v)[:70] + "…") if len(str(v)) > 70 else str(v)  # noqa: E731
                lain = [n for n in punya if n not in beda_node]
                temuan.append(F("warn", "Konfigurasi", f"{LABEL.get(k, k)} BERBEDA antar node",
                                "Beda di " + "; ".join(f"{n} ({pendek(punya[n])})" for n in beda_node) +
                                (f" — node lain: {pendek(punya[lain[0]])}" if lain else ""),
                                "Samakan berkasnya, lalu tunggu laporan berikut (≤5 menit)."))
            baris.append(row)

        def aturan(k, uji, judul, saran, lvl="warn"):
            for row in baris:
                if row["kunci"] != k:
                    continue
                salah = [n for n, v in row["nilai"].items() if not uji(v, n)]
                if salah:
                    row["status"] = "SALAH"
                    row["salah"] = sorted(set(row["salah"]) | set(salah))
                    row["catatan"] = (row["catatan"] + "; " if row["catatan"] else "") + judul
                    temuan.append(F(lvl, "Konfigurasi", f"{judul}: " + ", ".join(salah),
                                    "; ".join(f"{n}: {row['nilai'][n]}" for n in salah)[:300], saran))

        aturan("ufw.status", lambda v, n: str(v).startswith("active"), "ufw tidak aktif", "ufw enable")
        aturan("ufw.tanpa_komentar", lambda v, n: str(v) == "0", "ada aturan ufw tanpa komentar",
               "ufw show added  → hapus lalu tambah lagi dengan comment '<nama> - <fungsi porta>'")
        aturan("ufw.tanpa_sumber", lambda v, n: str(v) == "0", "ada aturan ufw tanpa IP sumber (terbuka untuk semua)",
               "ufw show added  → ganti dengan aturan 'from <IP>'")
        aturan("ufw.ip_ganda", lambda v, n: str(v) == "0", "ada IP dengan lebih dari 1 aturan ufw (kebijakan 1 IP = 1 aturan)",
               "ufw show added  → gabungkan porta jadi satu aturan", "info")
        for svc in ("etcd", "patroni", "haproxy", "keepalived"):
            aturan(f"{svc}.layanan", lambda v, n: v == "active", f"layanan {svc} tidak aktif", f"systemctl status {svc}")
        aturan("sistem.jam_sinkron", lambda v, n: v == "ya", "jam tidak tersinkron NTP",
               "timedatectl  ; systemctl status systemd-timesyncd")
        aturan("etcd.initial_cluster_state", lambda v, n: v == "existing",
               "etcd initial-cluster-state bukan 'existing'", "ubah jadi existing di /etc/etcd/etcd.conf.yaml (tanpa restart)")
        aturan("patroni.etcd_hosts", lambda v, n: {x.rsplit(":", 1)[0] for x in str(v).split(", ") if x} <= etcd_ip,
               "Patroni menunjuk etcd yang bukan anggota", "cek bagian etcd3.hosts di /etc/patroni/config.yml")
        aturan("keepalived.hari_h_vip", lambda v, n: "/32" in str(v), "VIP Hari-H tidak /32",
               "cek keepalived.conf.pelaksanaan")
        aturan("backup.nas_terpasang", lambda v, n: v != "TIDAK", "NAS backup tidak terpasang",
               "mount /mnt/nas  ; cek /etc/fstab", "crit" if cfg.prod_on is False else "warn")
        aturan("backup.cron_jadwal", lambda v, n: v not in ("-", ""), "cron backup tidak ada", "crontab -l -u postgres")

        # nama di initial-cluster harus = anggota etcd yang sebenarnya
        with self.lock:
            j = dict(self.jauh)
        nyata = None
        for v in (j.get("etcd") or {}).values():
            if isinstance(v, list):
                nyata = sorted(x.split("=", 1)[0] for x in v)
                break
        if nyata:
            aturan("etcd.default_initial_cluster", lambda v, n: v == "-" or sorted(str(v).split(", ")) == nyata,
                   "/etc/default/etcd memuat anggota etcd yang salah",
                   "Anggota sebenarnya: " + ", ".join(nyata) + ". Rapikan berkas (tanpa restart etcd).")
            aturan("etcd.default_initial_cluster", lambda v, n: str(v).startswith("-") or sorted(str(v).split(", ")) == nyata,
                   "/etc/default/etcd menyebut anggota yang bukan anggota etcd sebenarnya",
                   "Anggota sebenarnya: " + ", ".join(nyata) + ". Rapikan /etc/default/etcd (tanpa restart etcd).")
            def _nama_ok(v, n):
                token = str((segar.get(n) or {}).get("isi", {}).get("etcd.cluster_token", ""))
                return all(x in nyata for x in str(v).split(", ") if x and x not in ("-", token))
            aturan("etcd.default_nama_disebut", _nama_ok,
                   "/etc/default/etcd masih menyebut nama node lama", "grep -n ETCD_ /etc/default/etcd")
            aturan("etcd.initial_cluster", lambda v, n: sorted(str(v).split(", ")) == nyata,
                   "initial-cluster di berkas ≠ anggota etcd sebenarnya",
                   "Anggota sebenarnya: " + ", ".join(nyata) + ". Rapikan berkas (tanpa restart etcd).", "info")

        # --- cek dari luar
        jt = self._nilai_jauh(j, temuan)
        lvl = max((t["level"] for t in temuan), key=lambda x: {"ok": 0, "info": 1, "warn": 2, "crit": 3}[x], default="ok")
        if lap and lvl == "ok":
            temuan.append(F("ok", "Konfigurasi", f"Konfigurasi seragam ({len(segar)} node melapor)"))
        bk, bt = backup.nilai(cfg, lap, self.mon.state)
        temuan += bt
        if self.cekpenuh:
            temuan += self.cekpenuh.temuan()
        with self.lock:
            self.hasil = {"node": nodes, "baris": baris, "jauh": jt, "temuan": temuan, "waktu": sekarang,
                          "basi_menit": BASI // 60}
            self.backup = bk
        return self.hasil

    def _nilai_jauh(self, j, temuan):
        out = {"waktu": j.get("waktu")}
        # PostgreSQL
        pg = j.get("pg") or []
        ok = [x for x in pg if x.get("ok")]
        rows = []
        if len(ok) >= 2:
            for name in PG_SET:
                vals = {x["label"]: x["set"].get(name) for x in ok}
                cl = {k: v for k, v in vals.items() if k != "Produksi"}
                status, cat = "SAMA", ""
                if len(set(cl.values())) > 1:  # antar node cluster
                    status = "BEDA" if name in PG_HARUS_SAMA else "INFO"
                    cat = "pemimpin dan replica berbeda"
                elif "Produksi" in vals and vals["Produksi"] != next(iter(cl.values()), None):
                    pv, cv = vals["Produksi"], next(iter(cl.values()))
                    if name in PG_MIN_STANDBY:  # standby wajib >= produksi
                        try:
                            kurang = float(re.sub(r"[^0-9.]", "", cv) or 0) < float(re.sub(r"[^0-9.]", "", pv) or 0)
                        except ValueError:
                            kurang = True
                        status, cat = ("BEDA", "cluster LEBIH KECIL dari produksi: replikasi bisa berhenti") if kurang else \
                                      ("INFO", "cluster lebih besar dari produksi (boleh)")
                    elif name in PG_PROD_SAMA:
                        status, cat = "BEDA", "berbeda dengan produksi"
                    else:
                        status, cat = "INFO", ("produksi memakai stanza sendiri (wajar)" if name == "archive_command"
                                               else "berbeda dengan produksi (boleh, sesuai kapasitas)")
                rows.append({"nama": name, "nilai": vals, "status": status, "catatan": cat})
                if status == "BEDA":
                    temuan.append(F("warn", "Konfigurasi", f"PostgreSQL {name} berbeda: {cat}",
                                    "; ".join(f"{k} ({next((x['server'] for x in ok if x['label'] == k), '?')}): {v}" for k, v in vals.items()),
                                    "patronictl edit-config  → samakan, lalu restart bergilir bila Pending restart"))
        out["pg"] = {"kolom": [{"label": x["label"], "server": x.get("server"), "error": x.get("error"),
                                "recovery": x.get("recovery")} for x in pg], "baris": rows}
        # Patroni
        pv = j.get("patroni") or {}
        out["patroni"] = pv
        vs = {v.get("versi") for v in pv.values() if isinstance(v, dict) and v.get("versi")}
        if len(vs) > 1:
            temuan.append(F("warn", "Konfigurasi", "Versi Patroni berbeda antar node",
                            "; ".join(f"{n}: {v.get('versi')}" for n, v in pv.items())))
        pgv = {v.get("pg") for v in pv.values() if isinstance(v, dict) and v.get("pg")}
        if len(pgv) > 1:
            temuan.append(F("warn", "Konfigurasi", "Versi PostgreSQL berbeda antar node",
                            "; ".join(f"{n}: {v.get('pg')}" for n, v in pv.items())))
        # etcd
        em = j.get("etcd") or {}
        out["etcd"] = em
        daftar = {json.dumps(v) for v in em.values() if isinstance(v, list)}
        if len(daftar) > 1:
            temuan.append(F("warn", "Konfigurasi", "Daftar anggota etcd berbeda menurut tiap anggota",
                            "Ada anggota yang melihat cluster etcd berbeda.", "ETCDCTL_API=3 etcdctl member list -w table  (di tiap anggota)"))
        conf_names = sorted(n for n, _ in self.cfg.etcd_nodes)
        for v in em.values():
            if isinstance(v, list):
                nyata = sorted(x.split("=", 1)[0] for x in v)
                if nyata != conf_names:
                    lebih = sorted(set(nyata) - set(conf_names))
                    kurang = sorted(set(conf_names) - set(nyata))
                    temuan.append(F("warn", "Konfigurasi", "Anggota etcd tidak sesuai config helper",
                                    (f"Ada di etcd tapi tidak di config: {', '.join(lebih)}. " if lebih else "") +
                                    (f"Ada di config tapi bukan anggota etcd: {', '.join(kurang)}." if kurang else ""),
                                    "ETCDCTL_API=3 etcdctl member list -w table"))
                break
        # HAProxy
        hp = j.get("haproxy") or {}
        out["haproxy"] = hp
        harap = sorted(n for n, _ in self.cfg.db_nodes)
        for n, h in hp.items():
            for jalur in ("tulis", "baca"):
                if h[jalur] and h[jalur] != harap:
                    temuan.append(F("warn", "Konfigurasi", f"HAProxy {n}: daftar server jalur {jalur} ≠ node database",
                                    f"di HAProxy: {', '.join(h[jalur])}; node database: {', '.join(harap)}",
                                    "cek /etc/haproxy/haproxy.cfg di node itu"))
        return out

    # ------------------------------------------------------------------ jalan
    def loop(self):
        nxt = 0
        while True:
            try:
                if time.time() >= nxt:
                    self._cek_jauh()
                    nxt = time.time() + JARAK_JAUH_DETIK
                self.nilai()
            except Exception as e:  # noqa: BLE001
                self.mon.history.add("info", "Cek konfigurasi gagal satu putaran", f"{type(e).__name__}: {e}"[:200])
            time.sleep(15)

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()

    def temuan(self):
        with self.lock:
            return list(self.hasil.get("temuan", []))
