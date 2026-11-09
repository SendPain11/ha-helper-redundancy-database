"""Login sederhana: dua peran (admin = penuh, lihat = hanya melihat), sesi lewat cookie."""
import hmac
import secrets
import threading
import time

SESSION_HOURS = 12
MAX_FAIL, LOCK_MIN = 5, 5


class Auth:
    def __init__(self, cfg):
        self.users = {}
        if cfg.admin_user:
            self.users[cfg.admin_user] = (cfg.admin_pass, "admin")
        if cfg.view_user and cfg.view_user != cfg.admin_user:
            self.users[cfg.view_user] = (cfg.view_pass, "lihat")
        self.sessions, self.fails = {}, {}
        self.lock = threading.Lock()

    def login(self, ip, user, pw):
        """Mengembalikan (token, peran) atau (None, pesan)."""
        now = time.time()
        with self.lock:
            n, until = self.fails.get(ip, (0, 0))
            if until > now:
                return None, f"Terlalu banyak percobaan. Coba lagi dalam {int((until - now) / 60) + 1} menit."
        u = self.users.get(user or "")
        ok = bool(u) and bool(u[0]) and hmac.compare_digest(u[0].encode(), (pw or "").encode())
        with self.lock:
            if not ok:
                n += 1
                self.fails[ip] = (0, now + LOCK_MIN * 60) if n >= MAX_FAIL else (n, 0)
                return None, "Nama pengguna atau sandi salah."
            self.fails.pop(ip, None)
            tok = secrets.token_urlsafe(32)
            self.sessions[tok] = {"user": user, "role": u[1], "exp": now + SESSION_HOURS * 3600, "ip": ip}
            return tok, u[1]

    def check(self, tok):
        if not tok:
            return None
        with self.lock:
            s = self.sessions.get(tok)
            if not s:
                return None
            if s["exp"] < time.time():
                del self.sessions[tok]
                return None
            s["exp"] = time.time() + SESSION_HOURS * 3600
            return s

    def logout(self, tok):
        with self.lock:
            self.sessions.pop(tok, None)
