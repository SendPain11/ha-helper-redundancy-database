#!/usr/bin/env python3
"""HA Helper - dashboard cluster redundansi database (hanya membaca).

    python jalankan.py                -> buka dashboard (sandi ditanya bila belum disimpan)
    python jalankan.py --cek          -> cek sekali, tampilkan temuan di layar, lalu keluar
    python jalankan.py --simpan-sandi -> simpan sandi cluster terenkripsi di PC ini
    python jalankan.py --latar        -> jalan di latar (untuk Task Scheduler), log ke data/helper.log
"""
import argparse
import getpass
import os
import sys
import threading
import time
import webbrowser

if sys.version_info < (3, 8):
    sys.exit("Butuh Python 3.8 atau lebih baru (python.org).")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
sys.path.insert(0, HERE)


class _Log:
    """Pengganti layar saat jalan di latar (pythonw tidak punya layar). Dibatasi 5 MB."""

    def __init__(self, path):
        self.path = path

    def write(self, s):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > 5 * 1048576:
                os.replace(self.path, self.path + ".lama")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(s)
        except OSError:
            pass

    def flush(self):
        pass


def _console_utf8():
    for s in (sys.stdout, sys.stderr):  # konsol Windows (cp1252) tidak kenal huruf seperti → dan ≥
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def simpan_sandi(cfgpath, store):
    from hahelper import collect, config
    from hahelper.pgwire import Conn
    c = config.load(cfgpath, ask=False)
    need = config.secrets_needed(c)
    old = store.load()
    print("\nSIMPAN SANDI\n" + "-" * 50)
    print("Sandi disimpan " + ("terenkripsi (hanya bisa dibuka akun Windows ini di PC ini)."
                               if store.encrypted() else "TANPA enkripsi (bukan Windows)."))
    print("Tekan Enter tanpa mengetik untuk memakai sandi yang sudah tersimpan.\n")
    vals = {}
    for key, attr, label in need:
        if getattr(c, attr):
            print(f"- {label}: sudah diisi di config.ini, dilewati")
            continue
        v = getpass.getpass(f"- {label}" + (" [tersimpan]" if old.get(key) else "") + ": ")
        vals[key] = v or old.get(key, "")
    store.save(vals)
    print("\nTersimpan di data\\sandi.dat. Mencoba sambungan...\n")
    c = config.load(cfgpath, ask=False, store=store)
    if c.stats_user and c.db_nodes:
        try:
            code, _ = collect.http(f"http://{c.db_nodes[0][1]}:{c.p_stats}/;csv;norefresh", 3,
                                   auth=(c.stats_user, c.stats_pass))
            print(f"  HAProxy {c.db_nodes[0][1]} : " + ("OK" if code == 200 else f"GAGAL (HTTP {code}, sandi salah?)"))
        except Exception as e:  # noqa: BLE001
            print(f"  HAProxy {c.db_nodes[0][1]} : tidak terjangkau ({e})")
    tests = []
    if c.prod_on:
        tests.append(("Produksi", c.prod_host, c.prod_port, c.prod_user, c.prod_pass))
    tests.append(("Cluster ", c.cl_host, c.p_tulis, c.cl_user, c.cl_pass))
    for label, h, p, u, pw in tests:
        try:
            with Conn(h, p, u, pw, c.db, timeout=5) as k:
                k.query("SELECT 1")
            print(f"  {label} {h}:{p} : OK")
        except Exception as e:  # noqa: BLE001
            print(f"  {label} {h}:{p} : GAGAL ({str(e)[:120]})")


def main():
    ap = argparse.ArgumentParser(description="HA Helper")
    ap.add_argument("-c", "--config", default=os.path.join(HERE, "config.ini"))
    ap.add_argument("--cek", action="store_true", help="cek sekali lalu keluar")
    ap.add_argument("--simpan-sandi", action="store_true")
    ap.add_argument("--latar", action="store_true", help="jalan tanpa layar, untuk Task Scheduler")
    ap.add_argument("--tanpa-browser", action="store_true")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    if a.latar or sys.stdout is None:
        sys.stdout = sys.stderr = _Log(os.path.join(DATA, "helper.log"))
        a.tanpa_browser = True
    else:
        _console_utf8()
    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} HA Helper mulai ===", flush=True)

    from hahelper import config, monitor, server
    from hahelper.secret import Store
    if not os.path.exists(a.config):
        sys.exit(f"Berkas konfigurasi tidak ada: {a.config}")
    store = Store(os.path.join(DATA, "sandi.dat"))

    if a.simpan_sandi:
        return simpan_sandi(a.config, store)

    cfg = config.load(a.config, ask=not a.latar, store=store)
    if a.latar:
        missing = [label for key, attr, label in config.secrets_needed(cfg) if not getattr(cfg, attr)]
        if missing:
            print("Sandi belum disimpan: " + "; ".join(missing) + ". Jalankan simpan-sandi.bat dulu.", flush=True)
            sys.exit(3)

    if a.cek:
        mon = monitor.Monitor(cfg, DATA)
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

    if not cfg.admin_pass:
        sys.exit("config.ini [akses] admin_sandi belum diisi.")
    try:
        httpd, url, _ = server.serve(cfg, DATA)
    except OSError as e:
        sys.exit(f"Port {cfg.port} tidak bisa dipakai ({e}). Mungkin HA Helper sudah berjalan di latar: "
                 f"buka saja http://127.0.0.1:{cfg.port}")
    if not a.tanpa_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard dihentikan.")


if __name__ == "__main__":
    main()
