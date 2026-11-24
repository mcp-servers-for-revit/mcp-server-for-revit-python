# -*- coding: utf-8 -*-
"""Per-Windows-user pairing, shared by CPython and IronPython.

The bearer secret is encrypted with Windows DPAPI CurrentUser. It is never
included in the extension, manifest, URLs, or logs. Other processes running
as the same Windows user remain inside this trust boundary.
"""
import binascii
import os
import sys

# pyRevit Routes (6.x and 7.0) builds each route request without copying the
# HTTP headers, so route handlers never see Authorization. Bridge calls also
# carry the secret in this JSON body field; routes remove it before use.
AUTH_FIELD = "revit_mcp_auth"


class PairingError(RuntimeError):
    pass


def pairing_path():
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise PairingError("Windows LOCALAPPDATA is unavailable.")
    return os.path.join(root, "RevitMCPPython", "pairing.dpapi")


def _dpapi(data, protect):
    if sys.platform == "cli":
        import clr
        # .NET Framework (Revit <= 2024) ships ProtectedData in System.Security;
        # .NET 8 (Revit 2025+) ships it in its own assembly. A facade with the
        # .NET 8 name can load on .NET Framework without exposing the type, so
        # reference both instead of stopping at the first that loads.
        for assembly in ("System.Security", "System.Security.Cryptography.ProtectedData"):
            try:
                clr.AddReference(assembly)
            except Exception:
                pass
        from System import Array, Byte
        from System.Security.Cryptography import ProtectedData, DataProtectionScope
        operation = ProtectedData.Protect if protect else ProtectedData.Unprotect
        result = operation(Array[Byte](bytearray(data)), None,
                           DataProtectionScope.CurrentUser)
        return bytes(bytearray(result))

    if os.name != "nt":
        raise PairingError("Pairing requires Windows DPAPI.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD),
                    ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
                          ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN: requests must never trigger credential dialogs.
    if not operation(ctypes.byref(source), None, None, None, None, 1,
                     ctypes.byref(target)):
        raise PairingError("Windows could not protect or read the pairing secret.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def enable_pairing():
    """Rotate the secret after the user accepts the Connect ribbon notice."""
    token = binascii.hexlify(os.urandom(32))
    encrypted = _dpapi(token, True)
    path = pairing_path()
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    # No plaintext token is written to disk.
    with open(path, "wb") as stream:
        stream.write(encrypted)


def disable_pairing():
    """Revoke pairing by removing exactly this extension's protected secret."""
    path = pairing_path()
    if os.path.isfile(path):
        os.remove(path)


def get_token():
    try:
        with open(pairing_path(), "rb") as stream:
            token = _dpapi(stream.read(), False).decode("ascii")
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("Invalid pairing data")
        return token
    # IronPython 2 raises IOError, which is not an OSError there, for a
    # missing file; letting it escape made pyRevit answer 408 instead of 401.
    except (EnvironmentError, ValueError, PairingError):
        raise PairingError("Use Revit's MCP > Bridge > Connect button to consent and pair this Windows user.")


def authorization_headers():
    return {"Authorization": "Bearer " + get_token()}


def authorization_body(data=None):
    """Return a copy of a JSON object payload with the pairing secret added."""
    body = dict(data or {})
    body[AUTH_FIELD] = get_token()
    return body


def validate_request(headers, body_token=None):
    """Reject browser-originated requests and authenticate a paired client.

    An Authorization header is authoritative when one arrives. pyRevit Routes
    drops headers, so otherwise the secret taken from the JSON body is checked.
    """
    normalized = dict((str(k).lower(), str(v)) for k, v in (headers or {}).items())
    if "origin" in normalized:
        return False
    try:
        token = get_token()
    except PairingError:
        return False
    if "authorization" in normalized:
        return _same_secret(normalized["authorization"], "Bearer " + token)
    if not body_token or isinstance(body_token, (dict, list, bool, int, float)):
        return False
    try:
        body_token = u"{}".format(body_token)
    except Exception:
        return False
    return _same_secret(body_token, token)


def _same_secret(actual, expected):
    """Compare secrets in constant time."""
    import hmac
    # IronPython 2 may not provide compare_digest.
    if hasattr(hmac, "compare_digest"):
        try:
            return hmac.compare_digest(actual.encode("utf-8"), expected.encode("utf-8"))
        except UnicodeError:
            return False
    if len(actual) != len(expected):
        return False
    difference = 0
    for left, right in zip(actual, expected):
        difference |= ord(left) ^ ord(right)
    return difference == 0
