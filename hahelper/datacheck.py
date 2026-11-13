"""Cek streaming & ISI data. Hanya membaca.

Sebelum cutover ([produksi] aktif = yes): acuan = produksi, dibandingkan dengan jalur tulis & baca cluster.
Sesudah cutover ([produksi] aktif = no) : acuan = pemimpin (jalur tulis), dibandingkan dengan replica (jalur baca).

Yang dicocokkan per tabel: JUMLAH baris dan ISI setiap baris (sidik md5 seluruh kolom tiap baris, dijumlahkan).
Bagian yang beda dicek ulang sampai 2 kali: data yang sedang diubah aplikasi bisa tampak beda sesaat.
"""
import json
import threading
import time

from . import collect
from .pgwire import Conn, PgError, lsn_to_int

# pengaturan sesi SAMA di semua server, supaya teks tiap baris dibentuk persis sama
SESI = ("SET TimeZone='UTC'", "SET DateStyle='ISO, YMD'", "SET IntervalStyle='postgres'",
        "SET extra_float_digits=1", "SET bytea_output='hex'", "SET statement_timeout=0")
SIDIK = "coalesce(sum(('x'||substr(md5(t::text),1,16))::bit(64)::bigint),0)::text"
POS = "(CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_lsn() END)::text"
ULANG = 2


def _sql(tables, t1, t2):
    return " UNION ALL ".join(
        f"SELECT '{t}'::text, count(*)::text, {SIDIK} FROM public.{t} t WHERE {c} >= '{t1}' AND {c} < '{t2}'"
        for t, c in tables)


def _baca(host, port, user, pw, db, sql, tries=3):
    """Satu transaksi REPEATABLE READ: semua tabel dibaca pada satu titik waktu. -> (alamat, {tabel:(jml,sidik)}, lsn)"""
    last = None
    for _ in range(tries):  # di replica, query bisa dibatalkan karena konflik replikasi: coba ulang
        try:
            with Conn(host, port, user, pw, db, timeout=60) as c:
                for st in SESI:
                    c.query(st)
                c.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                addr = c.query("SELECT coalesce(host(inet_server_addr()),'lokal')")[0][0]
                rows = c.query(sql)
                lsn = c.query(f"SELECT {POS}")[0][0]
                c.query("COMMIT")
            return addr, {r[0]: (r[1], r[2]) for r in rows}, lsn
        except (PgError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def _replayed(cfg, ip):
    _, body = collect.http(f"http://{ip}:{cfg.p_patroni}/patroni", 2)
    x = (json.loads(body).get("xlog") or {})
    v = x.get("replayed_location", x.get("location"))
    return v if isinstance(v, int) else lsn_to_int(v)


class DataCheck:
    def __init__(self, cfg):
        self.cfg, self.result, self.running, self.lock = cfg, None, False, threading.Lock()

    def start(self):
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.result = {"status": "berjalan", "mulai": time.time(), "langkah": "Menyambung ke acuan"}
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _step(self, s):
        self.result["langkah"] = s

    # ------------------------------------------------------------------ sumber
    def _sumber(self):
        c = self.cfg
        if c.prod_on:
            ref = ("Produksi", (c.prod_host, c.prod_port, c.prod_user, c.prod_pass, c.db))
            cols = [("Jalur tulis (pemimpin)", (c.cl_host, c.p_tulis, c.cl_user, c.cl_pass, c.db)),
                    ("Jalur baca (replica)", (c.cl_host, c.p_baca, c.cl_user, c.cl_pass, c.db))]
        else:
            ref = ("Pemimpin (jalur tulis)", (c.cl_host, c.p_tulis, c.cl_user, c.cl_pass, c.db))
            cols = [("Replica (jalur baca)", (c.cl_host, c.p_baca, c.cl_user, c.cl_pass, c.db))]
        return ref, cols

    def _tunggu(self, lsn):
        """Tunggu semua node menerapkan data sampai posisi lsn (maks 120 dtk). -> {node: detik|None}"""
        target, t0, catch = lsn_to_int(lsn), time.time(), {}
        pending = dict(self.cfg.db_nodes)
        while pending and time.time() - t0 <= 120:
            for name, ip in list(pending.items()):
                try:
                    v = _replayed(self.cfg, ip)
                    if v is not None and v >= target:
                        catch[name] = round(time.time() - t0, 1)
                        del pending[name]
                except Exception:  # noqa: BLE001
                    pass
            if pending:
                time.sleep(1)
        for name in pending:
            catch[name] = None
        return catch

    def _putaran(self, ref, cols, tables, t1, t2):
        sql = _sql(tables, t1, t2)
        _, acuan, lsn = _baca(*ref[1], sql)
        catch = self._tunggu(lsn)
        hasil = []
        for label, src in cols:
            try:
                addr, val, _ = _baca(*src, sql)
                hasil.append({"label": label, "server": addr, "val": val})
            except Exception as e:  # noqa: BLE001
                hasil.append({"label": label, "server": None, "val": {}, "error": f"{type(e).__name__}: {e}"[:160]})
        return acuan, hasil, lsn, catch

    # ------------------------------------------------------------------ jalan
    def _run(self):
        cfg, r = self.cfg, self.result
        try:
            ref, cols = self._sumber()
            r.update(mode="sebelum" if cfg.prod_on else "sesudah", acuan_label=ref[0])
            with Conn(*ref[1], timeout=30) as p:
                t1, t2 = p.query(
                    f"SELECT to_char(date_trunc('minute', localtimestamp - interval '5 minutes') - interval '{cfg.dc_minutes} minutes', 'YYYY-MM-DD HH24:MI:SS'), "
                    "to_char(date_trunc('minute', localtimestamp - interval '5 minutes'), 'YYYY-MM-DD HH24:MI:SS')")[0]
            r["rentang"] = [t1, t2]

            self._step(f"Membaca jumlah & isi data di {ref[0].lower()}, lalu menunggu node menyusul")
            acuan, hasil, lsn, catch = self._putaran(ref, cols, cfg.dc_tables, t1, t2)
            r.update(lsn_acuan=lsn, lsn_produksi=lsn, menyusul=catch)
            status = {}

            def banding(tabs, ronde):
                beda = []
                for t, _ in tabs:
                    vals = [h["val"].get(t) for h in hasil if h["server"]]
                    ok = bool(vals) and all(v == acuan.get(t) for v in vals)
                    if ok:
                        status[t] = "COCOK" if ronde == 0 else "BERUBAH"
                    else:
                        status[t] = "BEDA"
                        beda.append((t, dict(cfg.dc_tables)[t]))
                return beda

            beda = banding(cfg.dc_tables, 0)
            for u in range(1, ULANG + 1):
                if not beda:
                    break
                self._step(f"{len(beda)} tabel beda, dicek ulang ke-{u} (memisahkan data yang sedang diubah)")
                time.sleep(5)
                a2, h2, _, _ = self._putaran(ref, cols, beda, t1, t2)
                acuan.update(a2)
                for h, hb in zip(hasil, h2):
                    h["val"].update(hb["val"])
                    if hb.get("error"):
                        h["error"] = hb["error"]
                beda = banding(beda, u)

            rows = []
            for t, _ in cfg.dc_tables:
                a = acuan.get(t)
                cells, jenis = [], None
                for h in hasil:
                    v = h["val"].get(t)
                    cells.append(v[0] if v else None)
                    if v and a and v != a:
                        jenis = "jumlah" if v[0] != a[0] else (jenis or "isi")
                rows.append({"tabel": t, "acuan": a[0] if a else None, "cluster": cells,
                             "hasil": status.get(t, "BEDA"), "cocok": status.get(t) in ("COCOK", "BERUBAH"),
                             "beda": jenis if status.get(t) == "BEDA" else None})
            nbeda = sum(1 for x in rows if x["hasil"] == "BEDA")
            r.update(kolom=[{"label": h["label"], "server": h["server"], "error": h.get("error")} for h in hasil],
                     baris=rows, beda=nbeda, semua_menyusul=all(v is not None for v in catch.values()),
                     status="selesai", selesai=time.time())
            r["kesimpulan"] = (
                "Jumlah DAN isi semua data yang dicek sama, dan semua node sudah menyusul."
                if nbeda == 0 and r["semua_menyusul"] else
                "Ada perbedaan atau node yang belum menyusul (setelah dicek ulang). Jalankan sekali lagi; "
                "bila tetap, simpan hasil ini dan periksa node tersebut.")
        except Exception as e:  # noqa: BLE001
            r.update(status="gagal", error=f"{type(e).__name__}: {e}"[:300], selesai=time.time())
        finally:
            self.running = False
