"""Pemantau berkala: mengambil status, mendeteksi perubahan, mencatat riwayat, dan mengirim peringatan."""
import json
import os
import smtplib
import threading
import time
from email.mime.text import MIMEText

from . import collect, diagnose

RANK = diagnose.RANK


class Alerter:
    def __init__(self, cfg, history):
        self.cfg, self.history = cfg, history

    def send(self, subject, body, force=False):
        c = self.cfg
        if not (c.mail_on or force) or not c.mail_to:
            return "email tidak aktif"
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = "[Cluster DB] " + subject, c.mail_from, ", ".join(c.mail_to)
            with smtplib.SMTP(c.mail_host, c.mail_port, timeout=15) as s:
                if c.mail_tls:
                    s.starttls()
                if c.mail_user:
                    s.login(c.mail_user, c.mail_pass)
                s.sendmail(c.mail_from, c.mail_to, msg.as_string())
            return "terkirim"
        except Exception as e:  # noqa: BLE001
            return f"GAGAL: {type(e).__name__}: {e}"[:200]

    def send_async(self, subject, body, event):
        def run():
            event["email"] = self.send(subject, body)
            self.history.save()
        threading.Thread(target=run, daemon=True).start()


class History:
    def __init__(self, path, keep=2000):
        self.path, self.keep, self.lock = path, keep, threading.Lock()
        self.items = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.items = [json.loads(x) for x in f if x.strip()][-keep:]
            except Exception:  # noqa: BLE001
                self.items = []

    def add(self, level, title, detail=""):
        e = {"id": int(time.time() * 1000), "time": time.time(), "level": level, "title": title, "detail": detail}
        with self.lock:
            self.items.append(e)
            self.items = self.items[-self.keep:]
        self.save()
        return e

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for x in self.items:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)

    def since(self, ms):
        with self.lock:
            return [x for x in self.items if x["id"] > ms]


def _key(s):
    """Ciri keadaan yang dibandingkan antar putaran untuk mendeteksi kejadian."""
    k = {"leader": s["leader"], "paused": s["paused"]}
    for n in s["nodes"]:
        k["node:" + n["name"]] = (n["in_cluster"], n["role"], n["state"] in ("running", "streaming") if n["state"] else None)
    for name, m in s["etcd"]["members"].items():
        k["etcd:" + name] = (m["ok"] and m.get("healthy")) if m["ok"] else "?"
    for n, h in s["haproxy"].items():
        if h["ok"]:
            k["tulis:" + n] = sum(1 for x in h["tulis"] if (x["status"] or "").startswith("UP"))
    for v, holder in s["vip"].items():
        k["vip:" + v] = holder
    return k


class Monitor:
    def __init__(self, cfg, datadir):
        self.cfg = cfg
        self.history = History(os.path.join(datadir, "riwayat.jsonl"))
        self.alert = Alerter(cfg, self.history)
        self.state, self.findings, self.prev = None, [], None
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def poll_once(self):
        snap = collect.snapshot(self.cfg)
        summ, finds = diagnose.summarize(self.cfg, snap)
        with self.lock:
            self.state, self.findings = summ, finds
        self._events(summ, finds)
        return summ, finds

    def _events(self, s, finds):
        cur = _key(s)
        if self.prev is None:
            self.history.add("info", "Dashboard mulai memantau",
                             f"Pemimpin: {s['leader'] or 'TIDAK ADA'}; status keseluruhan: {s['level'].upper()}")
            self.prev = cur
            return
        p, ev = self.prev, []
        if cur["leader"] != p["leader"]:
            if cur["leader"] is None:
                ev.append(("crit", "Cluster tanpa pemimpin", f"Sebelumnya: {p['leader']}"))
            else:
                ev.append(("warn", f"Pemimpin berpindah: {p['leader'] or 'tidak ada'} → {cur['leader']}", ""))
        if cur["paused"] != p["paused"]:
            ev.append(("warn" if cur["paused"] else "info",
                       "Mode pemeliharaan Patroni " + ("DIAKTIFKAN" if cur["paused"] else "dimatikan"), ""))
        for k, v in cur.items():
            old = p.get(k)
            if old == v or k in ("leader", "paused"):
                continue
            kind, name = k.split(":", 1)
            if kind == "node":
                in_c, role, run = v
                if old and old[0] and not in_c:
                    ev.append(("crit", f"{name} keluar dari cluster (mati / Patroni berhenti)", ""))
                elif old and not old[0] and in_c:
                    ev.append(("info", f"{name} kembali bergabung ke cluster", f"peran: {role}"))
                elif old and old[2] and run is False:
                    ev.append(("warn", f"{name} tidak lagi running", ""))
            elif kind == "etcd" and v != "?" and old != "?":
                ev.append(("info" if v else "warn", f"Anggota etcd {name} " + ("sehat kembali" if v else "TIDAK SEHAT"), ""))
            elif kind == "tulis" and old is not None:
                if v == 0:
                    ev.append(("crit", f"HAProxy {name}: jalur tulis tanpa server", ""))
                elif old == 0:
                    ev.append(("info", f"HAProxy {name}: jalur tulis pulih", ""))
            elif kind == "vip" and old is not None:
                if v == "TIDAK TERJANGKAU":
                    ev.append(("warn", f"Alamat {name} TIDAK TERJANGKAU", f"Sebelumnya dipegang {old}. Cek keepalived."))
                elif old == "TIDAK TERJANGKAU":
                    ev.append(("info", f"Alamat {name} terjangkau lagi, dipegang {v}", ""))
                else:
                    ev.append(("info", f"Alamat {name} pindah: {old} → {v}", ""))
        self.prev = cur
        for lvl, title, detail in ev:
            e = self.history.add(lvl, title, detail)
            if self.cfg.mail_on and RANK[lvl] >= RANK.get(self.cfg.mail_min, 2):
                body = (f"{title}\n{detail}\n\nWaktu: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Pemimpin sekarang: {s['leader'] or 'TIDAK ADA'}\n\nTemuan saat ini:\n" +
                        "\n".join(f"- [{f['level'].upper()}] {f['area']}: {f['title']}" for f in finds))
                self.alert.send_async(title, body, e)

    def loop(self):
        while not self.stop.is_set():
            t0 = time.time()
            try:
                self.poll_once()
            except Exception as e:  # noqa: BLE001
                self.history.add("warn", "Pemantau gagal satu putaran", f"{type(e).__name__}: {e}")
            self.stop.wait(max(1.0, self.cfg.poll - (time.time() - t0)))

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()
