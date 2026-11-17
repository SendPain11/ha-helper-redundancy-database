# Riwayat perubahan

## 4.1.0
- `cek-semua-tabel.sh`: mode **CEK CEPAT** (`CEPAT=1`, kurang dari 1 menit) untuk dipakai di dalam
  downtime: selisih posisi replikasi, daftar tabel, sequence; kesimpulan "boleh lanjut / jangan lanjut"
- `cek-semua-tabel.sh`: `PROD_HOST=pemimpin` untuk sesudah cutover - acuan = pemimpin cluster
  (dicari lewat patronictl), dibandingkan dengan semua replica
- `cek-semua-tabel.sh`: sandi dari `.pgpass` bila tidak ada layar (cron); cek sequence & large object
- `linux/pasang-jadwal-cek.sh`: jadwalkan cek semua tabel lewat cron (sekali atau berkala), dicabut
  dengan `hapus`
- Daftar periksa Hari-H versi 2: tahap A (persiapan, pindah pemimpin), B (downtime), C (pindah VM
  node kedua ke host lain tanpa downtime); jam rencana per langkah; penanda awal/akhir downtime
  diatur dari rencana; catatan rencana lama diarsipkan otomatis
- Pelapor: nama anggota di `/etc/default/etcd` dibaca dari `nama=http://` dan `ETCD_NAME`

## 4.0.0
- Tab **Konfigurasi Node**: pelapor di tiap node (cron 5 menit, hanya membaca, tanpa sandi)
  membandingkan hosts, etcd (termasuk `/etc/default/etcd` dan sumber konfigurasi), Patroni,
  HAProxy, keepalived, pgBackRest, cron, NAS, ufw, NTP; pengaturan PostgreSQL pemimpin vs
  replica vs server sumber; daftar anggota etcd menurut tiap anggota
- Tab **Backup**: ringkasan `pgbackrest info`, umur backup, isi NAS, log cron, arsip WAL;
  nama stanza diatur di `[backup]`
- Tab **Cek Semua Tabel** + `linux/cek-semua-tabel.sh`: jumlah dan isi seluruh tabel,
  bisa dijadwalkan (`MULAI=01:00 LATAR=1`), kemajuan dikirim ke dashboard
- Tab **Hari-H**: daftar periksa cutover dengan catatan jam/nama, indikator otomatis,
  laporan unduhan; tetap lengkap walau riwayat dashboard sudah tergeser
- Mode setelah cutover: cek pemimpin bisa menulis, replikasi tiap node, arsip WAL
- Peringatan etcd tidak sehat hanya bila bertahan 2 putaran; kedipan sesaat dicatat sebagai info;
  perpindahan pemimpin etcd dicatat
- VIP dianjurkan mengikuti node pemimpin (keepalived `chk_leader`); dashboard memberi tanda bila tidak
- Logo sendiri dan ikon tab yang berubah warna sesuai status (beda dari halaman statistik HAProxy)

## 3.1.0
- Dokumentasi lengkap, `config.contoh.ini`, `.gitignore`
- Pesan petunjuk bila `config.ini` belum ada
- Perbaikan: mode jalan terus di Windows (Task Scheduler) berhenti saat mulai

## 3.0.0
- Versi Linux: layanan systemd dengan pengguna khusus `hahelper`
- Batas sumber daya (CPU 25%, RAM 256 MB, IO idle) agar aman dipasang di witness etcd
- Perintah `ha-helper` (status, cek, log, config, sandi, ulang, hidup, mati)
- Pembaruan versi tanpa kehilangan config, sandi, riwayat, dan laporan

## 2.0.0
- Login: peran admin dan lihat, sesi cookie, kunci 5 menit setelah 5 kali salah
- Akses dari komputer lain (`alamat_dashboard = 0.0.0.0`)
- Windows: jalan terus di latar lewat Task Scheduler, hidup lagi bila berhenti
- Sandi disimpan terenkripsi (DPAPI) dan diuji sambungannya saat disimpan

## 1.0.0
- Dashboard lokal: status cluster, cek streaming & data, perekam uji failover,
  riwayat & peringatan (browser + email)
- Klien PostgreSQL sendiri (SCRAM-SHA-256), tanpa pustaka tambahan
