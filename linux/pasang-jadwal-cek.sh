#!/bin/bash
# Jadwalkan cek-semua-tabel.sh lewat cron (tanpa harus begadang). HANYA MEMBACA database.
#   sudo bash pasang-jadwal-cek.sh                     jadwal bawaan: tiap Sabtu 01:30
#   sudo JADWAL="30 1 * * 3,6" bash pasang-jadwal-cek.sh   (JADWAL ditulis SESUDAH sudo)   Rabu & Sabtu 01:30
#   sudo bash pasang-jadwal-cek.sh sekali 01:30        hanya malam ini / besok pukul 01:30, sekali saja
#   Setelah Hari-H (produksi lama mati), acuan = pemimpin cluster:
#   sudo sed -i 's#PAKAI_PGPASS=1 #PAKAI_PGPASS=1 PROD_HOST=pemimpin #' /etc/cron.d/cek-semua-tabel
#   sudo bash pasang-jadwal-cek.sh hapus               cabut jadwal + sandi tersimpan (/root/.pgpass-cek-semua)
set -e
[ "$(id -u)" = 0 ] || { echo "Jalankan dengan sudo."; exit 1; }
D="$(cd "$(dirname "$0")" && pwd)"
CRON=/etc/cron.d/cek-semua-tabel
PF=/root/.pgpass-cek-semua      # berkas sandi khusus skrip ini (berkas .pgpass lain tidak disentuh)
export PGPASSFILE=$PF
if [ "$1" = "hapus" ]; then
  rm -f $CRON /usr/local/sbin/cek-semua-tabel.sh $PF
  echo "Jadwal cek semua tabel dan $PF dihapus."; exit 0
fi
install -m 755 "$D/cek-semua-tabel.sh" /usr/local/sbin/cek-semua-tabel.sh
mkdir -p /var/log/cek-semua
# --- sandi untuk psql (dibaca psql langsung; hanya root yang bisa membaca)
PROD=${PROD_HOST:-$(grep -o 'PROD_HOST:-[0-9.]*' /usr/local/sbin/cek-semua-tabel.sh | head -1 | cut -d- -f2)}
PPORT=${PROD_PORT:-5432}
if [ ! -s $PF ]; then
  read -s -p "Sandi postgres (produksi & cluster, sama): " P; echo
  [ -n "$P" ] || { echo "Sandi kosong, dibatalkan."; exit 1; }
  P=${P//\\/\\\\}; P=${P//:/\\:}          # escape \ dan : sesuai aturan .pgpass
  umask 077
  { echo "$PROD:$PPORT:*:postgres:$P"; echo "*:5433:*:postgres:$P"; } > $PF
  unset P; chmod 600 $PF
fi
# --- uji sambungan tanpa menanyakan sandi
if ! psql -h "$PROD" -p "$PPORT" -U postgres -d postgres -w -Atc "select 1" >/dev/null 2>&1; then
  echo "GAGAL login ke produksi memakai $PF. Hapus berkasnya (rm $PF) lalu ulangi."; exit 1
fi
echo "Sandi tersimpan di $PF (600) - login ke produksi OK."
if [ "$1" = "sekali" ]; then
  echo "MULAI=${2:-01:30} LATAR=1 PAKAI_PGPASS=1 PGPASSFILE=$PF /usr/local/sbin/cek-semua-tabel.sh" | bash
  exit 0
fi
J=${JADWAL:-30 1 * * 6}
cat > $CRON <<CR
# Cek semua tabel produksi vs cluster (hanya membaca). Hapus SETELAH Hari-H: sudo bash pasang-jadwal-cek.sh hapus
SHELL=/bin/bash
$J root PAKAI_PGPASS=1 PGPASSFILE=$PF flock -n /run/cek-semua-tabel.lock /usr/local/sbin/cek-semua-tabel.sh > /var/log/cek-semua/cron-terakhir.log 2>&1
CR
chmod 644 $CRON
echo "Jadwal terpasang: '$J'  (menit jam tgl bln hari; 6 = Sabtu)"
echo "Hasil: tab 'Cek Semua Tabel' di dashboard, atau tail -25 /var/log/cek-semua/cron-terakhir.log"
