# Pasang di Windows

## 1. Persiapan

1. Pasang Python 3 dari <https://www.python.org/downloads/>.
   Centang **Add python.exe to PATH**.
2. Unduh repo ini (Code → Download ZIP), lalu ekstrak, mis. ke `D:\ha-helper`.
3. Salin `config.contoh.ini` menjadi `config.ini`, lalu isi dengan Notepad:
   alamat node, etcd, VIP, dan sandi login di `[akses]`.
4. Buka akses dari PC ini ke cluster (lihat tabel porta di README), contoh di
   node database:
   `sudo ufw allow from <IP-PC> to any port 8008,2379 proto tcp comment 'HA Helper'`

## 2a. Jalan manual

- `jalankan.bat`: jendela hitam menanyakan sandi, lalu browser terbuka.
  Tutup jendela berarti dashboard berhenti.
- `cek-sekali.bat`: cek cepat tanpa browser.

## 2b. Jalan terus di latar (disarankan)

1. `simpan-sandi.bat`: isi sandi. Sandi disimpan terenkripsi (DPAPI), hanya
   bisa dibuka akun Windows ini di PC ini. Di akhir tampil uji sambungan.
2. `pasang-jalan-terus.bat` (klik **Yes**): isi sandi login Windows. Ini
   membuat tugas terjadwal "HA Helper" (mulai saat PC menyala, hidup lagi
   bila berhenti) dan membuka porta dashboard di firewall Windows.
3. Buka `http://127.0.0.1:8099`. Dari komputer lain: `http://<IP-PC>:8099`.

Atur PC agar tidak tidur (Settings → System → Power → Sleep = Never).

| Berkas | Fungsi |
|---|---|
| `mulai-ulang.bat` | setelah mengubah config.ini atau menyimpan sandi baru |
| `hapus-jalan-terus.bat` | hentikan & hapus mode jalan terus |
| `data\helper.log` | catatan program bila ada masalah |

Bila login Windows memakai PIN dan sandi akun tidak diketahui, langkah 2b.2
tidak bisa. Pakai cara 2a.

## Email (opsional)

1. Akun Gmail khusus notifikasi, aktifkan Verifikasi 2 Langkah.
2. <https://myaccount.google.com/apppasswords>: buat sandi aplikasi "HA Helper".
3. `config.ini` `[email]`: `aktif = yes`, isi pengguna, pengirim, penerima.
4. `simpan-sandi.bat` (isi sandi aplikasi), lalu `mulai-ulang.bat`.
5. Login admin → Pengaturan → **Kirim email uji**.
