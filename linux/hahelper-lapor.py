#!/usr/bin/env python3
"""HA Helper - pelapor konfigurasi node (HANYA MEMBACA).

Dijalankan cron tiap 5 menit sebagai root di setiap node cluster (DB & witness).
Mengirim RINGKASAN konfigurasi ke dashboard HA Helper: versi, nilai penting, dan sidik md5 berkas.
TIDAK mengirim sandi atau isi berkas rahasia. Tidak mengubah apa pun di node.

    /etc/hahelper-lapor.conf :  URL=http://10.10.0.30:8099
                                TOKEN=<token dari: sudo ha-helper token>
    Uji manual               :  python3 /usr/local/sbin/hahelper-lapor.py --tampil
"""
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.environ.get("LAPOR_ROOT", "")          # hanya untuk uji: awalan jalur berkas
CONF = os.environ.get("LAPOR_CONF", "/etc/hahelper-lapor.conf")
VERSI_PELAPOR = "2"
CACHE_BACKUP = "/var/cache/hahelper-lapor-backup.json"   # pgbackrest info dibaca paling sering tiap 15 menit
# cron hanya punya PATH pendek: tambahkan lokasi ufw/etcd/patroni
os.environ["PATH"] = os.environ.get("PATH", "/usr/bin:/bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin"


def baca(path):
    try:
        with open(ROOT + path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def jalan(cmd, timeout=10):
    if ROOT:  # mode uji: perintah sistem tidak dijalankan
        return None
    exe = cmd[0]
    if not (os.path.isabs(exe) and os.path.exists(exe)) and not shutil.which(exe):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception:  # noqa: BLE001
        return None


def md5(teks):
    return None if teks is None else hashlib.md5(teks.encode()).hexdigest()[:10]


def baris_bersih(teks, buang=r"^\s*(#|$)"):
    """Baris yang berarti saja (tanpa komentar/baris kosong), spasi dirapikan."""
    if teks is None:
        return None
    return [re.sub(r"\s+", " ", x.strip()) for x in teks.splitlines() if not re.match(buang, x)]


def versi(cmd, pola=r"(\d+\.\d+(?:\.\d+)*)"):
    out = jalan(cmd)
    m = re.search(pola, out or "")
    return m.group(1) if m else None


def aktif(svc):
    out = jalan(["systemctl", "is-active", svc])
    return out.splitlines()[0] if out else None


def yaml_sederhana(teks):
    """Ambil nilai kunci 'a.b.c' dari YAML sederhana tanpa pustaka (cukup untuk etcd/patroni)."""
    hasil, jalur = {}, []
    for raw in (teks or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        ind = len(raw) - len(raw.lstrip())
        m = re.match(r"\s*([A-Za-z0-9_\-]+)\s*:\s*(.*)$", raw)
        if not m:
            if raw.strip().startswith("- "):
                k = ".".join(p for _, p in jalur)
                hasil.setdefault(k, [])
                if isinstance(hasil[k], list):
                    hasil[k].append(raw.strip()[2:].strip().strip("'\""))
            continue
        while jalur and jalur[-1][0] >= ind:
            jalur.pop()
        jalur.append((ind, m.group(1)))
        v = m.group(2).split(" #")[0].strip().strip("'\"")
        if v:
            hasil[".".join(p for _, p in jalur)] = v
    return hasil


def kumpul():
    L = {}

    def taruh(k, v):
        if v is not None and v != "":
            L[k] = v

    # ---------------------------------------------------------------- sistem
    host = os.environ.get("LAPOR_HOST") or socket.gethostname()
    taruh("sistem.hostname", host)
    osr = baca("/etc/os-release") or ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)', osr, re.M)
    taruh("sistem.os", m.group(1) if m else None)
    td = jalan(["timedatectl", "show", "-p", "Timezone", "-p", "NTPSynchronized"]) or ""
    for kv in td.splitlines():
        k, _, v = kv.partition("=")
        if k == "Timezone":
            taruh("sistem.zona_waktu", v)
        if k == "NTPSynchronized":
            taruh("sistem.jam_sinkron", "ya" if v == "yes" else "TIDAK")
    if "sistem.zona_waktu" not in L:
        lt = os.path.realpath(ROOT + "/etc/localtime") if os.path.exists(ROOT + "/etc/localtime") else ""
        taruh("sistem.zona_waktu", lt.split("zoneinfo/")[-1] if "zoneinfo/" in lt else (baca("/etc/timezone") or "").strip())
    taruh("sistem.python", "%d.%d" % sys.version_info[:2])
    ips = os.environ.get("LAPOR_IP") or jalan(["hostname", "-I"])
    taruh("sistem.ip", " ".join(sorted(x for x in (ips or "").split() if ":" not in x)))

    # ---------------------------------------------------------------- /etc/hosts (baris selain localhost)
    h = baris_bersih(baca("/etc/hosts"))
    if h is not None:
        ch = sorted(x for x in h if not re.match(r"^(127\.|::1|ff0|fe0)", x))
        taruh("hosts.isi_md5", md5("\n".join(ch)))
        taruh("hosts.jumlah_baris", len(ch))
        taruh("hosts.nama", ", ".join(sorted({p for x in ch for p in x.split()[1:]})))

    # ---------------------------------------------------------------- etcd
    et_txt = baca("/etc/etcd/etcd.conf.yaml")
    if et_txt is not None:
        y = yaml_sederhana(et_txt)
        ic = sorted((y.get("initial-cluster") or "").split(","))
        taruh("etcd.initial_cluster", ", ".join(x.split("=")[0] for x in ic if x))
        taruh("etcd.initial_cluster_md5", md5(",".join(ic)))
        taruh("etcd.initial_cluster_state", y.get("initial-cluster-state"))
        taruh("etcd.cluster_token", y.get("initial-cluster-token"))
        taruh("etcd.compaction", ("%s %s" % (y.get("auto-compaction-mode", "-"), y.get("auto-compaction-retention", "-"))))
        taruh("etcd.data_dir", y.get("data-dir"))
        taruh("etcd.layanan", aktif("etcd"))
        taruh("etcd.versi", versi(["etcd", "--version"]))
        taruh("etcd.nama", y.get("name"))
    # /etc/default/etcd (berkas lingkungan paket Debian/Ubuntu) - ikut dicek agar tidak ada sisa anggota lama
    de = baca("/etc/default/etcd")
    if de is not None:
        aktif_ = [x for x in baris_bersih(de) if x.startswith("ETCD_INITIAL_CLUSTER=")]
        if aktif_:
            v = aktif_[-1].split("=", 1)[1].strip().strip("'\"")
            taruh("etcd.default_initial_cluster", ", ".join(sorted(x.split("=")[0] for x in v.split(",") if x)))
        else:
            taruh("etcd.default_initial_cluster", "- (tidak diisi)")
        # nama anggota yang disebut: "nama=http://..." di ETCD_INITIAL_CLUSTER dan nilai ETCD_NAME
        teks = "\n".join(baris_bersih(de))
        sisa = sorted(set(re.findall(r"([A-Za-z0-9_.-]+)=https?://", teks))
                      | set(re.findall(r"^ETCD_NAME=['\"]?([^'\"\s]+)", teks, re.M)))
        taruh("etcd.default_nama_disebut", ", ".join(sisa) or "-")
    # etcd yang sedang berjalan membaca konfigurasi dari mana
    pid = (jalan(["pgrep", "-xo", "etcd"]) or "").split()
    if pid and pid[0].isdigit():
        try:
            with open(f"/proc/{pid[0]}/cmdline", "rb") as f:
                cmd = f.read().decode(errors="replace").split("\0")
            cf = next((cmd[i + 1] for i, a in enumerate(cmd) if a == "--config-file" and i + 1 < len(cmd)),
                      next((a.split("=", 1)[1] for a in cmd if a.startswith("--config-file=")), None))
            with open(f"/proc/{pid[0]}/environ", "rb") as f:
                env = [x for x in f.read().decode(errors="replace").split("\0") if x.startswith("ETCD_")]
            taruh("etcd.sumber_konfig", f"--config-file {cf}" if cf else
                  ("variabel ETCD_* (/etc/default/etcd)" if env else "flag baris perintah"))
        except OSError:
            pass
    # /etc/default/etcd (berkas lingkungan paket Debian). Hanya dipakai bila unit systemd memuatnya,
    # tapi isinya harus tetap benar supaya tidak menyesatkan saat etcd dipasang ulang.
    ed = baca("/etc/default/etcd")
    if ed is not None:
        bb = [x for x in baris_bersih(ed) if "=" in x]
        taruh("etcd.default_md5", md5("\n".join(sorted(bb))) if bb else "kosong")
        ic = next((x.split("=", 1)[1].strip().strip("'\"") for x in bb if x.startswith("ETCD_INITIAL_CLUSTER=")), None)
        taruh("etcd.default_initial_cluster", ", ".join(sorted(x.split("=")[0] for x in ic.split(",") if x)) if ic else "-")
    unit = jalan(["systemctl", "cat", "etcd"])
    if unit and "No files found" not in unit:
        ex = " ".join(x.split("=", 1)[1].strip() for x in unit.splitlines() if x.startswith(("ExecStart=", "EnvironmentFile=")))
        taruh("etcd.unit", ("memakai /etc/default/etcd; " if "/etc/default/etcd" in ex else "") +
              ("--config-file" if "config-file" in ex or "ETCD_CONFIG_FILE" in unit else "tanpa --config-file"))

    # ---------------------------------------------------------------- Patroni + PostgreSQL
    pa_txt = baca("/etc/patroni/config.yml")
    if pa_txt is not None:
        y = yaml_sederhana(pa_txt)
        hosts = y.get("etcd3.hosts") or y.get("etcd.hosts") or ""
        if isinstance(hosts, list):
            hosts = ",".join(hosts)
        taruh("patroni.scope", y.get("scope"))
        taruh("patroni.namespace", y.get("namespace", "/service/"))
        taruh("patroni.etcd_hosts", ", ".join(sorted(x.strip() for x in hosts.split(",") if x.strip())))
        taruh("patroni.data_dir", y.get("postgresql.data_dir"))
        taruh("patroni.bin_dir", y.get("postgresql.bin_dir"))
        taruh("patroni.porta_pg", (y.get("postgresql.listen") or "").rsplit(":", 1)[-1])
        taruh("patroni.porta_rest", (y.get("restapi.listen") or "").rsplit(":", 1)[-1])
        tags = {k.split(".", 1)[1]: v for k, v in y.items() if k.startswith("tags.")}
        taruh("patroni.tags", ", ".join(f"{k}={v}" for k, v in sorted(tags.items())) or "-")
        taruh("patroni.layanan", aktif("patroni"))
        taruh("patroni.versi", versi(["patroni", "--version"]))
        pg = sorted(glob.glob(ROOT + "/usr/lib/postgresql/*/bin/postgres"))
        if pg:
            taruh("postgresql.versi", versi([pg[-1].replace(ROOT, "", 1), "-V"]) or os.path.basename(os.path.dirname(os.path.dirname(pg[-1]))))

    # ---------------------------------------------------------------- HAProxy
    ha = baca("/etc/haproxy/haproxy.cfg")
    if ha is not None:
        taruh("haproxy.cfg_md5", md5("\n".join(baris_bersih(ha))))
        taruh("haproxy.server", ", ".join(sorted({re.sub(r"\s+maxconn.*|\s+check.*", "", x)
                                                  for x in baris_bersih(ha) if x.startswith("server ")})))
        taruh("haproxy.layanan", aktif("haproxy"))
        taruh("haproxy.versi", versi(["haproxy", "-v"]))

    # ---------------------------------------------------------------- keepalived
    def ka_md5(bb):
        # buang yang memang beda per node: prioritas, state, IP sendiri/peer (baris IP saja); VIP dicek terpisah
        return md5("\n".join(x for x in bb if not re.match(r"^(priority|state|unicast_src_ip|router_id) ", x)
                             and not re.match(r"^\d+\.\d+\.\d+\.\d+(/\d+)?( .*)?$", x)))

    ka = baca("/etc/keepalived/keepalived.conf")
    if ka is not None:
        bb = baris_bersih(ka)
        taruh("keepalived.layanan", aktif("keepalived"))
        taruh("keepalived.prioritas", " ".join(x.split()[1] for x in bb if x.startswith("priority ")))
        taruh("keepalived.vrid", " ".join(x.split()[1] for x in bb if x.startswith("virtual_router_id ")))
        vip = re.findall(r"virtual_ipaddress\s*\{([^}]*)\}", ka)
        taruh("keepalived.vip", " ".join(" ".join(baris_bersih(v)) for v in vip))
        taruh("keepalived.state", " ".join(x.split()[1] for x in bb if x.startswith("state ")))
        taruh("keepalived.tanpa_prioritas_md5", ka_md5(bb))
    kp = baca("/etc/keepalived/keepalived.conf.pelaksanaan")
    if kp is not None:
        vip = re.findall(r"virtual_ipaddress\s*\{([^}]*)\}", kp)
        taruh("keepalived.hari_h_vip", " ".join(" ".join(baris_bersih(v)) for v in vip))
        taruh("keepalived.hari_h_prioritas", " ".join(x.split()[1] for x in baris_bersih(kp) if x.startswith("priority ")))
        taruh("keepalived.hari_h_md5", ka_md5(baris_bersih(kp)))

    # ---------------------------------------------------------------- backup
    pb = baca("/etc/pgbackrest.conf")
    if pb is not None:
        bb = baris_bersih(pb)
        taruh("backup.pgbackrest_conf_md5", md5("\n".join(bb)))
        taruh("backup.stanza", ", ".join(re.findall(r"^\[([^\]]+)\]", "\n".join(bb), re.M)).replace("global, ", "").replace("global", "") or "-")
        taruh("backup.retensi_full", next((x.split("=", 1)[1] for x in bb if x.startswith("repo1-retention-full=")), "-"))
        taruh("backup.pgbackrest_versi", versi(["pgbackrest", "version"]))
        cr = jalan(["crontab", "-l", "-u", "postgres"])
        if cr is not None and "no crontab" in cr:
            cr = ""
        if not ROOT:
            taruh("backup.cron_md5", md5("\n".join(baris_bersih(cr))) if cr else "tidak ada")
            taruh("backup.cron_jadwal", " | ".join(" ".join(x.split()[:5]) + " " + ("full" if "type=full" in x else "incr" if "type=incr" in x else "?")
                                                    for x in baris_bersih(cr or "") if "pgbackrest" in x) or "-")
        fs = [x for x in (baris_bersih(baca("/etc/fstab")) or []) if " /mnt/nas " in f" {x} "]
        taruh("backup.nas_fstab_md5", md5(fs[0]) if fs else "tidak ada")
        mnt = jalan(["findmnt", "-n", "-o", "SOURCE", "/mnt/nas"])
        if not ROOT:
            taruh("backup.nas_terpasang", mnt or "TIDAK")

    # ---------------------------------------------------------------- ufw
    ufw = jalan(["ufw", "show", "added"])
    if ufw is not None:
        rules = [x[4:] for x in ufw.splitlines() if x.startswith("ufw ")]
        st = (jalan(["ufw", "status"]) or "").splitlines()
        taruh("ufw.status", st[0].replace("Status: ", "") if st else None)
        taruh("ufw.jumlah_aturan", len(rules))
        taruh("ufw.tanpa_komentar", sum(1 for r in rules if " comment " not in r))
        taruh("ufw.tanpa_sumber", sum(1 for r in rules if r.startswith("allow") and " from " not in r))
        ips = [re.search(r" from (\S+)", r).group(1) for r in rules if " from " in r]
        taruh("ufw.ip_ganda", sum(1 for i in set(ips) if ips.count(i) > 1))
        taruh("ufw.aturan_md5", md5("\n".join(sorted(rules))))

    return host, L


def _pgbackrest_info():
    """pgbackrest info --output=json sebagai user postgres (hanya membaca repositori). Disimpan sementara 15 menit."""
    if os.environ.get("LAPOR_PGBR_JSON"):  # mode uji
        with open(os.environ["LAPOR_PGBR_JSON"]) as f:
            return json.load(f), None
    try:
        if time.time() - os.path.getmtime(CACHE_BACKUP) < 900:
            with open(CACHE_BACKUP) as f:
                c = json.load(f)
            return c["info"], c.get("galat")
    except (OSError, ValueError, KeyError):
        pass
    cmd = ["pgbackrest", "info", "--output=json", "--log-level-console=off", "--log-level-file=off"]
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", "postgres", "--"] + cmd
    info, galat = None, None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        info = json.loads(r.stdout) if r.stdout.strip() else None
        if info is None:
            galat = (r.stderr or "tidak ada keluaran").strip()[:300]
    except Exception as e:  # noqa: BLE001
        galat = f"{type(e).__name__}: {e}"[:300]
    try:
        os.makedirs(os.path.dirname(CACHE_BACKUP), exist_ok=True)
        with open(CACHE_BACKUP, "w") as f:
            json.dump({"info": info, "galat": galat}, f)
    except OSError:
        pass
    return info, galat


def kumpul_backup():
    """Ringkasan backup untuk tab Backup. Hanya di node yang punya /etc/pgbackrest.conf."""
    if baca("/etc/pgbackrest.conf") is None:
        return None
    out = {"waktu": time.time()}
    info, galat = _pgbackrest_info()
    if galat:
        out["galat"] = galat
    st = []
    for s in info or []:
        bk = []
        for b in (s.get("backup") or [])[-40:]:
            i = b.get("info") or {}
            bk.append({"label": b.get("label"), "jenis": b.get("type"),
                       "mulai": (b.get("timestamp") or {}).get("start"), "selesai": (b.get("timestamp") or {}).get("stop"),
                       "ukuran": i.get("size"), "ukuran_repo": (i.get("repository") or {}).get("size"),
                       "wal_awal": (b.get("archive") or {}).get("start"), "wal_akhir": (b.get("archive") or {}).get("stop"),
                       "galat": bool(b.get("error"))})
        st.append({"nama": s.get("name"), "kode": (s.get("status") or {}).get("code"),
                   "pesan": (s.get("status") or {}).get("message"), "backup": bk,
                   "arsip": [{"id": a.get("id"), "min": a.get("min"), "max": a.get("max")} for a in (s.get("archive") or [])]})
    out["stanza"] = st
    nas = os.environ.get("LAPOR_NAS", "/mnt/nas")
    if os.path.ismount(nas) or os.environ.get("LAPOR_NAS"):
        try:
            v = os.statvfs(nas)
            out["nas"] = {"total": v.f_blocks * v.f_frsize, "sisa": v.f_bavail * v.f_frsize}
        except OSError as e:
            out["nas"] = {"galat": str(e)[:120]}
    else:
        out["nas"] = {"galat": "/mnt/nas tidak terpasang"}
    lg = ROOT + "/var/log/pgbackrest/cron.log"
    try:
        with open(lg, "rb") as f:
            f.seek(max(0, os.path.getsize(lg) - 65536))
            baris = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
        err = [x for x in baris if "ERROR" in x]
        out["cron_log"] = {"ubah": os.path.getmtime(lg), "terakhir": baris[-1][:220] if baris else "",
                           "galat_terakhir": err[-1][:220] if err else ""}
    except OSError:
        out["cron_log"] = None
    return out


def main():
    host, L = kumpul()
    data = {"host": host, "waktu": time.time(), "pelapor": VERSI_PELAPOR, "isi": L}
    try:
        b = kumpul_backup()
        if b:
            data["backup"] = b
            if "--tampil" in sys.argv:
                print("backup:", ", ".join(f"{s['nama']} ({len(s['backup'])} backup, {s['pesan']})" for s in b["stanza"])
                      or b.get("galat", "-"))
    except Exception as e:  # noqa: BLE001
        data["backup"] = {"waktu": time.time(), "galat": f"{type(e).__name__}: {e}"[:200], "stanza": []}
    if "--tampil" in sys.argv:
        for k in sorted(L):
            print(f"{k:<34} {L[k]}")
        if "--kirim" not in sys.argv:
            return
    conf = {}
    for line in (baca(CONF) if not ROOT else open(CONF).read() if os.path.exists(CONF) else "").splitlines():
        k, _, v = line.partition("=")
        conf[k.strip()] = v.strip()
    if not conf.get("URL") or not conf.get("TOKEN"):
        sys.exit(f"{CONF} belum berisi URL= dan TOKEN=")
    req = urllib.request.Request(conf["URL"].rstrip("/") + "/api/konfig/lapor", data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json", "X-Token": conf["TOKEN"]}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            out = r.read().decode()
        if "--tampil" in sys.argv:
            print("\nterkirim:", out)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"gagal mengirim ke {conf['URL']}: {e}")


if __name__ == "__main__":
    main()
