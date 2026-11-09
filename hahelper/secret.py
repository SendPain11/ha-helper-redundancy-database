"""Penyimpanan sandi terenkripsi di PC ini.

Windows: DPAPI (CryptProtectData) - hanya bisa dibuka oleh akun Windows yang sama di PC yang sama.
Sistem lain (hanya untuk uji): base64, tidak terenkripsi.
"""
import base64
import json
import os
import sys

_WIN = sys.platform == "win32"

if _WIN:
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32, _kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _dpapi(data, encrypt):
        buf = ctypes.create_string_buffer(data, len(data))
        inp = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = _BLOB()
        if encrypt:
            ok = _crypt32.CryptProtectData(ctypes.byref(inp), "HA Helper", None, None, None, 0x1, ctypes.byref(out))
        else:
            ok = _crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _enc(s):
    b = s.encode("utf-8")
    return base64.b64encode(_dpapi(b, True) if _WIN else b).decode()


def _dec(s):
    b = base64.b64decode(s)
    return (_dpapi(b, False) if _WIN else b).decode("utf-8")


class Store:
    def __init__(self, path):
        self.path = path

    def load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            return {k: _dec(v) for k, v in raw.items()}
        except Exception:  # noqa: BLE001  (berkas rusak / dipindah ke akun lain)
            return {}

    def save(self, values):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: _enc(v) for k, v in values.items() if v}, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def encrypted():
        return _WIN
