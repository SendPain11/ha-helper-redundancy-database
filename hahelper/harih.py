"""Daftar periksa Hari-H. Helper hanya MENCATAT (siapa, kapan, catatan); semua perintah tetap dijalankan manusia
di node masing-masing. Isi langkah = dokumen 2_Hari-H_Pelaksanaan_Redundansi.txt."""
import json
import os
import threading
import time

# otomatis: indikator yang dinilai dashboard dari keadaan cluster saat itu (lihat index.html: HH_CEK)
RENCANA = [
    {"id": "pra", "judul": "PRASYARAT (dicentang H-1)", "durasi": None, "dimana": "semua",
     "jalankan": "", "gagal": "Jangan menetapkan Hari-H sebelum semua tercentang.",
     "bukti": ["etcd 5 anggota sehat: db-node1, db-node2, witness1-3 (db-lama sudah keluar)",
               "/etc/hosts sama di kelima node",
               "ufw db-node1 & db-node2 sudah membuka SEMUA server aplikasi (5432)",
               "db-node2 sudah tidak satu host dengan db-node1",
               "Pengguna aplikasi bisa login lewat HAProxy (pg_hba cluster)",
               "Koneksi puncak produksi di bawah maxconn HAProxy",
               "keepalived.conf.pelaksanaan di kedua node: 10.20.0.5/32, prioritas 100, chk_haproxy -50 + chk_leader +20, md5 sama",
               "Produksi bisa dimasuki lewat IP lain / konsol setelah 10.20.0.5 dilepas",
               "Data cocok (cek semua tabel tanpa BEDA)",
               "Uji perpindahan peran & mati mendadak sudah berhasil (laporan perekam)",
               "Backup produksi malam sebelumnya selesai dan berkasnya ada",
               "Backup cluster siap: pgBackRest + NAS di KEDUA node, stanza cluster sama, arsip WAL di Patroni, cron /primary",
               "db-node1 bisa menjangkau produksi 10.20.0.5:5432",
               "Skrip pengembalian dimatikan: /etc/patroni/no-failback ada di db-node2",
               "Tim aplikasi & tim jaringan siaga, pengumuman gangguan sudah dikirim"],
     "otomatis": ["etcd5", "konfig", "cekpenuh", "backup_prod"]},
    {"id": "l1", "judul": "Langkah 1 - Pemeriksaan akhir", "durasi": 10, "dimana": "db-node2, dan host fisik",
     "jalankan": "patronictl -c /etc/patroni/config.yml list\n"
                 "ETCDCTL_API=3 etcdctl --endpoints=http://10.10.0.11:2379,http://10.10.0.12:2379,http://10.10.0.21:2379,http://10.10.0.22:2379,http://10.10.0.23:2379 endpoint health\n"
                 "curl -s -o /dev/null -w '%{http_code}\\n' http://10.10.0.12:8008/leader\n"
                 "curl -s -o /dev/null -w '%{http_code}\\n' http://10.10.0.11:8008/replica\n"
                 "ls /etc/patroni/no-failback\n"
                 "ip -4 addr | grep 10.10.0.10                   # di db-node1 DAN db-node2\n"
                 "lvs -o lv_name,data_percent --units g            # di host fisik",
     "bukti": ["dua anggota running: db-node2 Standby Leader, db-node1 Replica, Lag 0",
               "tidak ada tulisan \"Maintenance mode\"", "etcd: lima alamat \"is healthy\"",
               "/leader = 200 ; /replica = 200 ; berkas no-failback ada",
               "alamat latihan 10.10.0.10 hanya di db-node2 (node pemimpin)", "kapasitas disk host di bawah 78 %",
               "dashboard: AMAN"],
     "gagal": "PELAKSANAAN DITUNDA. Jangan lanjut dengan anggota tidak sehat.",
     "otomatis": ["standby_ok", "lag0", "etcd5", "tanpa_pause", "vip_pemimpin", "aman"]},
    {"id": "l2", "judul": "Langkah 2 - Hentikan aplikasi", "durasi": 2, "dimana": "semua server aplikasi (tim aplikasi)",
     "tanda": "gangguan_mulai", "jalankan": "", "bukti": ["semua layanan aplikasi berhenti"],
     "gagal": "", "otomatis": []},
    {"id": "l3", "judul": "Langkah 3 - Kunci produksi", "durasi": 1, "dimana": "db-lama",
     "jalankan": "sudo -u postgres psql -c \"ALTER SYSTEM SET default_transaction_read_only = on;\" -c \"SELECT pg_reload_conf();\" -c \"SHOW default_transaction_read_only;\"\n"
                 "sudo -u postgres psql -Atc \"SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND state='active' AND pid<>pg_backend_pid();\"\n"
                 "# bila tetap ada (aplikasi lupa dimatikan):\n"
                 "sudo -u postgres psql -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE backend_type='client backend' AND usename<>'replikator' AND pid<>pg_backend_pid();\"\n"
                 "# tutup backup lama sampai detik ini:\n"
                 "sudo -u postgres psql -c \"SELECT pg_switch_wal();\"\n"
                 "sleep 5; sudo -u postgres psql -c \"SELECT last_archived_wal, last_failed_wal FROM pg_stat_archiver;\"",
     "bukti": ["SHOW default_transaction_read_only = on", "koneksi aktif = 0 (selain replikasi replikator)",
               "last_archived_wal = berkas hasil pg_switch_wal ; last_failed_wal kosong"],
     "gagal": "cari server aplikasi yang masih tersambung, matikan dulu.", "otomatis": ["prod_readonly", "prod_arsip"]},
    {"id": "l4", "judul": "Langkah 4 - Pastikan selisih data nol", "durasi": 2, "dimana": "db-lama, lalu db-node2",
     "jalankan": "# di db-lama\n"
                 "sudo -u postgres psql -c \"SELECT client_addr, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS selisih FROM pg_stat_replication;\"\n"
                 "# di db-node2\n"
                 "patronictl -c /etc/patroni/config.yml list",
     "bukti": ["baris 10.10.0.12 selisih = 0", "diulang setelah 30 detik, tetap 0", "db-node1 Lag in MB = 0"],
     "gagal": "TUNGGU sampai nol. Angka selain nol = ada data produksi yang belum sampai ke cluster. JANGAN lanjut.",
     "otomatis": ["selisih0", "lag0"]},
    {"id": "l5", "judul": "Langkah 5 - Naikkan cluster menjadi utama", "durasi": 2, "dimana": "db-node2",
     "jalankan": "cd /tmp && EDITOR=nano patronictl -c /etc/patroni/config.yml edit-config\n"
                 "#  -> hapus SELURUH blok  standby_cluster:  beserta baris di bawahnya\n"
                 "#  -> simpan: Ctrl+O, Enter ; keluar: Ctrl+X ; jawab: y\n"
                 "patronictl -c /etc/patroni/config.yml show-config | grep -c standby_cluster\n"
                 "patronictl -c /etc/patroni/config.yml list\n"
                 "curl -s -o /dev/null -w '%{http_code}\\n' http://10.10.0.12:8008/primary",
     "bukti": ["hitungan standby_cluster = 0", "db-node2 Role = Leader (bukan Standby Leader), TL naik satu angka",
               "db-node1 Replica, streaming, Lag 0", "/primary = 200"],
     "catatan": "Sejak detik ini arsip WAL ke stanza cluster gagal sampai Langkah 6B. WAJAR.",
     "gagal": "JALAN MUNDUR A. Jangan memasang ulang blok standby_cluster.", "otomatis": ["leader_utama", "lag0"]},
    {"id": "l6", "judul": "Langkah 6 - Buktikan cluster bisa menulis", "durasi": 2, "dimana": "db-node2, lalu db-node1",
     "jalankan": "# di db-node2\n"
                 "sudo -u postgres psql -p 5433 -d appdb -c \"CREATE TABLE uji_pelaksanaan(waktu timestamptz DEFAULT now());\" -c \"INSERT INTO uji_pelaksanaan DEFAULT VALUES;\"\n"
                 "# di db-node1\n"
                 "sudo -u postgres psql -p 5433 -d appdb -Atc \"SELECT count(*) FROM uji_pelaksanaan;\"\n"
                 "# kembali di db-node2\n"
                 "sudo -u postgres psql -p 5433 -d appdb -c \"DROP TABLE uji_pelaksanaan;\"",
     "bukti": ["tabel uji dibuat dan diisi di db-node2", "db-node1 ikut melihatnya: hitungan = 1", "tabel uji terhapus kembali"],
     "gagal": "JALAN MUNDUR A.", "otomatis": ["bisa_tulis", "replika_streaming"]},
    {"id": "l6b", "judul": "Langkah 6B - Nyalakan backup cluster (stanza cluster)", "durasi": 2, "dimana": "db-node2 (pemimpin)",
     "jalankan": "sudo -u postgres pgbackrest --stanza=cluster stanza-create\n"
                 "sudo -u postgres pgbackrest --stanza=cluster check\n"
                 "sudo -u postgres psql -p 5433 -Atc \"SELECT archived_count, last_archived_wal, last_failed_wal FROM pg_stat_archiver\"",
     "bukti": ["stanza-create: \"completed successfully\"", "check: \"completed successfully\"",
               "archived_count > 0 ; last_archived_wal berawalan timeline baru"],
     "gagal": "Lanjutkan pelaksanaan, catat pesannya; harus beres HARI ITU JUGA (WAL menumpuk di disk). JANGAN pakai stanza backup_lama.",
     "otomatis": ["arsip_cluster"]},
    {"id": "l7", "judul": "Langkah 7 - Lepas alamat dari produksi", "durasi": 2,
     "dimana": "db-lama lewat KONSOL / IP pengelolaan (BUKAN lewat 10.20.0.5)",
     "jalankan": "ip -4 addr show | grep 10.20.0.5          # catat <KARTU> dan <PREFIKS>\n"
                 "ip addr del 10.20.0.5/<PREFIKS> dev <KARTU>\n"
                 "nano /etc/network/interfaces                # beri tanda # pada alamat itu",
     "bukti": ["ip -4 addr show tidak lagi memuat 10.20.0.5", "produksi masih bisa dimasuki lewat konsol / IP pengelolaan"],
     "gagal": "JALAN MUNDUR A.", "otomatis": []},
    {"id": "l8", "judul": "Langkah 8 - Pasang alamat di cluster", "durasi": 2, "dimana": "db-node1, lalu db-node2",
     "tanda": "titik_kembali",
     "jalankan": "cp /etc/keepalived/keepalived.conf.pelaksanaan /etc/keepalived/keepalived.conf && systemctl reload keepalived\n"
                 "sleep 5; ip -4 addr show | grep 10.20.0.5",
     "bukti": ["10.20.0.5/32 muncul di SATU node saja: db-node2 (node pemimpin), +/- 6 detik setelah reload",
               "tertulis /32, bukan /24", "ping 10.20.0.5 dari mesin lain dijawab"],
     "catatan": "Alamat selalu mengikuti node pemimpin. Bila HAProxy di node pemimpin mati, alamat pindah ke node lain dan HAProxy di sana tetap meneruskan ke pemimpin.",
     "gagal": "JALAN MUNDUR B.", "otomatis": []},
    {"id": "l9", "judul": "Langkah 9 - Uji dari server aplikasi", "durasi": 5,
     "dimana": "MESIN APLIKASI, minimal satu dari tiap jaringan: 10.30.0.x, 10.20.0.x, 10.40.0.x",
     "jalankan": "nc -vz 10.20.0.5 5432\n"
                 "psql \"host=10.20.0.5 port=5432 user=<pengguna_aplikasi> dbname=appdb\" -c \"SELECT inet_server_addr(), pg_is_in_recovery(), now();\" -c \"CREATE TEMP TABLE t(x int); INSERT INTO t VALUES (1);\"",
     "bukti": ["succeeded dari jaringan 10.30.0.x", "succeeded dari jaringan 10.20.0.x", "succeeded dari jaringan 10.40.0.x",
               "inet_server_addr = 10.10.0.12 (pemimpin)", "pg_is_in_recovery = f ; INSERT 0 1"],
     "gagal": "JANGAN nyalakan aplikasi. JALAN MUNDUR B. Uji dari dalam node TIDAK membuktikan apa pun.", "otomatis": []},
    {"id": "l10", "judul": "Langkah 10 - Nyalakan aplikasi", "durasi": 10, "dimana": "server aplikasi (tim aplikasi)",
     "tanda": "gangguan_selesai",
     "jalankan": "nyalakan layanan satu per satu, jangan serentak\ncoba login, buka data, simpan SATU transaksi",
     "bukti": ["transaksi tersimpan dan bisa dibuka lagi"], "gagal": "", "otomatis": []},
    {"id": "l11", "judul": "Langkah 11 - Pantau satu jam", "durasi": 60, "dimana": "server dashboard dan db-node2",
     "jalankan": "# di server dashboard (sekali):  sudo ha-helper config\n"
                 "#   [produksi] aktif = no\n#   [haproxy]  alamat_vip = 10.20.0.5\n#   [cluster]  alamat_haproxy = 10.20.0.5\n"
                 "# lalu: sudo ha-helper sandi\n"
                 "# di db-node2:\n"
                 "watch -n10 \"sudo -u postgres psql -p 5433 -Atc \\\"SELECT client_addr, count(*) FROM pg_stat_activity WHERE client_addr IS NOT NULL GROUP BY 1 ORDER BY 1;\\\"\"",
     "bukti": ["semua server aplikasi muncul (sama dengan daftar klien produksi)", "dashboard AMAN, Lag 0",
               "di pgsql_tulis hanya pemimpin yang hijau", "tidak ada pesan \"no server available\" berulang",
               "tidak ada keluhan pengguna"],
     "gagal": "", "otomatis": ["mode_sesudah", "aman", "lag0", "tulis_satu", "vip_pemimpin"]},
    {"id": "l12", "judul": "Langkah 12 - Penutup: backup baru + skrip pengembalian", "durasi": 15, "dimana": "db-node2, db-lama",
     "jalankan": "nohup sudo -u postgres pgbackrest --stanza=cluster --type=full backup >> /var/log/pgbackrest/full-pertama.log 2>&1 &\n"
                 "tail -3 /var/log/pgbackrest/full-pertama.log\n"
                 "sudo -u postgres pgbackrest --stanza=cluster info\n"
                 "# SETELAH full cluster selesai, di db-lama:\n"
                 "crontab -l -u postgres | sed -E 's/^([^#].*stanza=backup_lama)/# \\1/' | crontab -u postgres -\n"
                 "crontab -l -u postgres | grep backup_lama\n"
                 "# di db-node2:\n"
                 "# nyalakan lagi skrip pengembalian pemimpin (bila ada) agar hanya jalan di jam sepi\n"
                 "rm /etc/patroni/no-failback",
     "bukti": ["full backup PERTAMA cluster selesai (\"full backup: <tanggal>F\")",
               "jadwal backup_lama di produksi diberi tanda # (isi backup lama TIDAK dihapus)",
               "skrip pengembalian dinyalakan lagi (jam sepi) dan no-failback dihapus",
               "kabar resmi ke PM: jam mulai & selesai gangguan"],
     "gagal": "", "otomatis": ["backup_cluster"]},
]

MUNDUR = [
    {"id": "A", "judul": "A. Gagal di Langkah 1-7 (alamat belum dipasang di cluster)",
     "isi": "Bila Langkah 7 sudah dikerjakan, pasang kembali alamat di db-lama:\n"
            "  ip addr add 10.20.0.5/<PREFIKS> dev <KARTU> ; hapus tanda # di interfaces\n"
            "  arping -U -c 3 -I <KARTU> 10.20.0.5\n"
            "Lalu di db-lama:\n"
            "  sudo -u postgres psql -c \"ALTER SYSTEM SET default_transaction_read_only = off;\" -c \"SELECT pg_reload_conf();\"\n"
            "Nyalakan aplikasi. Produksi utuh, TIDAK ADA DATA HILANG.\n"
            "Bila cluster sudah dinaikkan (Langkah 5), cluster dibiarkan dan disiapkan ulang sebagai standby di hari lain."},
    {"id": "B", "judul": "B. Gagal di Langkah 8-9 (alamat sudah di cluster, aplikasi BELUM menyala)",
     "isi": "systemctl stop keepalived          (di db-node1 DAN db-node2)\nlalu kerjakan A. Tidak ada data yang perlu disalin balik."},
    {"id": "C", "judul": "C. Setelah Langkah 10 (aplikasi sudah menulis ke cluster)",
     "isi": "Kembali ke produksi berarti data sejak Langkah 10 harus disalin balik manual. Keputusan PM. "
            "Utamakan memperbaiki di tempat."},
]

SESUDAH = [
    {"id": "h1a", "judul": "H+1  ganti sandi postgres, replikator, VRRP, statistik HAProxy"},
    {"id": "h1b", "judul": "H+1  hapus IP uji (10.20.0.100, 10.30.0.46, 10.30.0.42) dari ufw & pg_hba"},
    {"id": "h17", "judul": "H+1..7  cek backup cluster tiap pagi (tab Backup: incr harian & full Minggu)"},
    {"id": "h7", "judul": "H+7  uji restore dari stanza cluster ke laptop/VM uji (bukan ke cluster), ukur waktunya"},
    {"id": "h27", "judul": "H+2..7  uji failover DENGAN menulis, direkam; alamat ikut pindah ke pemimpin baru; buktikan backup ikut pindah (check + /primary=200)"},
    {"id": "h14", "judul": "H+14  setelah stanza cluster punya 2 full DAN uji restore berhasil: expire backup_lama jadi 1 rantai"},
    {"id": "s1", "judul": "stabil  matikan VM produksi lama: qm shutdown (BUKAN qm destroy)"},
    {"id": "s2", "judul": "stabil  hapus VM db-lama dan VM lama db-node2 di host-fisik-A"},
    {"id": "s3", "judul": "terjadwal  naikkan RAM db-node1 ; nyalakan data_checksums (jendela tersendiri)"},
]

STATUS = ("belum", "berjalan", "selesai", "gagal", "lewati")


class HariH:
    def __init__(self, datadir, history):
        self.path = os.path.join(datadir, "harih.json")
        self.history = history
        self.lock = threading.Lock()
        try:
            with open(self.path, encoding="utf-8") as f:
                self.st = json.load(f)
        except (OSError, ValueError):
            self.st = self._kosong()

    @staticmethod
    def _kosong():
        return {"mulai": None, "selesai": None, "langkah": {}, "sesudah": {}, "mundur": None, "log": []}

    def _simpan(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False)
        os.replace(self.path + ".tmp", self.path)

    def _log(self, siapa, teks, level="info", riwayat=True):
        self.st["log"].append({"t": time.time(), "oleh": siapa, "teks": teks})
        self.st["log"] = self.st["log"][-500:]
        if riwayat:
            self.history.add(level, "Hari-H: " + teks, f"oleh {siapa}")

    def _abadikan(self):
        """Salin kejadian dashboard selama pelaksanaan ke harih.json, supaya laporan tetap lengkap
        walau riwayat dashboard (maks 2000 kejadian) lama-lama tergeser."""
        t0 = (self.st["mulai"] or time.time()) - 600
        with self.history.lock:
            baru = [dict(e) for e in self.history.items if e["time"] >= t0]
        lama = {(e["id"], e["title"]): e for e in self.st.get("kejadian", [])}
        lama.update({(e["id"], e["title"]): e for e in baru})
        self.st["kejadian"] = sorted(lama.values(), key=lambda e: e["time"])[-1000:]

    def data(self):
        with self.lock:
            return {"rencana": RENCANA, "mundur": MUNDUR, "sesudah_daftar": SESUDAH, "status": self.st, "sekarang": time.time()}

    def aksi(self, b, siapa):
        a, now = b.get("aksi"), time.time()
        lid = str(b.get("langkah", ""))[:10]
        judul = next((x["judul"] for x in RENCANA if x["id"] == lid), None)
        with self.lock:
            if a == "mulai_harih":
                if not self.st["mulai"]:
                    self.st["mulai"] = now
                    self._log(siapa, "PELAKSANAAN DIMULAI", "warn")
            elif a == "selesai_harih":
                self.st["selesai"] = now
                self._log(siapa, "PELAKSANAAN SELESAI")
                self._abadikan()
            elif a == "reset":
                arsip = self.path.replace(".json", time.strftime("-%Y%m%d-%H%M%S.json"))
                if self.st["mulai"] or self.st["langkah"]:
                    self._simpan()
                    os.replace(self.path, arsip)
                self.st = self._kosong()
                self._log(siapa, "daftar periksa dikosongkan (salinan lama disimpan)")
            elif a == "status" and judul:
                s = b.get("status")
                if s not in STATUS:
                    return 400, {"error": "status tidak dikenal"}
                L = self.st["langkah"].setdefault(lid, {"bukti": {}})
                L["status"], L["oleh"] = s, siapa
                if s == "berjalan":
                    L["mulai"] = now
                    L.pop("selesai", None)
                    if not self.st["mulai"] and lid != "pra":
                        self.st["mulai"] = now
                        self._log(siapa, "PELAKSANAAN DIMULAI", "warn")
                elif s in ("selesai", "gagal", "lewati"):
                    L.setdefault("mulai", now)
                    L["selesai"] = now
                elif s == "belum":
                    L.pop("mulai", None)
                    L.pop("selesai", None)
                lvl = "crit" if s == "gagal" else "info"
                self._log(siapa, f"{judul}: {s.upper()}", lvl)
                if self.st["mulai"]:
                    self._abadikan()
            elif a == "bukti" and judul:
                L = self.st["langkah"].setdefault(lid, {"bukti": {}})
                i = str(int(b.get("no", -1)))
                if b.get("ok"):
                    L["bukti"][i] = {"t": now, "oleh": siapa}
                else:
                    L["bukti"].pop(i, None)
                self._log(siapa, f"{judul}: bukti #{int(i) + 1} {'dicentang' if b.get('ok') else 'dibatalkan'}", riwayat=False)
            elif a == "catatan" and judul:
                L = self.st["langkah"].setdefault(lid, {"bukti": {}})
                L["catatan"] = str(b.get("teks", ""))[:1000]
                self._log(siapa, f"{judul}: catatan diperbarui", riwayat=False)
            elif a == "mundur":
                m = str(b.get("jalur", ""))[:2]
                self.st["mundur"] = {"jalur": m, "t": now, "oleh": siapa} if m else None
                self._log(siapa, f"JALAN MUNDUR {m} diputuskan" if m else "jalan mundur dibatalkan", "crit" if m else "info")
            elif a == "sesudah":
                k = str(b.get("id", ""))[:10]
                if b.get("ok"):
                    self.st["sesudah"][k] = {"t": now, "oleh": siapa}
                else:
                    self.st["sesudah"].pop(k, None)
                self._log(siapa, f"tindak lanjut {k} {'selesai' if b.get('ok') else 'dibatalkan'}", riwayat=False)
            else:
                return 400, {"error": "aksi tidak dikenal"}
            self._simpan()
        return 200, {"ok": True}

    # ---------------------------------------------------------------- laporan teks untuk PM
    def laporan(self, history_items):
        st = self.st
        f = lambda t: time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(t)) if t else "-"  # noqa: E731
        fj = lambda t: time.strftime("%H:%M:%S", time.localtime(t)) if t else "-"  # noqa: E731
        L = st["langkah"]
        g0 = (L.get("l2") or {}).get("mulai")
        g1 = (L.get("l10") or {}).get("selesai")
        out = ["LAPORAN PELAKSANAAN - PEMINDAHAN DATABASE KE CLUSTER REDUNDANSI", "=" * 72,
               f"Mulai pelaksanaan : {f(st['mulai'])}", f"Selesai           : {f(st['selesai'])}",
               f"Gangguan layanan  : {fj(g0)} s/d {fj(g1)}" + (f"  ({(g1 - g0) / 60:.1f} menit)" if g0 and g1 else ""),
               f"Jalan mundur      : {('jalur ' + st['mundur']['jalur'] + ' pukul ' + fj(st['mundur']['t']) + ' oleh ' + st['mundur']['oleh']) if st.get('mundur') else 'tidak'}",
               "=" * 72, ""]
        for x in RENCANA:
            s = L.get(x["id"], {})
            dur = (s["selesai"] - s["mulai"]) / 60 if s.get("mulai") and s.get("selesai") else None
            out.append(f"{x['judul']}")
            out.append(f"   status  : {s.get('status', 'belum').upper()}   mulai {fj(s.get('mulai'))}   selesai {fj(s.get('selesai'))}"
                       + (f"   ({dur:.1f} mnt, rencana {x['durasi']} mnt)" if dur is not None and x.get("durasi") else "")
                       + (f"   oleh {s['oleh']}" if s.get("oleh") else ""))
            for i, bk in enumerate(x["bukti"]):
                c = (s.get("bukti") or {}).get(str(i))
                out.append(f"   [{'x' if c else ' '}] {bk}" + (f"   ({fj(c['t'])} {c['oleh']})" if c else ""))
            if s.get("catatan"):
                out.append("   catatan : " + s["catatan"].replace("\n", "\n             "))
            out.append("")
        out += ["TINDAK LANJUT SESUDAH PELAKSANAAN", "-" * 72]
        for x in SESUDAH:
            c = st["sesudah"].get(x["id"])
            out.append(f"   [{'x' if c else ' '}] {x['judul']}" + (f"   ({f(c['t'])} {c['oleh']})" if c else ""))
        out += ["", "KEJADIAN DI DASHBOARD SELAMA PELAKSANAAN", "-" * 72]
        t0 = st["mulai"] or 0
        t1 = (st["selesai"] or time.time()) + 3600
        semua = {(e["id"], e["title"]): e for e in st.get("kejadian", [])}
        semua.update({(e["id"], e["title"]): e for e in history_items})
        for e in sorted(semua.values(), key=lambda e: e["time"]):
            if t0 and t0 - 600 <= e["time"] <= t1:
                out.append(f"   {fj(e['time'])}  [{e['level'].upper():5}] {e['title']}" + (f" - {e['detail']}" if e.get("detail") else ""))
        out += ["", "CATATAN PENCENTANG", "-" * 72]
        for e in st["log"]:
            out.append(f"   {f(e['t'])}  {e['oleh']}: {e['teks']}")
        return "\n".join(out) + "\n"
