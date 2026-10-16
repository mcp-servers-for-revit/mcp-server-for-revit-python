# -*- coding: utf-8 -*-
"""Per-Windows-user pairing, shared by CPython and IronPython.

The bearer secret is encrypted with Windows DPAPI CurrentUser. It is never
included in the extension, manifest, URLs, or logs. Other processes running
as the same Windows user remain inside this trust boundary.
"""
import binascii
import os
import sys


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
        try:
            clr.AddReference("System.Security.Cryptography.ProtectedData")
        except Exception:
            clr.AddReference("System.Security")
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
    except (OSError, ValueError, PairingError):
        raise PairingError("Use Revit's MCP > Bridge > Connect button to consent and pair this Windows user.")


# pyRevit Routes does not expose HTTP headers to route handlers, so bridge
# requests carry the secret in this JSON body field instead of Authorization.
AUTH_FIELD = "revit_mcp_auth"


def authorization_headers():
    return {"Authorization": "Bearer " + get_token()}


def authorization_body(data=None):
    """Return a copy of a JSON object payload with the pairing secret added."""
    body = dict(data or {})
    body[AUTH_FIELD] = get_token()
    return body


def validate_request(headers, body_token=None):
    """Reject browser-originated requests and authenticate a paired client.

    The Authorization header wins when present; otherwise the secret popped from
    the JSON body (see AUTH_FIELD) is checked.
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
    if body_token is None:
        return False
    try:
        return _same_secret(str(body_token), token)
    except UnicodeError:
        return False


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
