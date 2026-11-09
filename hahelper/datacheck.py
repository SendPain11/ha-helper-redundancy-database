"""Cek streaming & data: data baru di produksi sudah sampai ke cluster atau belum. Hanya membaca."""
import json
import threading
import time

from . import collect
from .pgwire import Conn, PgError, lsn_to_int


def _count_sql(tables, t1, t2):
    parts = [f"SELECT '{t}'::text, count(*)::text FROM public.{t} WHERE {c} >= '{t1}' AND {c} < '{t2}'"
             for t, c in tables]
    return " UNION ALL ".join(parts)


def _replayed(cfg, ip):
    _, body = collect.http(f"http://{ip}:{cfg.p_patroni}/patroni", 2)
    x = (json.loads(body).get("xlog") or {})
    v = x.get("replayed_location", x.get("location"))
    return v if isinstance(v, int) else lsn_to_int(v)


def _cluster_counts(cfg, port, sql):
    last = None
    for _ in range(3):  # query di standby bisa dibatalkan karena konflik replikasi; coba ulang
        try:
            with Conn(cfg.cl_host, port, cfg.cl_user, cfg.cl_pass, cfg.db, timeout=30) as c:
                addr = c.query("SELECT coalesce(inet_server_addr()::text,'lokal')")[0][0]
                return addr.split("/")[0], dict(c.query(sql))
        except (PgError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


class DataCheck:
    def __init__(self, cfg):
        self.cfg, self.result, self.running, self.lock = cfg, None, False, threading.Lock()

    def start(self):
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.result = {"status": "berjalan", "mulai": time.time(), "langkah": "Menyambung ke produksi"}
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _step(self, s):
        self.result["langkah"] = s

    def _run(self):
        cfg, r = self.cfg, self.result
        try:
            with Conn(cfg.prod_host, cfg.prod_port, cfg.prod_user, cfg.prod_pass, cfg.db, timeout=30) as p:
                t1, t2 = p.query(
                    f"SELECT to_char(date_trunc('minute', localtimestamp - interval '5 minutes') - interval '{cfg.dc_minutes} minutes', 'YYYY-MM-DD HH24:MI:SS'), "
                    "to_char(date_trunc('minute', localtimestamp - interval '5 minutes'), 'YYYY-MM-DD HH24:MI:SS')")[0]
                sql = _count_sql(cfg.dc_tables, t1, t2)
                self._step("Menghitung data baru di produksi")
                prod = dict(p.query(sql))
                lsn = p.query("SELECT (CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_lsn() END)::text")[0][0]
            r.update(rentang=[t1, t2], lsn_produksi=lsn)
            target = lsn_to_int(lsn)

            self._step("Menunggu semua node menyusul posisi produksi")
            t0, catch = time.time(), {}
            pending = dict(cfg.db_nodes)
            while pending and time.time() - t0 <= 120:
                for name, ip in list(pending.items()):
                    try:
                        v = _replayed(cfg, ip)
                        if v is not None and v >= target:
                            catch[name] = round(time.time() - t0, 1)
                            del pending[name]
                    except Exception:  # noqa: BLE001
                        pass
                if pending:
                    time.sleep(1)
            for name in pending:
                catch[name] = None
            r["menyusul"] = catch

            self._step("Menghitung data yang sama di cluster")
            cols = []
            for label, port in (("Jalur tulis (pemimpin)", cfg.p_tulis), ("Jalur baca (replica)", cfg.p_baca)):
                try:
                    addr, counts = _cluster_counts(cfg, port, sql)
                    cols.append({"label": label, "server": addr, "counts": counts})
                except Exception as e:  # noqa: BLE001
                    cols.append({"label": label, "server": None, "counts": {}, "error": f"{type(e).__name__}: {e}"[:160]})
            rows, beda = [], 0
            for t, _ in cfg.dc_tables:
                vals = [c["counts"].get(t) for c in cols if c["server"]]
                ok = all(v == prod.get(t) for v in vals) and bool(vals)
                beda += 0 if ok else 1
                rows.append({"tabel": t, "produksi": prod.get(t), "cluster": [c["counts"].get(t) for c in cols], "cocok": ok})
            r.update(kolom=[{"label": c["label"], "server": c["server"], "error": c.get("error")} for c in cols],
                     baris=rows, beda=beda,
                     semua_menyusul=all(v is not None for v in catch.values()),
                     status="selesai", selesai=time.time())
            r["kesimpulan"] = (
                "Semua data baru sudah sampai, dan semua node sudah melewati posisi produksi."
                if beda == 0 and r["semua_menyusul"] else
                "Ada perbedaan atau node yang belum menyusul. Jalankan sekali lagi; bila tetap sama, periksa node tersebut.")
        except Exception as e:  # noqa: BLE001
            r.update(status="gagal", error=f"{type(e).__name__}: {e}"[:300], selesai=time.time())
        finally:
            self.running = False
