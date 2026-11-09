"""Klien PostgreSQL minimal tanpa pustaka tambahan (protokol v3, SCRAM-SHA-256 / MD5 / cleartext).

Hanya untuk query baca sederhana. Tidak mendukung SSL.
"""
import base64
import hashlib
import hmac
import os
import socket
import struct


class PgError(Exception):
    pass


def _msg(tag, payload=b""):
    return tag + struct.pack("!I", len(payload) + 4) + payload


class Conn:
    def __init__(self, host, port, user, password, database, timeout=5.0, app="ha-helper"):
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        self.user, self.password = user, password or ""
        params = b""
        for k, v in (("user", user), ("database", database), ("application_name", app)):
            params += k.encode() + b"\0" + str(v).encode() + b"\0"
        params += b"\0"
        payload = struct.pack("!I", 196608) + params
        self.sock.sendall(struct.pack("!I", len(payload) + 4) + payload)
        self._auth()

    # --- I/O -------------------------------------------------------------
    def _recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise PgError("koneksi ditutup server")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read(self):
        tag = self._recv(1)
        (ln,) = struct.unpack("!I", self._recv(4))
        return tag, self._recv(ln - 4)

    @staticmethod
    def _err(body):
        fields = {}
        for part in body.split(b"\0"):
            if part:
                fields[chr(part[0])] = part[1:].decode("utf-8", "replace")
        return PgError(fields.get("M", "error") + (f" ({fields['D']})" if "D" in fields else ""))

    # --- autentikasi -----------------------------------------------------
    def _auth(self):
        while True:
            tag, body = self._read()
            if tag == b"E":
                raise self._err(body)
            if tag == b"R":
                (code,) = struct.unpack("!I", body[:4])
                if code == 0:
                    continue
                if code == 3:
                    self.sock.sendall(_msg(b"p", self.password.encode() + b"\0"))
                elif code == 5:
                    salt = body[4:8]
                    inner = hashlib.md5((self.password + self.user).encode()).hexdigest()
                    outer = hashlib.md5(inner.encode() + salt).hexdigest()
                    self.sock.sendall(_msg(b"p", b"md5" + outer.encode() + b"\0"))
                elif code == 10:
                    self._scram(body[4:])
                elif code in (11, 12):
                    raise PgError("alur SCRAM tidak terduga")
                else:
                    raise PgError(f"metode autentikasi {code} tidak didukung")
            elif tag == b"Z":
                return
            # S (ParameterStatus), K (BackendKeyData), N (Notice) diabaikan

    def _scram(self, mechs):
        if b"SCRAM-SHA-256" not in mechs.split(b"\0"):
            raise PgError("server tidak menawarkan SCRAM-SHA-256")
        nonce = base64.b64encode(os.urandom(18)).decode()
        first_bare = f"n=,r={nonce}"
        first = ("n,," + first_bare).encode()
        payload = b"SCRAM-SHA-256\0" + struct.pack("!I", len(first)) + first
        self.sock.sendall(_msg(b"p", payload))
        tag, body = self._read()
        if tag == b"E":
            raise self._err(body)
        server_first = body[4:].decode()
        attrs = dict(x.split("=", 1) for x in server_first.split(","))
        rnonce, salt, iters = attrs["r"], base64.b64decode(attrs["s"]), int(attrs["i"])
        if not rnonce.startswith(nonce):
            raise PgError("nonce SCRAM tidak cocok")
        salted = hashlib.pbkdf2_hmac("sha256", self.password.encode(), salt, iters)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored = hashlib.sha256(client_key).digest()
        final_wo = f"c=biws,r={rnonce}"
        auth_msg = f"{first_bare},{server_first},{final_wo}".encode()
        sig = hmac.new(stored, auth_msg, hashlib.sha256).digest()
        proof = base64.b64encode(bytes(a ^ b for a, b in zip(client_key, sig))).decode()
        self.sock.sendall(_msg(b"p", f"{final_wo},p={proof}".encode()))
        tag, body = self._read()
        if tag == b"E":
            raise self._err(body)
        # code 12 (SASLFinal) - verifikasi server dilewati; lanjut ke AuthenticationOk di _auth

    # --- query -----------------------------------------------------------
    def query(self, sql):
        """Jalankan satu atau beberapa pernyataan. Mengembalikan baris hasil pernyataan terakhir."""
        self.sock.sendall(_msg(b"Q", sql.encode() + b"\0"))
        rows, err, cur = [], None, []
        while True:
            tag, body = self._read()
            if tag == b"T":
                cur = []
            elif tag == b"D":
                (n,) = struct.unpack("!H", body[:2])
                pos, row = 2, []
                for _ in range(n):
                    (ln,) = struct.unpack("!i", body[pos:pos + 4])
                    pos += 4
                    if ln == -1:
                        row.append(None)
                    else:
                        row.append(body[pos:pos + ln].decode("utf-8", "replace"))
                        pos += ln
                cur.append(tuple(row))
            elif tag == b"C":
                rows = cur
            elif tag == b"E":
                err = self._err(body)
            elif tag == b"Z":
                if err:
                    raise err
                return rows

    def close(self):
        try:
            self.sock.sendall(_msg(b"X"))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def lsn_to_int(s):
    if s is None:
        return None
    hi, lo = s.split("/")
    return (int(hi, 16) << 32) | int(lo, 16)
