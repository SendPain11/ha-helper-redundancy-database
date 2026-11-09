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
