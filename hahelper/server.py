"""Server web lokal untuk dashboard."""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config as cfgmod
from .datacheck import DataCheck
from .monitor import Monitor
from .recorder import Recorder

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False, default=str).encode()
                                                         if not isinstance(body, str) else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                with open(WEB, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/status":
                m = app["mon"]
                with m.lock:
                    return self._send(200, {"state": m.state, "findings": m.findings,
                                            "config": cfgmod.public(app["cfg"]), "now": time.time()})
            if u.path == "/api/riwayat":
                since = int((q.get("sejak") or ["0"])[0])
                return self._send(200, app["mon"].history.since(since)[-500:])
            if u.path == "/api/cekdata":
                return self._send(200, app["dc"].result or {})
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

        def do_POST(self):
            u = urlparse(self.path)
            b = self._body()
            if u.path == "/api/cekdata":
                return self._send(200, {"mulai": app["dc"].start()})
            if u.path == "/api/rekam/mulai":
                ok = app["rec"].start(str(b.get("catatan", ""))[:200])
                if ok:
                    app["mon"].history.add("info", "Perekaman uji failover dimulai", b.get("catatan", ""))
                return self._send(200, {"mulai": ok})
            if u.path == "/api/rekam/selesai":
                name = app["rec"].stop()
                if name:
                    r = app["rec"].last_report
                    app["mon"].history.add("info", "Perekaman uji failover selesai",
                                           f"tanpa pemimpin {r['tanpa_pemimpin']} dtk, jalur tulis putus {r['tulis_putus']} dtk; laporan {name}")
                return self._send(200, {"laporan": name})
            if u.path == "/api/email/uji":
                res = app["mon"].alert.send("Uji email dashboard", "Ini email uji dari HA Helper.\nBila sampai, peringatan email berfungsi.", force=True)
                return self._send(200, {"hasil": res})
            return self._send(404, {"error": "tidak ada"})
    return H


def serve(cfg, datadir):
    mon = Monitor(cfg, datadir)
    app = {"cfg": cfg, "mon": mon, "dc": DataCheck(cfg), "rec": Recorder(cfg, datadir)}
    mon.start()
    httpd = ThreadingHTTPServer((cfg.listen, cfg.port), make_handler(app))
    url = f"http://{'127.0.0.1' if cfg.listen in ('0.0.0.0', '') else cfg.listen}:{cfg.port}"
    print(f"\nDashboard berjalan di {url}\nBiarkan jendela ini terbuka. Tutup jendela = dashboard berhenti.\n", flush=True)
    return httpd, url, app
