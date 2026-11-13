#!/bin/bash
# Pasang pelapor konfigurasi HA Helper di SATU node (DB atau witness).
# Hanya membaca konfigurasi node dan mengirim ringkasan ke helper tiap 5 menit. Tidak mengubah layanan apa pun.
#   sudo bash pasang-lapor.sh http://10.10.0.30:8099 <token>
#   sudo bash pasang-lapor.sh hapus        (copot pelapor)
set -e
[ "$(id -u)" = 0 ] || { echo "Jalankan dengan sudo."; exit 1; }
D="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "hapus" ]; then
  rm -f /etc/cron.d/hahelper-lapor /usr/local/sbin/hahelper-lapor.py /var/cache/hahelper-lapor-backup.json /etc/hahelper-lapor.conf /var/log/hahelper-lapor.log
  echo "Pelapor dicopot."; exit 0
fi
URL="$1"; TOKEN="$2"
[ -n "$URL" ] && [ -n "$TOKEN" ] || { echo "Pemakaian: sudo bash pasang-lapor.sh http://<IP-helper>:8099 <token>"; exit 1; }
command -v python3 >/dev/null || { echo "python3 tidak ada."; exit 1; }
install -m 755 "$D/hahelper-lapor.py" /usr/local/sbin/hahelper-lapor.py
umask 077
printf 'URL=%s\nTOKEN=%s\n' "$URL" "$TOKEN" > /etc/hahelper-lapor.conf
chmod 600 /etc/hahelper-lapor.conf
# menit acak 0-4 supaya kelima node tidak mengirim bersamaan
M=$((RANDOM % 5))
cat > /etc/cron.d/hahelper-lapor <<CRON
# HA Helper - pelapor konfigurasi (hanya membaca)
$M-59/5 * * * * root flock -n /run/hahelper-lapor.lock /usr/bin/python3 /usr/local/sbin/hahelper-lapor.py > /var/log/hahelper-lapor.log 2>&1
CRON
chmod 644 /etc/cron.d/hahelper-lapor
echo "== Isi laporan node ini =="
/usr/bin/python3 /usr/local/sbin/hahelper-lapor.py --tampil --kirim
