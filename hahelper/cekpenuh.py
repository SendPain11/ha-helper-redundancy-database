"""Hasil cek-semua-tabel.sh (dijalankan di node DB) yang dikirim ke helper. Helper hanya menyimpan & menampilkan."""
import hmac
import json
import os
import re
import threading
import time

from .diagnose import F

SIMPAN = 10          # jumlah putaran cek yang disimpan
BASI = 30 * 60       # tanpa kabar selama ini = dianggap berhenti
STATUS = ("COCOK", "BERUBAH", "AKTIF", "BEDA", "UNLOGGED", "GAGAL")


def _aman(x):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(x))[:80]


def _kv(teks):
    out = {}
    for line in teks.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip()[:40]] = v.strip()[:2000]
    return out


class CekPenuh:
    def __init__(self, cfg, datadir, token, history):
        self.cfg, self.token, self.history = cfg, token, history
        self.dir = os.path.join(datadir, "cekpenuh")
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.runs, self._kotor = {}, {}
        for f in sorted(os.listdir(self.dir))[-SIMPAN:]:
            try:
                with open(os.path.join(self.dir, f), encoding="utf-8") as fh:
                    r = json.load(fh)
                self.runs[r["run"]] = r
            except (OSError, ValueError, KeyError):
                pass

    def _simpan(self, r):
        p = os.path.join(self.dir, _aman(r["run"]) + ".json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)
        self._kotor[r["run"]] = 0
        files = sorted(os.listdir(self.dir))
        for old in files[:-SIMPAN]:
            try:
                os.remove(os.path.join(self.dir, old))
                self.runs.pop(old[:-5], None)
            except OSError:
                pass

    def terima(self, ip, token, run, jenis, teks):
        if not token or not hmac.compare_digest(str(token), self.token):
            return 403, {"error": "token salah"}
        boleh = {i for _, i in self.cfg.db_nodes + self.cfg.etcd_nodes}
        if ip not in boleh:
            return 403, {"error": f"IP {ip} bukan node cluster"}
        run = _aman(run or "")
        if not run:
            return 400, {"error": "X-Run kosong"}
        now = time.time()
        with self.lock:
            if jenis == "mulai":
                kv = _kv(teks)
                r = {"run": run, "dari": ip, "mulai": now, "kabar": now, "selesai": None, "info": kv,
                     "total": int(kv.get("total", "0") or 0), "bytes": int(kv.get("bytes", "0") or 0),
                     "node": [x for x in kv.get("node", "").split(",") if x], "kelengkapan": kv.get("kelengkapan", ""),
                     "tabel": [], "jumlah": {}, "bytes_selesai": 0}
                self.runs[run] = r
                self._simpan(r)
                self.history.add("info", "Cek semua tabel dimulai",
                                 f"{r['total']} tabel dari {kv.get('host', ip)}; {kv.get('filter') or 'semua tabel'}")
                return 200, {"ok": True}
            r = self.runs.get(run)
            if not r:
                return 404, {"error": "putaran tidak dikenal (mulai dulu)"}
            r["kabar"] = now
            if jenis == "tabel":
                for line in teks.splitlines():
                    p = line.split("|")
                    if len(p) < 8:
                        continue
                    t = {"tabel": p[0][:200], "ukuran": p[1][:20], "hasil": p[2][:10], "produksi": p[3][:20],
                         "node": p[4][:200].split(","), "catatan": p[5][:300], "detik": int(p[6] or 0) if p[6].isdigit() else None}
                    r["tabel"].append(t)
                    r["jumlah"][t["hasil"]] = r["jumlah"].get(t["hasil"], 0) + 1
                    if p[7].isdigit():
                        r["bytes_selesai"] += int(p[7])
                self._kotor[run] = self._kotor.get(run, 0) + 1
                if self._kotor[run] >= 25:
                    self._simpan(r)
            elif jenis == "selesai":
                r["selesai"] = now
                self._simpan(r)
                j = r["jumlah"]
                masalah = sum(j.get(k, 0) for k in ("BEDA", "GAGAL", "UNLOGGED"))
                self.history.add("warn" if masalah else "info", "Cek semua tabel selesai",
                                 ", ".join(f"{k} {j[k]}" for k in STATUS if j.get(k)))
            return 200, {"ok": True}

    # ---------------------------------------------------------------- baca
    def daftar(self):
        with self.lock:
            return [self._meta(r) for r in sorted(self.runs.values(), key=lambda r: -r["mulai"])]

    def _meta(self, r):
        now = time.time()
        st = "selesai" if r["selesai"] else ("berhenti" if now - r["kabar"] > BASI else "berjalan")
        eta = None
        if st == "berjalan" and r["bytes_selesai"] and r["bytes"]:
            dt = now - r["mulai"]
            eta = int((r["bytes"] - r["bytes_selesai"]) * dt / r["bytes_selesai"])
        return {"run": r["run"], "mulai": r["mulai"], "selesai": r["selesai"], "kabar": r["kabar"], "status": st,
                "total": r["total"], "dicek": len(r["tabel"]), "jumlah": r["jumlah"], "node": r["node"],
                "info": r["info"], "kelengkapan": r.get("kelengkapan", ""), "sisa_detik": eta,
                "persen": round(100 * r["bytes_selesai"] / r["bytes"], 1) if r["bytes"] else None}

    def ambil(self, run):
        with self.lock:
            r = self.runs.get(run)
            return None if not r else {**self._meta(r), "tabel": r["tabel"]}

    def temuan(self):
        d = self.daftar()
        if not d:
            return []
        m = d[0]
        j, out = m["jumlah"], []
        if m["status"] == "berjalan":
            out.append(F("info", "Cek data", f"Cek semua tabel berjalan: {m['dicek']}/{m['total']} tabel",
                         ", ".join(f"{k} {j[k]}" for k in STATUS if j.get(k))))
        elif m["status"] == "berhenti":
            out.append(F("warn", "Cek data", "Cek semua tabel berhenti sebelum selesai",
                         f"{m['dicek']}/{m['total']} tabel; tidak ada kabar > 30 menit.", "lihat log.txt di /var/log/cek-semua/"))
        tgl = time.strftime("%d/%m %H:%M", time.localtime(m["mulai"]))
        if j.get("BEDA"):
            out.append(F("warn", "Cek data", f"Cek semua tabel ({tgl}): {j['BEDA']} tabel BEDA",
                         "Isi/jumlah berbeda padahal tabel tidak sedang diubah.", "lihat tab Cek Semua Tabel"))
        if j.get("UNLOGGED"):
            out.append(F("warn", "Cek data", f"Cek semua tabel ({tgl}): {j['UNLOGGED']} tabel UNLOGGED",
                         "Isinya tidak direplikasi ke cluster."))
        if j.get("GAGAL"):
            out.append(F("info", "Cek data", f"Cek semua tabel ({tgl}): {j['GAGAL']} tabel GAGAL dibaca",
                         "Ulangi dengan TABEL_FILE=.../ulang.txt"))
        if m["status"] == "selesai" and not any(j.get(k) for k in ("BEDA", "UNLOGGED", "GAGAL")):
            out.append(F("ok", "Cek data", f"Cek semua tabel ({tgl}): {m['dicek']} tabel tanpa BEDA",
                         ", ".join(f"{k} {j[k]}" for k in STATUS if j.get(k))))
        for f in out:
            f["kunci"] = re.sub(r"\(\d\d/\d\d \d\d:\d\d\)|\d+/\d+", "", f["title"])
        return out
