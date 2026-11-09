"""Pengumpul data dari Patroni, etcd, HAProxy, dan produksi. Semuanya hanya membaca."""
import base64
import csv
import io
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .pgwire import Conn, lsn_to_int

POOL = ThreadPoolExecutor(max_workers=16)


def http(url, timeout, data=None, auth=None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # Patroni menjawab 503 dengan isi JSON yang tetap berguna
        return e.code, e.read().decode("utf-8", "replace")


def _try(fn, *a):
    try:
        return {"ok": True, **fn(*a)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


# ------------------------------------------------------------------ Patroni
def _patroni_node(cfg, name, ip):
    code, body = http(f"http://{ip}:{cfg.p_patroni}/patroni", cfg.timeout)
    d = json.loads(body)
    x = d.get("xlog", {}) or {}
    return {
        "role": d.get("role"), "state": d.get("state"), "timeline": d.get("timeline"),
        "replayed": x.get("replayed_location", x.get("location")),
        "received": x.get("received_location"),
        "replayed_ts": x.get("replayed_timestamp"), "replay_paused": bool(x.get("paused")),
        "pause": bool(d.get("pause")), "tags": d.get("tags", {}) or {},
        "version": (d.get("patroni") or {}).get("version"), "http": code,
    }


def _patroni_cluster(cfg, ip):
    code, body = http(f"http://{ip}:{cfg.p_patroni}/cluster", cfg.timeout)
    d = json.loads(body)
    return {"members": d.get("members", []), "pause": bool(d.get("pause")), "scope": d.get("scope"), "via": ip}


def patroni(cfg):
    nodes = {n: POOL.submit(_try, _patroni_node, cfg, n, ip) for n, ip in cfg.db_nodes}
    nodes = {n: f.result() for n, f in nodes.items()}
    cluster = None
    for n, ip in cfg.db_nodes:  # tanya /cluster ke node yang hidup
        if nodes[n]["ok"]:
            r = _try(_patroni_cluster, cfg, ip)
            if r["ok"]:
                cluster = r
                break
    return {"nodes": nodes, "cluster": cluster}


# ------------------------------------------------------------------ etcd
def _etcd_one(cfg, name, ip):
    base = f"http://{ip}:{cfg.p_etcd}"
    _, h = http(base + "/health", cfg.timeout)
    _, s = http(base + "/v3/maintenance/status", cfg.timeout, data=b"{}")
    hj, sj = json.loads(h), json.loads(s)
    return {
        "healthy": str(hj.get("health")).lower() == "true",
        "member_id": (sj.get("header") or {}).get("member_id"),
        "leader_id": sj.get("leader"), "raft_term": sj.get("raftTerm"),
        "raft_index": sj.get("raftIndex"), "db_size": int(sj.get("dbSize", 0) or 0),
        "version": sj.get("version"),
    }


def etcd(cfg):
    fut = {n: POOL.submit(_try, _etcd_one, cfg, n, ip) for n, ip in cfg.etcd_nodes}
    res = {n: f.result() for n, f in fut.items()}
    leader = None
    for n, r in res.items():
        if r["ok"] and r.get("member_id") and r.get("member_id") == r.get("leader_id"):
            leader = n
    return {"members": res, "leader": leader}


# ------------------------------------------------------------------ HAProxy
_PID = re.compile(r"pid\s*=\s*</?b?>?\s*(\d+)", re.I)
_UP = re.compile(r"uptime\s*=\s*</?b?>?\s*([0-9dhms ]+)", re.I)


def _uptime_s(txt):
    s = 0
    for num, unit in re.findall(r"(\d+)([dhms])", txt or ""):
        s += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return s


def _haproxy_one(cfg, host):
    auth = (cfg.stats_user, cfg.stats_pass) if cfg.stats_user else None
    code, body = http(f"http://{host}:{cfg.p_stats}/;csv;norefresh", cfg.timeout, auth=auth)
    if code != 200:
        raise RuntimeError(f"HTTP {code} (sandi statistik salah?)" if code == 401 else f"HTTP {code}")
    rows = list(csv.DictReader(io.StringIO(body.lstrip("# "))))
    px = {}
    for r in rows:
        p = px.setdefault(r["pxname"], {"servers": [], "backend": None})
        if r["svname"] == "BACKEND":
            p["backend"] = r.get("status")
        elif r["svname"] != "FRONTEND":
            p["servers"].append({"name": r["svname"], "status": r.get("status"), "check": r.get("check_status"),
                                 "lastchg": r.get("lastchg")})
    pid = rows[0].get("pid") if rows else None
    _, html = http(f"http://{host}:{cfg.p_stats}/", cfg.timeout, auth=auth)
    m, u = _PID.search(html), _UP.search(html)
    return {"proxies": px, "pid": (m.group(1) if m else pid), "uptime_s": _uptime_s(u.group(1) if u else "")}


def haproxy(cfg):
    hosts = [(n, ip) for n, ip in cfg.db_nodes] + [("VIP " + v, v) for v in cfg.vips]
    fut = {n: POOL.submit(_try, _haproxy_one, cfg, ip) for n, ip in hosts}
    res = {n: f.result() for n, f in fut.items()}
    vip_holder = {}
    for v in cfg.vips:
        r = res.get("VIP " + v)
        holder = None
        if r and r["ok"]:
            for n, _ in cfg.db_nodes:
                x = res[n]
                if x["ok"] and x["pid"] == r["pid"] and abs(x["uptime_s"] - r["uptime_s"]) <= 5:
                    holder = n
        vip_holder[v] = holder if (r and r["ok"]) else ("TIDAK TERJANGKAU" if r else None)
    return {"nodes": res, "vip": vip_holder}


# ------------------------------------------------------------------ produksi
def produksi(cfg):
    if not cfg.prod_on:
        return {"ok": False, "off": True}

    def _q():
        with Conn(cfg.prod_host, cfg.prod_port, cfg.prod_user, cfg.prod_pass, cfg.db, timeout=cfg.timeout + 2) as c:
            r = c.query("SELECT (CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() "
                        "ELSE pg_current_wal_lsn() END)::text, pg_is_in_recovery()::text")
            ro = c.query("SHOW default_transaction_read_only")
            return {"lsn": r[0][0], "lsn_int": lsn_to_int(r[0][0]), "recovery": r[0][1] == "true",
                    "read_only": ro[0][0] == "on"}
    return _try(_q)


def snapshot(cfg):
    t0 = time.time()
    f_pat, f_etcd = POOL.submit(patroni, cfg), POOL.submit(etcd, cfg)
    f_hap, f_prod = POOL.submit(haproxy, cfg), POOL.submit(produksi, cfg)
    return {"time": time.time(), "patroni": f_pat.result(), "etcd": f_etcd.result(),
            "haproxy": f_hap.result(), "produksi": f_prod.result(), "poll_ms": int((time.time() - t0) * 1000)}
