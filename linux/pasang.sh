#!/bin/bash
# HA Helper - pasang / perbarui di server Linux (witness) sebagai layanan systemd.
# Jalankan dari folder hasil ekstrak:  sudo bash linux/pasang.sh
# Aman diulang untuk memperbarui versi: config.ini, sandi, riwayat dan laporan tetap.
set -e
SRC=$(cd "$(dirname "$0")/.." && pwd)
DST=/opt/ha-helper
[ "$(id -u)" = 0 ] || { echo "Jalankan dengan sudo."; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null \
  || { echo "Butuh python3 versi 3.8 atau lebih baru:  apt install python3"; exit 1; }
UPGRADE=no; [ -f "$DST/config.ini" ] && UPGRADE=yes

id hahelper >/dev/null 2>&1 || useradd --system --home-dir "$DST" --no-create-home --shell /usr/sbin/nologin hahelper
mkdir -p "$DST/data"
if [ "$SRC" != "$DST" ]; then
  rm -rf "$DST/hahelper"
  cp -r "$SRC/hahelper" "$SRC/jalankan.py" "$DST/"
  CFG="$SRC/config.ini"; [ -f "$CFG" ] || CFG="$SRC/config.contoh.ini"
  if [ "$UPGRADE" = yes ]; then
    cp "$CFG" "$DST/config.ini.baru"
  else
    cp "$CFG" "$DST/config.ini"
  fi
fi
sed -i 's/\r$//' "$DST"/config.ini*
find "$DST/hahelper" -name __pycache__ -prune -exec rm -rf {} +

chown -R root:hahelper "$DST"
chmod -R u=rwX,g=rX,o= "$DST"
chmod 640 "$DST"/config.ini*
chown -R hahelper:hahelper "$DST/data"
chmod 750 "$DST/data"

install -m 644 "$SRC/linux/ha-helper.service" /etc/systemd/system/ha-helper.service
install -m 755 "$SRC/linux/ha-helper" /usr/local/sbin/ha-helper
systemctl daemon-reload

if [ "$UPGRADE" = yes ]; then
  echo
  echo "DIPERBARUI ke versi $(cd "$DST" && python3 -B -c 'import hahelper;print(hahelper.__version__)')."
  echo "config.ini lama dipertahankan; contoh versi baru ada di $DST/config.ini.baru"
  systemctl is-enabled --quiet ha-helper && systemctl restart ha-helper && echo "Dashboard dimulai ulang."
  exit 0
fi

PORT=$(sed -n 's/^port_dashboard *= *\([0-9]*\).*/\1/p' "$DST/config.ini" | head -1); PORT=${PORT:-8099}
cat <<MSG

TERPASANG di $DST (port dashboard $PORT). Langkah berikut:

  1. Buka port dashboard untuk PC yang boleh membuka (ulangi per IP):
       sudo ufw allow from <IP-PC> to any port $PORT proto tcp comment 'HA Helper dashboard'
  2. Cek konfigurasi:        sudo ha-helper config
  3. Simpan sandi + uji:     sudo ha-helper sandi
  4. Nyalakan:               sudo ha-helper hidup
  5. Buka di browser:        http://$(hostname -I | awk '{print $1}'):$PORT

  Perintah lain:             sudo ha-helper
MSG
