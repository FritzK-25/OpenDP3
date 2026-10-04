"""Windows DPAPI (CryptProtectData/CryptUnprotectData) primitives.

Used by config.py for the Home Assistant bridge's broker password, which needs
one guarantee: a secret written to this account's application-data folder should
not be readable by another Windows account on the same machine, without pulling
in a dependency for it.

DPAPI ties the ciphertext to the current Windows user's login credentials,
with no key file to generate, protect or lose. It does not protect against
malware running as this same user, a compromised OS, or a local
administrator -- see docs/SECURITY.md. CRYPTPROTECT_UI_FORBIDDEN keeps
either call from ever blocking on a UI prompt, which matters here because
both callers can run unattended (the bridge, the Jackery CLI flow).
"""
import ctypes
from ctypes import wintypes
import os

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _run(data: bytes, crypt_name: str) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    crypt = getattr(ctypes.windll.crypt32, crypt_name)
    source = ctypes.create_string_buffer(data)
    in_blob = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _Blob()
    if not crypt(ctypes.byref(in_blob), None, None, None, None,
                 _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)):
        raise OSError("Windows DPAPI call failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def protect(data: bytes) -> bytes:
    """Encrypt ``data`` so only this Windows user's login can decrypt it."""
    return _run(data, "CryptProtectData")


def unprotect(data: bytes) -> bytes:
    """Reverse :func:`protect`. Raises OSError on any wrong-user or corrupt input."""
    return _run(data, "CryptUnprotectData")
