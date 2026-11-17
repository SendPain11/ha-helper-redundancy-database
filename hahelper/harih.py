"""Daftar periksa Hari-H. Helper hanya MENCATAT (siapa, kapan, catatan); semua perintah tetap dijalankan manusia
di node masing-masing. Isi langkah = dokumen Hari-H_Teknis_v2.txt."""
import json
import os
import threading
import time

# otomatis: indikator yang dinilai dashboard dari keadaan cluster saat itu (lihat index.html: HH_CEK)
# tanda: gangguan_mulai / gangguan_selesai (penghitung downtime), titik_kembali (setelah ini batal tidak sederhana)
RENCANA_VERSI = "v2-pemimpin-node1"
ETCD = ("ETCDCTL_API=3 etcdctl --endpoints=http://10.10.0.11:2379,http://10.10.0.12:2379,http://10.10.0.21:2379,"
        "http://10.10.0.22:2379,http://10.10.0.23:2379 endpoint health")
PATRONI = "patronictl -c /etc/patroni/config.yml list"
RENCANA = [
    {"id": "pra", "judul": "PRASYARAT (dicentang H-1)", "durasi": None, "dimana": "semua",
     "jalankan": "", "gagal": "Jangan menetapkan Hari-H sebelum semua tercentang.",
     "bukti": ["Gladi pindah Node 2 (vzdump + qmrestore uji VMID 214) berhasil; waktunya tercatat; VM 214 dihapus",
               "RAM Node 1 (VM 113) sudah 32 GB, kembali Replica Lag 0",
               "Malam H-1: cek isi seluruh tabel produksi vs cluster -> tanpa BEDA",
               "cek-semua-tabel.sh versi CEPAT terpasang di db-node2 (/usr/local/sbin)",
               "etcd 5/5 sehat; /etc/hosts sama di kelima node",
               "ufw db-node1 & db-node2 membuka SEMUA server aplikasi; ufw produksi mengizinkan 10.10.0.11",
               "keepalived.conf.pelaksanaan kedua node: 10.20.0.5/32, prioritas 100, chk_haproxy -50 + chk_leader +20, md5 SAMA",
               "/etc/patroni/no-failback ada di db-node2",
               "Backup produksi semalam selesai",
               "Tim aplikasi & tim jaringan siaga; pengumuman gangguan terkirim"],
     "otomatis": ["etcd5", "konfig", "cekpenuh", "backup_prod"]},
    # ------------------------------------------------------------------ TAHAP A
    {"id": "l1", "judul": "Langkah 1 - Cek akhir", "durasi": 10, "jam": "J+0:00", "tahap": "A - Persiapan (aplikasi masih berjalan)",
     "dimana": "db-node2",
     "jalankan": PATRONI + "\n" + ETCD + "\nls /etc/patroni/no-failback",
     "bukti": ["db-node2 Standby Leader, db-node1 Replica Lag 0", "etcd: lima alamat healthy",
               "berkas no-failback ada", "dashboard AMAN"],
     "gagal": "TUNDA Hari-H.", "otomatis": ["standby_ok", "lag0", "etcd5", "tanpa_pause", "aman"]},
    {"id": "l2", "judul": "Langkah 2 - Pindah pemimpin ke Node 1", "durasi": 5, "jam": "J+0:10",
     "dimana": "db-node2, db-lama, lalu db-node1 & db-node2", "pemimpin": "db-node1",
     "jalankan": "# di db-node2\n"
                 "patronictl -c /etc/patroni/config.yml switchover ha-cluster --leader db-node2 --candidate db-node1 --force\n"
                 "sleep 20; " + PATRONI + "\n"
                 "# di db-lama\n"
                 "sudo -u postgres psql -c \"SELECT client_addr, state FROM pg_stat_replication;\"\n"
                 "# di db-node1 dan db-node2\n"
                 "ip -4 addr | grep 10.10.0.10",
     "bukti": ["db-node1 Standby Leader, db-node2 Replica streaming Lag 0", "produksi: 10.10.0.11 streaming",
               "alamat latihan 10.10.0.10 pindah ke db-node1 (+/- 6 detik)"],
     "gagal": "TUNDA Hari-H (belum ada downtime).", "otomatis": ["pemimpin_rencana", "lag0", "vip_pemimpin"]},
    # ------------------------------------------------------------------ TAHAP B
    {"id": "l3", "judul": "Langkah 3 - Putus semua koneksi", "durasi": 2, "jam": "J+0:15",
     "tahap": "B - Pemindahan database (DOWNTIME)", "tanda": "gangguan_mulai",
     "dimana": "tim aplikasi, lalu db-lama",
     "jalankan": "# tim aplikasi: hentikan semua aplikasi\n"
                 "# di db-lama\n"
                 "sudo -u postgres psql -c \"ALTER SYSTEM SET default_transaction_read_only = on;\" -c \"SELECT pg_reload_conf();\" -c \"SHOW default_transaction_read_only;\"\n"
                 "sudo -u postgres psql -Atc \"SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid();\"\n"
                 "# bila tetap ada setelah 1 menit:\n"
                 "sudo -u postgres psql -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE backend_type='client backend' AND usename<>'replikator' AND pid<>pg_backend_pid();\"",
     "bukti": ["semua aplikasi berhenti", "read_only = on", "koneksi aplikasi = 0"],
     "gagal": "cari server aplikasi yang masih tersambung, matikan dulu.", "otomatis": ["prod_readonly"]},
    {"id": "l4", "judul": "Langkah 4 - Selisih nol + cek cepat", "durasi": 2, "jam": "J+0:17",
     "dimana": "db-lama, lalu db-node2",
     "jalankan": "# di db-lama\n"
                 "sudo -u postgres psql -c \"SELECT pg_switch_wal();\"\n"
                 "sleep 5; sudo -u postgres psql -c \"SELECT last_archived_wal, last_failed_wal FROM pg_stat_archiver;\"\n"
                 "sudo -u postgres psql -c \"SELECT client_addr, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS selisih FROM pg_stat_replication;\"\n"
                 "# di db-node2\n"
                 "sudo CEPAT=1 bash /usr/local/sbin/cek-semua-tabel.sh",
     "bukti": ["baris 10.10.0.11 selisih = 0", "KESIMPULAN CEPAT: Selisih NOL, Daftar tabel SAMA, Sequence OK"],
     "gagal": "TUNGGU sampai nol. JANGAN lanjut.", "otomatis": ["selisih0", "lag0", "prod_arsip"]},
    {"id": "l5", "judul": "Langkah 5 - Matikan database produksi", "durasi": 1, "jam": "J+0:19",
     "dimana": "db-lama lewat KONSOL / IP pengelolaan (BUKAN 10.20.0.5)",
     "jalankan": "systemctl stop postgresql\n"
                 "ip -4 addr show | grep 10.20.0.5          # catat <KARTU> dan <PREFIKS>\n"
                 "ip addr del 10.20.0.5/<PREFIKS> dev <KARTU>\n"
                 "nano /etc/network/interfaces                # beri tanda # pada alamat itu",
     "bukti": ["postgresql inactive", "10.20.0.5 tidak ada lagi di produksi"],
     "gagal": "JALAN MUNDUR A.", "otomatis": []},
    {"id": "l6", "judul": "Langkah 6 - Cluster jadi utama + uji tulis", "durasi": 2, "jam": "J+0:20",
     "dimana": "db-node1, lalu db-node2",
     "jalankan": "# di db-node1\n"
                 "cd /tmp && EDITOR=nano patronictl -c /etc/patroni/config.yml edit-config\n"
                 "#  -> hapus SELURUH blok standby_cluster: ; Ctrl+O, Enter, Ctrl+X, y\n"
                 + PATRONI + "\n"
                 "curl -s -o /dev/null -w '%{http_code}\\n' http://10.10.0.11:8008/primary\n"
                 "sudo -u postgres psql -p 5433 -d appdb -c \"CREATE TABLE uji_pelaksanaan(waktu timestamptz DEFAULT now());\" -c \"INSERT INTO uji_pelaksanaan DEFAULT VALUES;\"\n"
                 "# di db-node2\n"
                 "sudo -u postgres psql -p 5433 -d appdb -Atc \"SELECT count(*) FROM uji_pelaksanaan;\"\n"
                 "# kembali di db-node1\n"
                 "sudo -u postgres psql -p 5433 -d appdb -c \"DROP TABLE uji_pelaksanaan;\"",
     "bukti": ["db-node1 Leader (bukan Standby Leader), TL naik 1", "/primary = 200",
               "db-node2 Replica streaming; hitungan di db-node2 = 1", "tabel uji terhapus"],
     "catatan": "Arsip WAL gagal sampai Langkah 10 (stanza belum dibuat). WAJAR.",
     "gagal": "JALAN MUNDUR A.", "otomatis": ["leader_utama", "bisa_tulis", "replika_streaming"]},
    {"id": "l7", "judul": "Langkah 7 - VIP ke 10.20.0.5", "durasi": 1, "jam": "J+0:22", "tanda": "titik_kembali",
     "dimana": "db-node1, lalu db-node2",
     "jalankan": "cp /etc/keepalived/keepalived.conf.pelaksanaan /etc/keepalived/keepalived.conf && systemctl reload keepalived\n"
                 "sleep 8; ip -4 addr show | grep 10.20.0.5",
     "bukti": ["10.20.0.5/32 hanya di db-node1 (node pemimpin)", "ping 10.20.0.5 dari mesin lain dijawab"],
     "gagal": "JALAN MUNDUR B.", "otomatis": []},
    {"id": "l8", "judul": "Langkah 8 - Uji dari server aplikasi", "durasi": 2, "jam": "J+0:23",
     "dimana": "MESIN APLIKASI, satu dari tiap jaringan 10.30.0.x, 10.20.0.x, 10.40.0.x (pengguna aplikasi)",
     "jalankan": "nc -vz 10.20.0.5 5432\n"
                 "psql \"host=10.20.0.5 port=5432 user=<pengguna_aplikasi> dbname=appdb\" -c \"SELECT inet_server_addr(), pg_is_in_recovery();\" -c \"CREATE TEMP TABLE t(x int); INSERT INTO t VALUES (1);\"",
     "bukti": ["succeeded dari 10.30.0.x", "succeeded dari 10.20.0.x", "succeeded dari 10.40.0.x",
               "inet_server_addr = 10.10.0.11", "pg_is_in_recovery = f ; INSERT 0 1"],
     "gagal": "JANGAN nyalakan aplikasi. JALAN MUNDUR B.", "otomatis": []},
    {"id": "l9", "judul": "Langkah 9 - Nyalakan aplikasi", "durasi": 5, "jam": "J+0:25", "tanda": "gangguan_selesai",
     "dimana": "server aplikasi (tim aplikasi)",
     "jalankan": "nyalakan aplikasi UTAMA dulu (sesuai aba-aba)\nlogin, simpan 1 transaksi\nlayanan pendukung lain menyusul",
     "bukti": ["transaksi pertama tersimpan dan bisa dibuka lagi"],
     "catatan": "Downtime selesai saat langkah ini SELESAI. Sasaran J+0:30 = 15 menit.",
     "gagal": "", "otomatis": []},
    # ------------------------------------------------------------------ TAHAP C
    {"id": "l10", "judul": "Langkah 10 - Nyalakan backup cluster", "durasi": 5, "jam": "J+0:32",
     "tahap": "C - Pemindahan Node 2 (aplikasi berjalan, tanpa downtime)", "dimana": "db-node1",
     "jalankan": "sudo -u postgres pgbackrest --stanza=cluster stanza-create\n"
                 "sudo -u postgres pgbackrest --stanza=cluster check\n"
                 "sudo -u postgres psql -p 5433 -Atc \"SELECT archived_count, last_archived_wal, last_failed_wal FROM pg_stat_archiver\"",
     "bukti": ["stanza-create: completed successfully", "check: completed successfully", "archived_count > 0"],
     "catatan": "JANGAN pernah memakai stanza backup_lama di cluster.", "gagal": "", "otomatis": ["arsip_cluster"]},
    {"id": "l11", "judul": "Langkah 11 - Matikan Node 2 di host-A", "durasi": 2, "jam": "J+0:37",
     "dimana": "host host-A, lalu db-node1",
     "jalankan": "# di host-A\nqm shutdown 114 ; qm status 114\n# di db-node1\n" + PATRONI,
     "bukti": ["VM 114 stopped", "db-node1 Leader tetap melayani", "etcd 4/5 sehat"],
     "gagal": "", "otomatis": []},
    {"id": "l12", "judul": "Langkah 12 - Backup VM Node 2 ke NAS", "durasi": 28, "jam": "J+0:39", "dimana": "host host-A",
     "jalankan": "time vzdump 114 --mode stop --storage nas-backup --compress zstd --bwlimit 153600 --remove 0\n"
                 "ls -lh /mnt/pve/nas-backup/dump/ | grep qemu-114",
     "bukti": ["Finished Backup of VM 114", "berkas .vma.zst ada"],
     "catatan": "bwlimit 150 MB/detik: Node 1 di host yang sama sedang melayani.", "gagal": "", "otomatis": []},
    {"id": "l13", "judul": "Langkah 13 - Matikan VM produksi lama", "durasi": 5, "jam": "J+1:07",
     "dimana": "db-lama, lalu host host-B (host TIDAK di-reboot)",
     "jalankan": "# di db-lama (sebelum dimatikan)\n"
                 "crontab -l -u postgres | sed -E 's/^([^#].*stanza=backup_lama)/# \\1/' | crontab -u postgres -\n"
                 "systemctl disable postgresql\n"
                 "# di host host-B\n"
                 "qm set 126 --onboot 0 ; qm shutdown 126 ; qm status 126\n"
                 "free -g | head -2",
     "bukti": ["jadwal backup_lama diberi tanda #", "VM 126 stopped", "RAM bebas > 50 GB"],
     "gagal": "", "otomatis": []},
    {"id": "l14", "judul": "Langkah 14 - Pulihkan VM Node 2 di host tujuan", "durasi": 30, "jam": "J+1:12",
     "dimana": "host host-B",
     "jalankan": "F=$(ls -t /mnt/pve/nas-backup/dump/vzdump-qemu-114-*.vma.zst | head -1); echo \"$F\"\n"
                 "time qmrestore \"$F\" 114 --storage local-lvm\n"
                 "qm set 114 --onboot 1\n"
                 "qm config 114 | grep -E \"^(name|memory|net0|scsi0)\"",
     "bukti": ["VM 114 ada (stopped)", "net0 bridge=vmbr0", "VM 114 di host-A TETAP MATI"],
     "catatan": "Dua VM 114 hidup bersamaan = bentrok IP.", "gagal": "", "otomatis": []},
    {"id": "l15", "judul": "Langkah 15 - Hidupkan Node 2 + cek cepat", "durasi": 15, "jam": "J+1:42",
     "dimana": "host host-B, db-node1, db-node2 baru",
     "jalankan": "# di host host-B\nqm start 114\n"
                 "# di db-node1\n" + PATRONI + "\n" + ETCD + "\n"
                 "# di db-node2 baru\n"
                 "sudo CEPAT=1 PROD_HOST=pemimpin bash /usr/local/sbin/cek-semua-tabel.sh\n"
                 "ip -4 addr | grep 10.20.0.5 || echo \"alamat tidak di node ini (BENAR)\"",
     "bukti": ["db-node2 Replica streaming Lag 0", "etcd 5/5", "cek cepat OK", "10.20.0.5 tetap di db-node1"],
     "gagal": "Aplikasi tidak terganggu. Matikan VM 114 di host tujuan, cari sebabnya. Jangan nyalakan VM 114 lama selama yang baru hidup.",
     "otomatis": ["lag0", "etcd5", "replika_streaming"]},
    {"id": "l16", "judul": "Langkah 16 - Backup penuh pertama", "durasi": 60, "jam": "J+1:57", "dimana": "db-node1",
     "jalankan": "nohup sudo -u postgres pgbackrest --stanza=cluster --type=full backup >> /var/log/pgbackrest/full-pertama.log 2>&1 &\n"
                 "tail -3 /var/log/pgbackrest/full-pertama.log\n"
                 "sudo -u postgres pgbackrest --stanza=cluster info",
     "bukti": ["full backup PERTAMA cluster selesai (\"full backup: <tanggal>F\")"], "gagal": "", "otomatis": ["backup_cluster"]},
    {"id": "l17", "judul": "Langkah 17 - Penutup", "durasi": None, "jam": "J+2:57",
     "dimana": "VM Node Monitor, db-node2 baru",
     "jalankan": "# di VM Node Monitor\nsudo ha-helper config\n"
                 "#   [produksi] aktif = no\n#   [haproxy]  alamat_vip = 10.20.0.5\n#   [cluster]  alamat_haproxy = 10.20.0.5\n"
                 "sudo ha-helper sandi\n"
                 "# di db-node2 baru: verifikasi Node 1 vs Node 2 malam ini\n"
                 "sudo MULAI=02:00 LATAR=1 PROD_HOST=pemimpin bash /usr/local/sbin/cek-semua-tabel.sh",
     "bukti": ["dashboard mode sesudah, AMAN", "verifikasi malam dijadwalkan", "kabar ke PM: jam mulai/selesai downtime"],
     "gagal": "", "otomatis": ["mode_sesudah", "aman", "tulis_satu", "vip_pemimpin"]},
]

MUNDUR = [
    {"id": "A", "judul": "A. Gagal di Langkah 3-6 (alamat belum di cluster)",
     "isi": "(db-lama) bila Langkah 5 sudah dikerjakan, pasang lagi alamat:\n"
            "  ip addr add 10.20.0.5/<PREFIKS> dev <KARTU> ; hapus tanda # di interfaces\n"
            "  arping -U -c 3 -I <KARTU> 10.20.0.5\n"
            "  systemctl start postgresql\n"
            "  sudo -u postgres psql -c \"ALTER SYSTEM SET default_transaction_read_only = off;\" -c \"SELECT pg_reload_conf();\"\n"
            "Nyalakan aplikasi. TIDAK ADA DATA HILANG.\n"
            "Cluster disiapkan ulang sebagai standby di hari lain (jangan pasang standby_cluster saat itu)."},
    {"id": "B", "judul": "B. Gagal di Langkah 7-8 (alamat sudah di cluster, aplikasi BELUM menyala)",
     "isi": "systemctl stop keepalived          (di db-node1 DAN db-node2)\nlalu kerjakan A."},
    {"id": "C", "judul": "C. Setelah Langkah 9 (aplikasi sudah menulis ke cluster)",
     "isi": "Data baru sudah di cluster. Keputusan PM; utamakan perbaikan di tempat.\n"
            "Tahap C bermasalah: aplikasi aman di Node 1. VM 114 lama (host-A) dan VM 126 TIDAK dihapus sampai H+7."},
]

SESUDAH = [
    {"id": "h1a", "judul": "H+1  ganti sandi postgres, replikator, VRRP, statistik HAProxy"},
    {"id": "h1b", "judul": "H+1  hapus IP uji (10.20.0.100, 10.30.0.46, 10.30.0.42) dari ufw & pg_hba"},
    {"id": "h1c", "judul": "H+1  cabut jadwal cek semua tabel yang membandingkan produksi (bila dipasang): sudo bash pasang-jadwal-cek.sh hapus"},
    {"id": "h1d", "judul": "H+1  RAM Node 2 dinaikkan (qm set 114 --memory 32768, restart Node 2)"},
    {"id": "h17", "judul": "H+1..7  cek backup cluster tiap pagi (tab Backup: incr harian & full Minggu)"},
    {"id": "h27", "judul": "H+2..7  uji failover DENGAN menulis, direkam; alamat ikut pindah; buktikan backup ikut pindah (check + /primary=200)"},
    {"id": "h7", "judul": "H+7  uji restore dari stanza cluster ke VM uji (bukan ke cluster), ukur waktunya"},
    {"id": "h7b", "judul": "H+7  hapus VM 114 LAMA di host-A (qm destroy 114 hanya di host-A)"},
    {"id": "h7c", "judul": "H+7  bila Node 2 ingin jadi pemimpin lagi: rm /etc/patroni/no-failback di db-node2 (pindah hanya jam sepi)"},
    {"id": "h14", "judul": "H+14  setelah stanza cluster punya 2 full DAN uji restore berhasil: expire backup_lama jadi 1 rantai (VM 126 dinyalakan sementara)"},
    {"id": "s1", "judul": "stabil  hapus VM 126 (produksi lama) setelah disetujui PM"},
    {"id": "s2", "judul": "terjadwal  nyalakan data_checksums (jendela tersendiri)"},
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
        if self.st.get("rencana") != RENCANA_VERSI:
            if self.st.get("mulai") or self.st.get("langkah") or self.st.get("sesudah"):
                try:
                    os.replace(self.path, self.path.replace(".json", time.strftime("-rencana-lama-%Y%m%d-%H%M%S.json")))
                except OSError:
                    pass
                self.st = self._kosong()
            self.st["rencana"] = RENCANA_VERSI

    @staticmethod
    def _kosong():
        return {"mulai": None, "selesai": None, "langkah": {}, "sesudah": {}, "mundur": None, "log": [],
                "rencana": RENCANA_VERSI}

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
        tanda = {x.get("tanda"): x["id"] for x in RENCANA if x.get("tanda")}
        g0 = (L.get(tanda.get("gangguan_mulai")) or {}).get("mulai")
        g1 = (L.get(tanda.get("gangguan_selesai")) or {}).get("selesai")
        out = ["LAPORAN PELAKSANAAN - PEMINDAHAN DATABASE KE CLUSTER REDUNDANSI", "=" * 72,
               f"Mulai pelaksanaan : {f(st['mulai'])}", f"Selesai           : {f(st['selesai'])}",
               f"Gangguan layanan  : {fj(g0)} s/d {fj(g1)}" + (f"  ({(g1 - g0) / 60:.1f} menit)" if g0 and g1 else ""),
               f"Jalan mundur      : {('jalur ' + st['mundur']['jalur'] + ' pukul ' + fj(st['mundur']['t']) + ' oleh ' + st['mundur']['oleh']) if st.get('mundur') else 'tidak'}",
               "=" * 72, ""]
        for x in RENCANA:
            s = L.get(x["id"], {})
            dur = (s["selesai"] - s["mulai"]) / 60 if s.get("mulai") and s.get("selesai") else None
            out.append(f"{x['judul']}" + (f"   [rencana {x['jam']}]" if x.get("jam") else ""))
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
