# HA Helper

Dashboard pemantau cluster PostgreSQL HA (Patroni + etcd + HAProxy + keepalived).
Berjalan di komputer admin, **hanya membaca**: tidak mengubah apa pun di cluster.

- Status cluster langsung: pemimpin, peran tiap node, lag, etcd, HAProxy, VIP
- Cek streaming & data: membandingkan data baru di server sumber dengan cluster
- Perekam uji failover: catatan per detik + laporan `.txt`
- Riwayat & peringatan: notifikasi browser dan email

Tanpa pustaka tambahan: cukup Python 3.8+.

```
cp config.contoh.ini config.ini     # lalu sesuaikan
python jalankan.py                  # Windows: klik dua kali jalankan.bat
python jalankan.py --cek            # cek sekali di layar
```

## Versi 2
- Login dua peran: **admin** (penuh) dan **lihat** (hanya melihat), diatur di `[akses]`
- Bisa dibuka dari komputer lain lewat `http://<IP>:8099`
- Windows: berjalan terus di latar (`simpan-sandi.bat`, lalu `pasang-jalan-terus.bat`),
  sandi disimpan terenkripsi (DPAPI)

## Versi 3
- Linux (mis. di VM witness) sebagai layanan systemd: `sudo bash linux/pasang.sh`
- Perintah singkat: `sudo ha-helper status | cek | log | config | sandi`
- Dibatasi CPU 25%, RAM 256 MB, prioritas disk terendah agar etcd tidak terganggu
