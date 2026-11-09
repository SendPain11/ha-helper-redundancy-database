#!/usr/bin/env python3
"""HA Helper - dashboard cluster redundansi database (hanya membaca).

    python jalankan.py              -> buka dashboard di browser
    python jalankan.py --cek        -> cek sekali, tampilkan temuan di layar, lalu keluar
    python jalankan.py -c lain.ini  -> pakai berkas konfigurasi lain
"""
import argparse
import os
import sys
import threading
import webbrowser

if sys.version_info < (3, 8):
    sys.exit("Butuh Python 3.8 atau lebih baru (python.org).")

for _s in (sys.stdout, sys.stderr):  # konsol Windows (cp1252) tidak kenal huruf seperti → dan ≥
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hahelper import config, monitor, server  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="HA Helper")
    ap.add_argument("-c", "--config", default=os.path.join(HERE, "config.ini"))
    ap.add_argument("--cek", action="store_true", help="cek sekali lalu keluar")
    ap.add_argument("--tanpa-browser", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.config):
        sys.exit(f"Berkas konfigurasi tidak ada: {a.config}")
    cfg = config.load(a.config)
    datadir = os.path.join(HERE, "data")
    os.makedirs(datadir, exist_ok=True)

    if a.cek:
        mon = monitor.Monitor(cfg, datadir)
        st, finds = mon.poll_once()
        tag = {"ok": "[ OK ]", "info": "[INFO]", "warn": "[PERHATIAN]", "crit": "[KRITIS]"}
        print(f"\nStatus keseluruhan: {st['level'].upper()}   pemimpin: {st['leader'] or 'TIDAK ADA'}   ({st['poll_ms']} ms)\n")
        for f in finds:
            print(f"{tag[f['level']]:<11} {f['area']:<9} {f['title']}")
            if f["detail"]:
                print(f"{'':21}{f['detail']}")
            if f["action"]:
                print(f"{'':21}-> {f['action']}")
        sys.exit({"ok": 0, "info": 0, "warn": 1, "crit": 2}[st["level"]])

    httpd, url, _ = server.serve(cfg, datadir)
    if not a.tanpa_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard dihentikan.")


if __name__ == "__main__":
    main()
