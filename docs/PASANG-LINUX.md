# Pasang di Linux (mis. VM witness)

Dashboard berjalan 24 jam sebagai layanan systemd dengan pengguna khusus
`hahelper`. Sumber daya dibatasi (CPU 25%, RAM 256 MB, prioritas disk terendah)
supaya etcd di server yang sama tidak terganggu.

> **Pilih server yang tidak satu host fisik dengan node database.** Bila host
> itu mati, dashboard ikut mati tepat saat paling dibutuhkan.

## 1. Cek server

```bash
python3 --version                  # 3.8 atau lebih baru
free -m ; df -h /opt               # sisa RAM > 300 MB, disk > 500 MB
timeout 3 bash -c '</dev/tcp/<IP-NODE-DB>/8008' && echo 8008-OK
timeout 3 bash -c '</dev/tcp/<IP-NODE-DB>/7000' && echo 7000-OK
timeout 3 bash -c '</dev/tcp/<IP-HAPROXY>/5432' && echo 5432-OK
timeout 3 bash -c '</dev/tcp/<IP-HAPROXY>/5434' && echo 5434-OK
timeout 3 bash -c '</dev/tcp/<IP-SUMBER>/5432' && echo SUMBER-OK   # bila ada server sumber
```

Bila tidak keluar `OK`:
- **kode 124 (diam)**: diblokir firewall di server tujuan atau di jaringan.
  Cek dengan `tcpdump` di server tujuan apakah paket sampai.
- Di node database (ufw), contoh:
  `sudo ufw allow from <IP-DASHBOARD> to any port 8008,7000,5432,5434 proto tcp comment 'HA Helper'`
- `pg_hba.conf` server sumber harus mengizinkan IP dashboard (lalu
  `SELECT pg_reload_conf();`, tanpa restart).

## 2. Pasang

```bash
git clone https://github.com/<akun>/ha-helper.git    # atau salin & ekstrak zip
cd ha-helper
sudo bash linux/pasang.sh
sudo ufw allow from <IP-PC-ADMIN> to any port 8099 proto tcp comment 'HA Helper dashboard'
sudo ha-helper config      # isi alamat cluster & sandi login di [akses]
sudo ha-helper sandi       # simpan sandi HAProxy/PostgreSQL/email + uji sambungan
sudo ha-helper hidup       # nyalakan & jalan otomatis saat server menyala
```

Buka `http://<IP-SERVER>:8099`.

## Perintah sehari-hari

| Perintah | Fungsi |
|---|---|
| `sudo ha-helper status` | layanan berjalan atau tidak |
| `sudo ha-helper cek` | cek cluster sekali di layar |
| `sudo ha-helper log` | catatan terakhir (`log 200` untuk 200 baris) |
| `sudo ha-helper config` | ubah config.ini lalu mulai ulang |
| `sudo ha-helper sandi` | ganti sandi lalu mulai ulang |
| `sudo ha-helper ulang` / `hidup` / `mati` | mulai ulang / nyalakan / matikan |

## Pelapor konfigurasi di node (opsional, disarankan)

Untuk tab **Konfigurasi Node** dan **Backup**. Di server dashboard: `sudo ha-helper token`.
Lalu di setiap node cluster (database & witness):

```bash
sudo bash linux/pasang-lapor.sh http://<IP-dashboard>:8099 <token>
```

Pelapor hanya membaca dan mengirim ringkasan (versi, nilai penting, sidik md5 berkas) tiap
5 menit. Node harus bisa menjangkau porta 8099 server dashboard.
Copot: `sudo bash linux/pasang-lapor.sh hapus`.

## Cek semua tabel

Di node database yang sudah terpasang pelapor (hasil ikut tampil di dashboard):

```bash
MULAI=01:00 LATAR=1 sudo bash linux/cek-semua-tabel.sh
```

Membaca seluruh isi database di server sumber dan tiap node: jalankan di jam sepi.

Cek cepat (kurang dari 1 menit, aman di jam kerja / di dalam downtime cutover):

```bash
sudo CEPAT=1 bash linux/cek-semua-tabel.sh
```

Terjadwal lewat cron (sandi disimpan sekali di `/root/.pgpass-cek-semua`, hanya root):

```bash
sudo JADWAL="0 2 * * 2,3" bash linux/pasang-jadwal-cek.sh     # Selasa & Rabu 02:00
sudo bash linux/pasang-jadwal-cek.sh hapus
```

Sesudah cutover (server sumber sudah mati): `sudo PROD_HOST=pemimpin bash linux/cek-semua-tabel.sh`
membandingkan pemimpin cluster dengan replica.

## Memperbarui versi

```bash
cd ha-helper && git pull       # atau ekstrak zip baru
sudo bash linux/pasang.sh      # config, sandi, riwayat, laporan tetap
```

Contoh config versi baru disimpan sebagai `/opt/ha-helper/config.ini.baru`.

## Menghapus

```bash
sudo bash linux/hapus.sh       # riwayat & laporan dipindah ke /root/ha-helper-data-<tanggal>
```

## Catatan

- Sandi tersimpan di `/opt/ha-helper/data/sandi.dat` (izin 600, hanya
  pengguna `hahelper`). Berkas `sandi.dat` dari Windows tidak bisa dipakai di
  Linux, jadi isi ulang dengan `ha-helper sandi`.
- Email: server harus bisa keluar ke server SMTP (mis. `smtp.gmail.com:587`).
  Bila tidak boleh ke internet, pakai server email internal.
- Notifikasi browser lewat `http://IP` hanya berupa bunyi (aturan browser untuk
  halaman non-HTTPS). Email tetap terkirim 24 jam.
