# -*- coding: utf-8 -*-
"""Per-Windows-user pairing, shared by CPython and IronPython.

The bearer secret is encrypted with Windows DPAPI CurrentUser. It is never
included in the extension or manifest. Bridge calls to pyRevit's loopback
routes server carry it as a query parameter (see PAIRING_QUERY_PARAM), so it
can appear in pyRevit's own debug-level request log. Other processes running
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


def authorization_headers():
    return {"Authorization": "Bearer " + get_token()}


# pyRevit's routes server does not forward HTTP request headers to route
# handlers (Request.headers is always empty), so bridge calls also carry the
# token as a query parameter, which pyRevit does pass through.
PAIRING_QUERY_PARAM = "revit_mcp_pairing"


def bridge_query_params():
    return {PAIRING_QUERY_PARAM: get_token()}


def bridge_request_headers(headers, query_params):
    """Headers to validate for a pyRevit route request.

    Uses the real Authorization header when pyRevit provides one, otherwise
    the token from the pairing query parameter.
    """
    merged = dict(headers or {})
    if any(str(key).lower() == "authorization" for key in merged):
        return merged
    token = (query_params or {}).get(PAIRING_QUERY_PARAM)
    # A repeated parameter arrives as a list; reject anything but one string.
    if isinstance(token, basestring if sys.version_info[0] == 2 else str):  # noqa: F821
        merged["Authorization"] = "Bearer " + token
    return merged


def validate_request(headers):
    """Reject browser-originated requests and authenticate a paired client."""
    import hmac
    normalized = dict((str(k).lower(), str(v)) for k, v in headers.items())
    if "origin" in normalized:
        return False
    try:
        expected = "Bearer " + get_token()
    except PairingError:
        return False
    actual = normalized.get("authorization", "")
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
