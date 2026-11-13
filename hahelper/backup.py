"""Ringkasan backup (pgBackRest di NAS) dari laporan pelapor di node database. Hanya membaca.

Sebelum cutover yang dinilai: stanza lama ([backup] stanza_lama, backup server lama).
Sesudah cutover            : stanza cluster ([backup] stanza_cluster).
"""
import time

from .diagnose import F

JAM = 3600


def _umur(ts, now):
    return None if not ts else int(now - ts)


def _jam(d):
    if d is None:
        return "-"
    return f"{d // 60} menit" if d < 2 * JAM else f"{d / JAM:.0f} jam" if d < 72 * JAM else f"{d / 86400:.0f} hari"


def ringkas_stanza(s, now):
    bk = s.get("backup") or []
    full = [b for b in bk if b.get("jenis") == "full"]
    last = bk[-1] if bk else None
    lf = full[-1] if full else None
    return {
        "nama": s.get("nama"), "kode": s.get("kode"), "pesan": s.get("pesan"),
        "jumlah": {"full": len(full), "diff": sum(1 for b in bk if b.get("jenis") == "diff"),
                   "incr": sum(1 for b in bk if b.get("jenis") == "incr")},
        "terakhir": last, "umur_terakhir": _umur(last and last.get("selesai"), now),
        "full_terakhir": lf, "umur_full": _umur(lf and lf.get("selesai"), now),
        "tertua": bk[0] if bk else None,
        "ukuran_repo": sum(b.get("ukuran_repo") or 0 for b in bk),
        "galat": [b["label"] for b in bk if b.get("galat")],
        "arsip": s.get("arsip") or [],
        "backup": list(reversed(bk[-20:])),
    }


def _k(f, kunci):
    f["kunci"] = kunci  # judul berubah tiap jam; kunci tetap supaya riwayat tidak dibanjiri
    return f


def nilai(cfg, laporan, state):
    """laporan: {node: data pelapor}. state: ringkasan pemantau terakhir. -> (data tab Backup, temuan)."""
    now = time.time()
    sesudah = not cfg.prod_on
    aktif = cfg.stanza_cluster if sesudah else cfg.stanza_lama
    out, temuan = {"mode": "sesudah" if sesudah else "sebelum", "aktif": aktif, "node": [],
                  "stanza_cluster": cfg.stanza_cluster, "stanza_lama": cfg.stanza_lama}, []

    db = {n for n, _ in cfg.db_nodes}
    ada = {n: r for n, r in laporan.items() if n in db and isinstance(r.get("backup"), dict)}
    for n in sorted(db):
        r = ada.get(n)
        b = (r or {}).get("backup") or {}
        out["node"].append({"node": n, "umur": _umur(b.get("waktu"), now) if r else None,
                            "nas": b.get("nas"), "cron_log": b.get("cron_log"), "galat": b.get("galat"),
                            "jadwal": ((r or {}).get("isi") or {}).get("backup.cron_jadwal")})
    if not ada:
        temuan.append(F("info", "Backup", "Status backup belum terbaca",
                        "Pasang pelapor di db-node1 dan db-node2 agar status pgBackRest, NAS dan cron tampil.",
                        "sudo bash pasang-lapor.sh <URL-helper> <token>"))
        return out, temuan

    # pakai laporan backup terbaru yang berhasil membaca repositori
    src = max(ada.items(), key=lambda kv: (bool(kv[1]["backup"].get("stanza")), kv[1]["backup"].get("waktu") or 0))
    b = src[1]["backup"]
    out["sumber"] = {"node": src[0], "umur": _umur(b.get("waktu"), now)}
    out["stanza"] = [ringkas_stanza(s, now) for s in b.get("stanza") or []]
    if b.get("galat") and not out["stanza"]:
        temuan.append(F("warn", "Backup", f"pgbackrest info gagal dibaca di {src[0]}", b["galat"][:200],
                        "sudo -u postgres pgbackrest info"))

    # --- stanza yang sedang dinilai
    st = next((s for s in out["stanza"] if s["nama"] == aktif), None)
    label = f"Backup cluster ({aktif})" if sesudah else f"Backup produksi ({aktif})"
    if b.get("stanza") is not None and not st and not b.get("galat"):
        if sesudah:
            temuan.append(F("crit", "Backup", f"Stanza {aktif} belum ada di NAS",
                            "Cluster sudah jadi utama tapi backup belum dinyalakan.", f"Hari-H Langkah 6B: pgbackrest --stanza={aktif} stanza-create"))
        else:
            temuan.append(F("warn", "Backup", f"Stanza {aktif} tidak ditemukan di NAS", "", "cek mount /mnt/nas"))
    if st:
        if st["kode"] not in (0, None):
            lvl = "warn"
            judul = f"{label}: {st['pesan']}"
            if st["kode"] == 2 and sesudah:
                judul = f"{label}: belum ada backup sama sekali"
            temuan.append(F(lvl, "Backup", judul, "", "Hari-H Langkah 12: full backup pertama" if sesudah else ""))
        u = st["umur_terakhir"]
        if u is not None:
            if u > 50 * JAM:
                temuan.append(_k(F("crit", "Backup", f"{label}: backup terakhir {_jam(u)} lalu",
                                   "Backup harian berhenti.", "tail -20 /var/log/pgbackrest/cron.log  (di node pemimpin)"),
                                 f"{label}: backup harian berhenti (>2 hari)"))
            elif u > 26 * JAM:
                temuan.append(_k(F("warn", "Backup", f"{label}: backup terakhir {_jam(u)} lalu",
                                   "Seharusnya ada backup tiap malam pukul 23:00.", "tail -20 /var/log/pgbackrest/cron.log"),
                                 f"{label}: backup harian terlambat (>26 jam)"))
            else:
                t = st["terakhir"]
                temuan.append(F("ok", "Backup", f"{label}: {t['jenis']} terakhir {_jam(u)} lalu",
                                f"{st['jumlah']['full']} full, {st['jumlah']['incr']} incr tersimpan"))
        if st["umur_full"] is not None and st["umur_full"] > 8 * 86400:
            temuan.append(_k(F("warn", "Backup", f"{label}: full terakhir {_jam(st['umur_full'])} lalu",
                               "Full seharusnya tiap Minggu."), f"{label}: full mingguan terlambat"))
        if st["galat"]:
            temuan.append(F("warn", "Backup", f"{label}: backup dengan galat halaman: {', '.join(st['galat'][-3:])}"))

    # --- NAS
    for n in out["node"]:
        nas = n.get("nas") or {}
        if nas.get("total"):
            pakai = 100 - nas["sisa"] * 100 / nas["total"]
            nas["pakai_persen"] = round(pakai, 1)
    nas = next((n["nas"] for n in out["node"] if (n.get("nas") or {}).get("total")), None)
    if nas:
        p = nas["pakai_persen"]
        if p > 95:
            temuan.append(_k(F("crit", "Backup", f"NAS backup hampir penuh: {p}%", "Backup berikut bisa gagal."), "NAS > 95%"))
        elif p > 85:
            temuan.append(_k(F("warn", "Backup", f"NAS backup terisi {p}%", "Periksa retensi / kosongkan #recycle."), "NAS > 85%"))
    for n in out["node"]:
        if (n.get("nas") or {}).get("galat"):
            temuan.append(F("warn", "Backup", f"{n['node']}: {n['nas']['galat']}", "", "mount /mnt/nas"))
        cl = n.get("cron_log") or {}
        if cl.get("terakhir") and "ERROR" in cl["terakhir"]:
            temuan.append(F("warn", "Backup", f"{n['node']}: backup terjadwal terakhir GAGAL", cl["terakhir"][:200],
                            "tail -20 /var/log/pgbackrest/cron.log"))

    # --- arsip WAL (dibaca langsung dari database)
    st_ = state or {}
    out["arsip"] = {"produksi": ((st_.get("produksi") or {}).get("arsip")), "cluster": ((st_.get("utama") or {}).get("arsip"))}
    pa = out["arsip"]["produksi"]
    if not sesudah and pa:
        if pa["gagal_umur_detik"] is not None and (pa["umur_detik"] is None or pa["gagal_umur_detik"] < pa["umur_detik"]):
            temuan.append(F("warn", "Backup", "Arsip WAL produksi GAGAL", f"WAL {pa['gagal_wal']}", "cek NAS dan log PostgreSQL produksi"))
        elif pa["umur_detik"] is not None and pa["umur_detik"] > JAM:
            temuan.append(F("info", "Backup", f"Arsip WAL produksi terakhir {_jam(pa['umur_detik'])} lalu"))
    return out, temuan
