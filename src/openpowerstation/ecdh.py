"""Native OpenSSL secp160r1 ECDH for the device's fixed legacy wire protocol.

No Python scalar arithmetic, signing, persisted private keys, or fallback.
The native key owns its secret and is freed as soon as the exchange finishes.
OpenSSL 3's legacy EC API is needed for this curve, absent from the supported
curve set of the high-level Python crypto libraries. It remains available in
the pinned CPython Windows runtime and Debian app image.
"""
import ctypes as ct
import ctypes.util
from functools import lru_cache
from pathlib import Path
import _ssl
import sys


@lru_cache(maxsize=1)
def _native():
    # Import _ssl first so CPython loads its own crypto runtime on Windows.
    # Never search the current directory or PATH for a DLL.
    if sys.platform == 'win32':
        location = Path(_ssl.__file__).resolve().parent / 'libcrypto-3.dll'
        if not location.is_file():
            raise RuntimeError('The Python runtime is missing native OpenSSL 3.')
        library = ct.CDLL(str(location))
    else:
        location = ctypes.util.find_library('crypto')
        if not location:
            raise RuntimeError('Native OpenSSL 3 is required for the DP3 handshake.')
        library = ct.CDLL(location)
    signatures = {
        'OpenSSL_version_num': (ct.c_ulong, []),
        'OBJ_txt2nid': (ct.c_int, [ct.c_char_p]),
        'EC_KEY_new_by_curve_name': (ct.c_void_p, [ct.c_int]),
        'EC_KEY_generate_key': (ct.c_int, [ct.c_void_p]),
        'EC_KEY_free': (None, [ct.c_void_p]),
        'EC_KEY_get0_group': (ct.c_void_p, [ct.c_void_p]),
        'EC_KEY_get0_public_key': (ct.c_void_p, [ct.c_void_p]),
        'EC_POINT_new': (ct.c_void_p, [ct.c_void_p]),
        'EC_POINT_free': (None, [ct.c_void_p]),
        'EC_POINT_point2oct': (ct.c_size_t, [ct.c_void_p, ct.c_void_p, ct.c_int,
                                            ct.c_void_p, ct.c_size_t, ct.c_void_p]),
        'EC_POINT_oct2point': (ct.c_int, [ct.c_void_p, ct.c_void_p, ct.c_void_p,
                                         ct.c_size_t, ct.c_void_p]),
        'EC_POINT_is_on_curve': (ct.c_int, [ct.c_void_p, ct.c_void_p, ct.c_void_p]),
        'EC_POINT_is_at_infinity': (ct.c_int, [ct.c_void_p, ct.c_void_p]),
        'ECDH_compute_key': (ct.c_int, [ct.c_void_p, ct.c_size_t, ct.c_void_p,
                                       ct.c_void_p, ct.c_void_p]),
        'ERR_clear_error': (None, []),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
    if not 0x30000000 <= library.OpenSSL_version_num() < 0x40000000:
        raise RuntimeError('This handshake requires the qualified OpenSSL 3 API.')
    return library


class EphemeralKey:
    def __init__(self):
        self._key = None
        self._lib = _native()
        nid = self._lib.OBJ_txt2nid(b'secp160r1')
        if not nid:
            raise RuntimeError('Native crypto does not support the device curve.')
        self._key = self._lib.EC_KEY_new_by_curve_name(nid)
        if not self._key or self._lib.EC_KEY_generate_key(self._key) != 1:
            self.close()
            raise RuntimeError('Native ephemeral key generation failed.')

    def _group(self):
        if not self._key:
            raise ValueError('Ephemeral key is closed.')
        return self._lib.EC_KEY_get0_group(self._key)

    def public_bytes(self):
        group = self._group()
        output = ct.create_string_buffer(41)
        point = self._lib.EC_KEY_get0_public_key(self._key)
        count = self._lib.EC_POINT_point2oct(group, point, 4, output, 41, None)
        if count != 41 or output.raw[0] != 4:
            raise RuntimeError('Native public key has an unexpected wire format.')
        return output.raw[1:]

    def exchange(self, peer):
        group = self._group()
        if not isinstance(peer, bytes) or len(peer) != 40:
            raise ValueError('Peer key must contain two 20-byte coordinates.')
        point = self._lib.EC_POINT_new(group)
        if not point:
            raise RuntimeError('Native peer key allocation failed.')
        output = ct.create_string_buffer(20)
        try:
            encoded = b'\x04' + peer
            if (self._lib.EC_POINT_oct2point(group, point, encoded, len(encoded), None) != 1
                    or self._lib.EC_POINT_is_at_infinity(group, point) != 0
                    or self._lib.EC_POINT_is_on_curve(group, point, None) != 1):
                raise ValueError('Peer key is not a valid device-curve point.')
            count = self._lib.ECDH_compute_key(output, 20, point, self._key, None)
            if count != 20:
                raise ValueError('Native shared secret has an unexpected length.')
            return output.raw
        finally:
            ct.memset(output, 0, 20)
            self._lib.EC_POINT_free(point)
            self._lib.ERR_clear_error()

    def close(self):
        if self._key:
            self._lib.EC_KEY_free(self._key)
            self._key = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()
