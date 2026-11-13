"""Server web dashboard dengan login (admin = penuh, lihat = hanya melihat)."""
import json
import os
import socket
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config as cfgmod
from .auth import Auth
from .datacheck import DataCheck
from .cekpenuh import CekPenuh
from .harih import HariH
from .konfig import Konfig
from .monitor import Monitor
from .recorder import Recorder

WEBDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
COOKIE = "hahelper"
ADMIN_ONLY = ("/api/cekdata", "/api/rekam/mulai", "/api/rekam/selesai", "/api/email/uji", "/api/harih")


def _page(name):
    with open(os.path.join(WEBDIR, name), "rb") as f:
        return f.read()


def make_handler(app):
    auth = app["auth"]

    class H(BaseHTTPRequestHandler):
        server_version = "HAHelper"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False, default=str).encode()
                                                         if not isinstance(body, str) else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = min(int(self.headers.get("Content-Length") or 0), 262144)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def _token(self):
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c[COOKIE].value if COOKIE in c else None

        def _session(self):
            return auth.check(self._token())

        def _redirect(self, where):
            self.send_response(302)
            self.send_header("Location", where)
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---------------------------------------------------------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/favicon.ico", "/favicon.svg", "/logo.svg"):
                return self._send(200, _page("logo.svg"), "image/svg+xml", {"Cache-Control": "max-age=86400"})
            if u.path == "/masuk":
                return self._send(200, _page("masuk.html"), "text/html; charset=utf-8")
            s = self._session()
            if not s:
                if u.path == "/":
                    return self._redirect("/masuk")
                return self._send(401, {"error": "belum masuk"})
            if u.path == "/":
                return self._send(200, _page("index.html"), "text/html; charset=utf-8")
            if u.path == "/api/saya":
                return self._send(200, {"pengguna": s["user"], "peran": s["role"]})
            if u.path == "/api/status":
                m = app["mon"]
                with m.lock:
                    return self._send(200, {"state": m.state, "findings": m.findings,
                                            "config": cfgmod.public(app["cfg"]), "now": time.time()})
            if u.path == "/api/riwayat":
                try:
                    since = int((q.get("sejak") or ["0"])[0])
                except ValueError:
                    since = 0
                return self._send(200, app["mon"].history.since(since)[-500:])
            if u.path == "/api/cekdata":
                return self._send(200, app["dc"].result or {})
            if u.path == "/api/konfig":
                with app["kf"].lock:
                    return self._send(200, app["kf"].hasil)
            if u.path == "/api/backup":
                with app["kf"].lock:
                    return self._send(200, app["kf"].backup)
            if u.path == "/api/cekpenuh":
                run = (q.get("run") or [""])[0]
                if run:
                    r = app["cp"].ambil(run)
                    return self._send(200 if r else 404, r or {"error": "tidak ada"})
                return self._send(200, app["cp"].daftar())
            if u.path == "/api/cekpenuh/csv":
                r = app["cp"].ambil((q.get("run") or [""])[0])
                if not r:
                    return self._send(404, {"error": "tidak ada"})
                rows = ["tabel;ukuran;hasil;baris_produksi;" + ";".join("baris_" + n for n in r["node"]) + ";catatan"]
                rows += [";".join([t["tabel"], t["ukuran"], t["hasil"], t["produksi"]] + t["node"] + [t["catatan"]]) for t in r["tabel"]]
                return self._send(200, "\n".join(rows) + "\n", "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="cek-semua-tabel-{r["run"]}.csv"'})
            if u.path == "/api/harih":
                return self._send(200, app["hh"].data())
            if u.path == "/api/harih/laporan":
                txt = app["hh"].laporan(list(app["mon"].history.items))
                return self._send(200, txt, "text/plain; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="laporan-hari-h-{time.strftime("%Y%m%d-%H%M")}.txt"'})
            if u.path == "/api/rekam":
                r = app["rec"].live()
                r["daftar"] = app["rec"].list_reports()[:20]
                return self._send(200, r)
            if u.path == "/api/laporan":
                name = os.path.basename((q.get("nama") or [""])[0])
                p = os.path.join(app["rec"].dir, name)
                if not name.endswith(".txt") or not os.path.exists(p):
                    return self._send(404, {"error": "tidak ada"})
                with open(p, "rb") as f:
                    return self._send(200, f.read(), "text/plain; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
            return self._send(404, {"error": "tidak ada"})

        # ---------------------------------------------------------------- POST
        def do_POST(self):
            u = urlparse(self.path)
            ip = self.client_address[0]
            if u.path == "/api/cekpenuh/lapor":  # dari cek-semua-tabel.sh di node: token, bukan login
                n = min(int(self.headers.get("Content-Length") or 0), 262144)
                teks = self.rfile.read(n).decode("utf-8", "replace")
                code, res = app["cp"].terima(ip, self.headers.get("X-Token"), self.headers.get("X-Run"),
                                             self.headers.get("X-Jenis"), teks)
                return self._send(code, res)
            b = self._body()
            if u.path == "/api/konfig/lapor":  # dari pelapor di node: pakai token, bukan login
                code, res = app["kf"].terima(ip, self.headers.get("X-Token"), b)
                return self._send(code, res)
            if u.path == "/api/masuk":
                tok, info = auth.login(ip, str(b.get("pengguna", "")), str(b.get("sandi", "")))
                if not tok:
                    app["mon"].history.add("info", f"Gagal masuk dari {ip}", f"pengguna: {str(b.get('pengguna', ''))[:40]}")
                    return self._send(403, {"error": info})
                app["mon"].history.add("info", f"{b.get('pengguna')} masuk dari {ip}", f"peran: {info}")
                return self._send(200, {"peran": info}, extra={
                    "Set-Cookie": f"{COOKIE}={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age={12 * 3600}"})
            s = self._session()
            if not s:
                return self._send(401, {"error": "belum masuk"})
            if u.path == "/api/keluar":
                auth.logout(self._token())
                return self._send(200, {"ok": True}, extra={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            if u.path in ADMIN_ONLY and s["role"] != "admin":
                return self._send(403, {"error": "Hanya admin yang boleh melakukan ini."})
            if u.path == "/api/cekdata":
                return self._send(200, {"mulai": app["dc"].start()})
            if u.path == "/api/rekam/mulai":
                ok = app["rec"].start(str(b.get("catatan", ""))[:200])
                if ok:
                    app["mon"].history.add("info", "Perekaman uji failover dimulai", str(b.get("catatan", ""))[:200])
                return self._send(200, {"mulai": ok})
            if u.path == "/api/rekam/selesai":
                name = app["rec"].stop()
                if name:
                    r = app["rec"].last_report
                    app["mon"].history.add("info", "Perekaman uji failover selesai",
                                           f"tanpa pemimpin {r['tanpa_pemimpin']} dtk, jalur tulis putus {r['tulis_putus']} dtk; laporan {name}")
                return self._send(200, {"laporan": name})
            if u.path == "/api/harih":
                nama = str(b.get("nama", "")).strip()[:40]
                code, res = app["hh"].aksi(b, s["user"] + (f" ({nama})" if nama else ""))
                return self._send(code, res)
            if u.path == "/api/email/uji":
                res = app["mon"].alert.send("Uji email dashboard", "Ini email uji dari HA Helper.\nBila sampai, peringatan email berfungsi.", force=True)
                return self._send(200, {"hasil": res})
            return self._send(404, {"error": "tidak ada"})
    return H


def local_ip(cfg):
    """IP PC ini di jaringan menuju cluster (tanpa mengirim paket)."""
    target = cfg.db_nodes[0][1] if cfg.db_nodes else "8.8.8.8"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def serve(cfg, datadir):
    mon = Monitor(cfg, datadir)
    kf = Konfig(cfg, datadir, mon)
    mon.konfig = kf
    cp = CekPenuh(cfg, datadir, kf.token, mon.history)
    kf.cekpenuh = cp
    app = {"cfg": cfg, "mon": mon, "dc": DataCheck(cfg), "rec": Recorder(cfg, datadir), "auth": Auth(cfg), "kf": kf,
           "cp": cp, "hh": HariH(datadir, mon.history)}
    mon.start()
    kf.start()
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer((cfg.listen, cfg.port), make_handler(app))
    url = f"http://{'127.0.0.1' if cfg.listen in ('0.0.0.0', '') else cfg.listen}:{cfg.port}"
    msg = f"\nDashboard berjalan di {url}"
    if cfg.listen in ("0.0.0.0", ""):
        ip = local_ip(cfg)
        if ip and not ip.startswith("127."):
            msg += f"\nDari komputer lain : http://{ip}:{cfg.port}"
    if getattr(sys.stdout, "isatty", lambda: False)():
        msg += "\nBiarkan jendela ini terbuka. Tutup jendela = dashboard berhenti."
    print(msg + "\n", flush=True)
    return httpd, url, app
