# Riwayat perubahan

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
