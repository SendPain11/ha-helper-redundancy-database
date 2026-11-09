"""Perekam uji failover: mencatat keadaan cluster setiap detik dan membuat laporan."""
import json
import os
import threading
import time

from . import collect
from .pgwire import Conn

LEADER_ROLES = ("leader", "standby_leader", "master", "primary")


def _ts(t):
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 10)}"


class Recorder:
    def __init__(self, cfg, datadir):
        self.cfg, self.dir = cfg, os.path.join(datadir, "laporan")
        os.makedirs(self.dir, exist_ok=True)
        self.active, self.samples, self.events, self.note = False, [], [], ""
        self.lock = threading.Lock()
        self.last_report = None

    # ---------------------------------------------------------------- sampel
    def _leader(self):
        for _, ip in self.cfg.db_nodes:
            try:
                _, body = collect.http(f"http://{ip}:{self.cfg.p_patroni}/cluster", 1.0)
                ms = json.loads(body).get("members", [])
                ld = [m for m in ms if m.get("role") in LEADER_ROLES]
                return (ld[0]["name"] if len(ld) == 1 else None), (ld[0].get("timeline") if ld else None), \
                       sorted(m["name"] for m in ms)
            except Exception:  # noqa: BLE001
                continue
        return "?", None, []

    def _tulis(self, ip):
        try:
            auth = (self.cfg.stats_user, self.cfg.stats_pass) if self.cfg.stats_user else None
            _, body = collect.http(f"http://{ip}:{self.cfg.p_stats}/;csv;norefresh", 1.0, auth=auth)
            for line in body.splitlines():
                f = line.split(",")
                if len(f) > 17 and f[0] == "pgsql_tulis" and f[1] == "BACKEND":
                    return f[17]
            return "?"
        except Exception:  # noqa: BLE001
            return "?"

    def _app(self):
        try:
            with Conn(self.cfg.cl_host, self.cfg.p_tulis, self.cfg.cl_user, self.cfg.cl_pass, self.cfg.db, timeout=1.5) as c:
                c.query("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _sample(self):
        t = time.time()
        f_l = collect.POOL.submit(self._leader)
        f_t = {n: collect.POOL.submit(self._tulis, ip) for n, ip in self.cfg.db_nodes}
        f_a = collect.POOL.submit(self._app)
        leader, tl, members = f_l.result()
        return {"t": t, "leader": leader, "tl": tl, "members": members,
                "tulis": {n: f.result() for n, f in f_t.items()}, "app": f_a.result()}

    # ---------------------------------------------------------------- loop
    def start(self, note=""):
        with self.lock:
            if self.active:
                return False
            self.active, self.samples, self.events, self.note = True, [], [], note
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        prev = None
        while self.active:
            t0 = time.time()
            s = self._sample()
            with self.lock:
                self.samples.append(s)
                if prev is not None:
                    self._diff(prev, s)
                else:
                    self.events.append((s["t"], f"Mulai merekam. Pemimpin: {s['leader'] or 'TIDAK ADA'}, TL {s['tl']}, "
                                                f"anggota: {', '.join(s['members'])}"))
            prev = s
            time.sleep(max(0.05, 1.0 - (time.time() - t0)))

    def _diff(self, a, b):
        ev = self.events
        if a["leader"] != b["leader"]:
            ev.append((b["t"], "TIDAK ADA PEMIMPIN" if b["leader"] is None else
                       ("Patroni tidak terjangkau" if b["leader"] == "?" else f"Pemimpin: {b['leader']}")))
        if a["tl"] != b["tl"] and b["tl"] is not None and a["tl"] is not None:
            ev.append((b["t"], f"Timeline berubah {a['tl']} → {b['tl']}"))
        if a["members"] != b["members"] and b["members"]:
            gone = set(a["members"]) - set(b["members"])
            came = set(b["members"]) - set(a["members"])
            if gone:
                ev.append((b["t"], "Keluar dari cluster: " + ", ".join(sorted(gone))))
            if came:
                ev.append((b["t"], "Bergabung ke cluster: " + ", ".join(sorted(came))))
        wa = any(v == "UP" for v in a["tulis"].values())
        wb = any(v == "UP" for v in b["tulis"].values())
        if wa != wb:
            ev.append((b["t"], "Jalur tulis HAProxy " + ("PULIH" if wb else "TANPA SERVER")))
        if a["app"] != b["app"]:
            ev.append((b["t"], "Koneksi ke database lewat HAProxy " + ("berhasil lagi" if b["app"] else "GAGAL")))

    # ---------------------------------------------------------------- laporan
    @staticmethod
    def _periods(samples, bad):
        out, start = [], None
        for s in samples:
            if bad(s) and start is None:
                start = s["t"]
            elif not bad(s) and start is not None:
                out.append((start, s["t"]))
                start = None
        if start is not None:
            out.append((start, samples[-1]["t"]))
        return out

    def live(self):
        with self.lock:
            last = self.samples[-1] if self.samples else None
            return {"aktif": self.active, "mulai": self.samples[0]["t"] if self.samples else None,
                    "sampel": len(self.samples), "terakhir": last,
                    "kejadian": [{"t": t, "teks": x} for t, x in self.events[-50:]],
                    "laporan_terakhir": self.last_report}

    def stop(self):
        with self.lock:
            if not self.active:
                return None
            self.active = False
        time.sleep(1.2)
        with self.lock:
            S, E = list(self.samples), list(self.events)
        if not S:
            return None
        no_leader = self._periods(S, lambda s: s["leader"] is None)
        no_write = self._periods(S, lambda s: not any(v == "UP" for v in s["tulis"].values()))
        no_app = self._periods(S, lambda s: not s["app"])
        dur = lambda ps: sum(b - a for a, b in ps)  # noqa: E731
        name = time.strftime("uji-failover-%Y%m%d-%H%M%S.txt", time.localtime(S[0]["t"]))
        L = ["LAPORAN UJI FAILOVER - CLUSTER REDUNDANSI DATABASE", "=" * 60,
             f"Keterangan uji : {self.note or '-'}",
             f"Mulai          : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(S[0]['t']))}",
             f"Selesai        : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(S[-1]['t']))}",
             f"Lama rekaman   : {round(S[-1]['t'] - S[0]['t'])} detik ({len(S)} sampel, 1 per detik)",
             f"Pemimpin awal  : {S[0]['leader']}  (TL {S[0]['tl']})",
             f"Pemimpin akhir : {S[-1]['leader']}  (TL {S[-1]['tl']})", "",
             "RINGKASAN", "-" * 60,
             f"Tanpa pemimpin            : {round(dur(no_leader), 1)} detik dalam {len(no_leader)} periode",
             f"Jalur tulis HAProxy putus : {round(dur(no_write), 1)} detik dalam {len(no_write)} periode",
             f"Koneksi database gagal    : {round(dur(no_app), 1)} detik dalam {len(no_app)} periode", ""]
        for title, ps in (("Periode tanpa pemimpin", no_leader), ("Periode jalur tulis putus", no_write),
                          ("Periode koneksi gagal", no_app)):
            if ps:
                L.append(title + ":")
                L += [f"  {_ts(a)}  s/d  {_ts(b)}   = {round(b - a, 1)} detik" for a, b in ps]
                L.append("")
        L += ["URUTAN KEJADIAN", "-" * 60] + [f"  {_ts(t)}  {x}" for t, x in E] + ["",
              "Catatan: sebelum cutover, 'koneksi database' mengukur jalur HAProxy ke pemimpin standby (hanya baca).",
              "Setelah cutover, angka yang sama adalah lama aplikasi tidak bisa menulis."]
        text = "\n".join(L) + "\n"
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)
        self.last_report = {"nama": name, "tanpa_pemimpin": round(dur(no_leader), 1),
                            "tulis_putus": round(dur(no_write), 1), "koneksi_gagal": round(dur(no_app), 1)}
        return name

    def list_reports(self):
        return sorted((f for f in os.listdir(self.dir) if f.endswith(".txt")), reverse=True)
