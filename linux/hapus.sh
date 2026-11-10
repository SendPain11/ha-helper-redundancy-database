#!/bin/bash
# HA Helper - hapus dari witness. Riwayat & laporan dipindah ke /root/ha-helper-data-<tanggal>.
[ "$(id -u)" = 0 ] || { echo "Jalankan dengan sudo."; exit 1; }
systemctl disable --now ha-helper 2>/dev/null
rm -f /etc/systemd/system/ha-helper.service /usr/local/sbin/ha-helper
systemctl daemon-reload
[ -d /opt/ha-helper/data ] && mv /opt/ha-helper/data "/root/ha-helper-data-$(date +%F)" && rm -f "/root/ha-helper-data-$(date +%F)/sandi.dat"
rm -rf /opt/ha-helper
userdel hahelper 2>/dev/null
echo "HA Helper dihapus. Hapus juga aturan ufw:  sudo ufw status numbered | grep 'HA Helper'"
