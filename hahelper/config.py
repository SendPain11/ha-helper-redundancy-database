"""Membaca config.ini dan menanyakan sandi yang dikosongkan."""
import configparser
import getpass


class Cfg:
    pass


def _list(sec):
    """Baris 'nama = ip' di satu bagian -> list (nama, ip)."""
    return [(k, v.strip()) for k, v in sec.items()]


def load(path, ask=True):
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    cp.optionxform = str  # pertahankan huruf besar-kecil nama node
    with open(path, encoding="utf-8-sig") as f:
        cp.read_file(f)
    c = Cfg()
    c.path = path
    g = cp["umum"]
    c.listen = g.get("alamat_dashboard", "127.0.0.1")
    c.port = g.getint("port_dashboard", 8099)
    c.poll = g.getint("interval_detik", 5)
    c.lag_mb_warn = g.getfloat("batas_lag_mb", 16)
    c.preferred = g.get("pemimpin_utama", "").strip()
    c.timeout = g.getfloat("timeout_detik", 2.0)

    c.db_nodes = _list(cp["node_database"])
    c.etcd_nodes = _list(cp["anggota_etcd"])
    p = cp["porta"]
    c.p_patroni, c.p_etcd, c.p_stats = p.getint("patroni", 8008), p.getint("etcd", 2379), p.getint("statistik_haproxy", 7000)
    c.p_tulis, c.p_baca = p.getint("haproxy_tulis", 5432), p.getint("haproxy_baca", 5434)

    h = cp["haproxy"]
    c.stats_user, c.stats_pass = h.get("pengguna", "admin"), h.get("sandi", "")
    c.vips = [v.strip() for v in h.get("alamat_vip", "").split(",") if v.strip()]

    pr = cp["produksi"]
    c.prod_on = pr.getboolean("aktif", True)
    c.prod_host, c.prod_port = pr.get("alamat", ""), pr.getint("porta", 5432)
    c.prod_user, c.prod_pass, c.db = pr.get("pengguna", "postgres"), pr.get("sandi", ""), pr.get("database", "postgres")

    cl = cp["cluster"]
    c.cl_host = cl.get("alamat_haproxy", c.db_nodes[0][1] if c.db_nodes else "")
    c.cl_user, c.cl_pass = cl.get("pengguna", c.prod_user), cl.get("sandi", "")
    c.cl_same = cl.getboolean("sandi_sama_dengan_produksi", True)

    d = cp["cek_data"]
    c.dc_minutes = d.getint("rentang_menit", 60)
    c.dc_tables = [(k, v.strip()) for k, v in cp["tabel_cek_data"].items()]

    e = cp["email"]
    c.mail_on = e.getboolean("aktif", False)
    c.mail_host, c.mail_port = e.get("server", ""), e.getint("porta", 587)
    c.mail_tls = e.getboolean("starttls", True)
    c.mail_user, c.mail_pass = e.get("pengguna", ""), e.get("sandi", "")
    c.mail_from = e.get("pengirim", c.mail_user)
    c.mail_to = [x.strip() for x in e.get("penerima", "").split(",") if x.strip()]
    c.mail_min = e.get("kirim_mulai_level", "warn")

    if ask:
        if c.stats_user and not c.stats_pass:
            c.stats_pass = getpass.getpass("Sandi halaman statistik HAProxy (stats auth): ")
        if c.prod_on and not c.prod_pass:
            c.prod_pass = getpass.getpass(f"Sandi '{c.prod_user}' di PRODUKSI {c.prod_host}: ")
        if not c.cl_pass:
            c.cl_pass = c.prod_pass if (c.cl_same and c.prod_on) else getpass.getpass(
                f"Sandi '{c.cl_user}' di CLUSTER {c.cl_host}: ")
        if c.mail_on and c.mail_user and not c.mail_pass:
            c.mail_pass = getpass.getpass(f"Sandi aplikasi email {c.mail_user}: ")
    return c


def public(c):
    """Isi konfigurasi tanpa sandi, untuk ditampilkan di dashboard."""
    return {
        "node_database": c.db_nodes, "anggota_etcd": c.etcd_nodes, "vip": c.vips,
        "pemimpin_utama": c.preferred, "interval": c.poll, "batas_lag_mb": c.lag_mb_warn,
        "produksi": f"{c.prod_host}:{c.prod_port}" if c.prod_on else "tidak aktif",
        "cluster_haproxy": f"{c.cl_host}:{c.p_tulis}", "database": c.db,
        "email": (", ".join(c.mail_to) if c.mail_on else "tidak aktif"),
        "tabel_cek_data": [t for t, _ in c.dc_tables], "rentang_cek_data_menit": c.dc_minutes,
    }
