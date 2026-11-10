"""Windows SSPI through ctypes: sign in to a proxy as the logged-on Windows user.

The ``Negotiate`` package picks Kerberos when the proxy has a service principal
(``HTTP/<proxy host>``) the domain can issue a ticket for, and NTLM otherwise;
the ``NTLM`` package is NTLM only. Either way Windows supplies the credential of
the account this process runs as: no password is read, stored or sent by us.

Nothing here runs at import on a non-Windows host: the struct layouts are plain
``ctypes`` types, and ``secur32.dll`` is loaded on first use, so the module
imports everywhere and tests can replace :class:`SspiContext` with a fake.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

SECPKG_CRED_INBOUND = 1
SECPKG_CRED_OUTBOUND = 2
SECBUFFER_VERSION = 0
SECBUFFER_TOKEN = 2
SECURITY_NATIVE_DREP = 0x10
ISC_REQ_ALLOCATE_MEMORY = 0x100
ISC_REQ_CONNECTION = 0x800
ASC_REQ_ALLOCATE_MEMORY = 0x100
ASC_REQ_CONNECTION = 0x800
SEC_E_OK = 0
SEC_I_CONTINUE_NEEDED = 0x00090312
SEC_I_COMPLETE_NEEDED = 0x00090313
SEC_I_COMPLETE_AND_CONTINUE = 0x00090314

#: The SSPI package for each HTTP auth scheme this module speaks.
PACKAGE_FOR_SCHEME = {"negotiate": "Negotiate", "ntlm": "NTLM"}


# Module scope on purpose (see platform_compat_native): a Structure declared in a
# function body pins a new pair of type objects in ctypes' POINTER cache per call.
class SecHandle(ctypes.Structure):
    _fields_ = [("dwLower", ctypes.c_size_t), ("dwUpper", ctypes.c_size_t)]


class SecBuffer(ctypes.Structure):
    _fields_ = [
        ("cbBuffer", wintypes.ULONG),
        ("BufferType", wintypes.ULONG),
        ("pvBuffer", ctypes.c_void_p),
    ]


class SecBufferDesc(ctypes.Structure):
    _fields_ = [
        ("ulVersion", wintypes.ULONG),
        ("cBuffers", wintypes.ULONG),
        ("pBuffers", ctypes.POINTER(SecBuffer)),
    ]


class SspiError(OSError):
    """An SSPI call failed; ``status`` is the SECURITY_STATUS it returned."""

    def __init__(self, call: str, status: int) -> None:
        self.status = status & 0xFFFFFFFF
        super().__init__(f"{call} failed with SECURITY_STATUS 0x{self.status:08X}")


_lib_lock = threading.Lock()
_lib: ctypes.WinDLL | None = None  # type: ignore[name-defined, unused-ignore]


def available() -> bool:
    """Whether this host has SSPI (Windows)."""
    return sys.platform == "win32"


def _secur32() -> ctypes.WinDLL:  # type: ignore[name-defined, unused-ignore]
    global _lib
    with _lib_lock:
        if _lib is not None:
            return _lib
        if not available():
            raise SspiError("LoadLibrary(secur32)", 0x80090302)  # SEC_E_UNSUPPORTED_FUNCTION
        lib = ctypes.WinDLL("secur32")  # type: ignore[attr-defined, unused-ignore]
        lib.AcquireCredentialsHandleW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.ULONG,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(SecHandle),
            ctypes.POINTER(ctypes.c_longlong),
        ]
        lib.AcquireCredentialsHandleW.restype = ctypes.c_long
        lib.InitializeSecurityContextW.argtypes = [
            ctypes.POINTER(SecHandle),
            ctypes.POINTER(SecHandle),
            wintypes.LPCWSTR,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.POINTER(SecBufferDesc),
            wintypes.ULONG,
            ctypes.POINTER(SecHandle),
            ctypes.POINTER(SecBufferDesc),
            ctypes.POINTER(wintypes.ULONG),
            ctypes.POINTER(ctypes.c_longlong),
        ]
        lib.InitializeSecurityContextW.restype = ctypes.c_long
        lib.AcceptSecurityContext.argtypes = [
            ctypes.POINTER(SecHandle),
            ctypes.POINTER(SecHandle),
            ctypes.POINTER(SecBufferDesc),
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.POINTER(SecHandle),
            ctypes.POINTER(SecBufferDesc),
            ctypes.POINTER(wintypes.ULONG),
            ctypes.POINTER(ctypes.c_longlong),
        ]
        lib.AcceptSecurityContext.restype = ctypes.c_long
        lib.CompleteAuthToken.argtypes = [ctypes.POINTER(SecHandle), ctypes.POINTER(SecBufferDesc)]
        lib.CompleteAuthToken.restype = ctypes.c_long
        lib.FreeContextBuffer.argtypes = [ctypes.c_void_p]
        lib.FreeContextBuffer.restype = ctypes.c_long
        lib.DeleteSecurityContext.argtypes = [ctypes.POINTER(SecHandle)]
        lib.DeleteSecurityContext.restype = ctypes.c_long
        lib.FreeCredentialsHandle.argtypes = [ctypes.POINTER(SecHandle)]
        lib.FreeCredentialsHandle.restype = ctypes.c_long
        _lib = lib
        return lib


def _token_desc(data: bytes | None) -> tuple[SecBufferDesc, SecBuffer, ctypes.Array | None]:
    """A one-buffer token descriptor; keep the returned buffer objects alive."""
    if data is None:
        buf = SecBuffer(0, SECBUFFER_TOKEN, None)
        raw = None
    else:
        raw = ctypes.create_string_buffer(data, len(data))
        buf = SecBuffer(len(data), SECBUFFER_TOKEN, ctypes.cast(raw, ctypes.c_void_p))
    return SecBufferDesc(SECBUFFER_VERSION, 1, ctypes.pointer(buf)), buf, raw


def _take_output(lib: ctypes.WinDLL, buf: SecBuffer) -> bytes:  # type: ignore[name-defined, unused-ignore]
    if not buf.pvBuffer or not buf.cbBuffer:
        return b""
    try:
        return ctypes.string_at(buf.pvBuffer, buf.cbBuffer)
    finally:
        lib.FreeContextBuffer(buf.pvBuffer)


class SspiCredentials:
    """One credential handle for *package*, as this process's Windows user.

    ``inbound`` is the server side, used only by tests that stand up a local
    SSPI-checking proxy.
    """

    def __init__(self, package: str, *, inbound: bool = False) -> None:
        self.package = package
        self._handle = SecHandle()
        lib = _secur32()
        expiry = ctypes.c_longlong()
        status = lib.AcquireCredentialsHandleW(
            None,
            package,
            SECPKG_CRED_INBOUND if inbound else SECPKG_CRED_OUTBOUND,
            None,
            None,
            None,
            None,
            ctypes.byref(self._handle),
            ctypes.byref(expiry),
        )
        if status != SEC_E_OK:
            raise SspiError("AcquireCredentialsHandleW", status)
        self._open = True

    @property
    def handle(self) -> SecHandle:
        return self._handle

    def close(self) -> None:
        if getattr(self, "_open", False):
            _secur32().FreeCredentialsHandle(ctypes.byref(self._handle))
            self._open = False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class SspiContext:
    """One client-side security context: the token exchange for one connection."""

    def __init__(self, credentials: SspiCredentials, target: str) -> None:
        self._creds = credentials
        self._target = target
        self._handle = SecHandle()
        self._started = False
        self.done = False

    def step(self, server_token: bytes | None) -> bytes:
        """The next token to send, given the server's last one (``None`` to start).

        Sets :attr:`done` once the context is complete. Blocking: with Kerberos
        it may contact the domain controller, so call it from a worker thread.
        """
        lib = _secur32()
        in_desc, in_buf, in_raw = _token_desc(server_token)
        out_desc, out_buf, _ = _token_desc(None)
        attrs = wintypes.ULONG()
        expiry = ctypes.c_longlong()
        status = lib.InitializeSecurityContextW(
            ctypes.byref(self._creds.handle),
            ctypes.byref(self._handle) if self._started else None,
            self._target,
            ISC_REQ_ALLOCATE_MEMORY | ISC_REQ_CONNECTION,
            0,
            SECURITY_NATIVE_DREP,
            ctypes.byref(in_desc) if server_token is not None else None,
            0,
            ctypes.byref(self._handle),
            ctypes.byref(out_desc),
            ctypes.byref(attrs),
            ctypes.byref(expiry),
        )
        del in_buf, in_raw
        self._started = True
        try:
            if status in (SEC_I_COMPLETE_NEEDED, SEC_I_COMPLETE_AND_CONTINUE):
                # Reads and may rewrite the token, so it runs before the buffer is freed.
                complete = lib.CompleteAuthToken(ctypes.byref(self._handle), ctypes.byref(out_desc))
                if complete != SEC_E_OK:
                    raise SspiError("CompleteAuthToken", complete)
        finally:
            token = _take_output(lib, out_buf)
        if status in (SEC_E_OK, SEC_I_COMPLETE_NEEDED):
            self.done = True
        elif status not in (SEC_I_CONTINUE_NEEDED, SEC_I_COMPLETE_AND_CONTINUE):
            raise SspiError("InitializeSecurityContextW", status)
        return token

    def close(self) -> None:
        if self._started:
            _secur32().DeleteSecurityContext(ctypes.byref(self._handle))
            self._started = False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class SspiServerContext:
    """The server side of one exchange (``AcceptSecurityContext``), for tests."""

    def __init__(self, credentials: SspiCredentials) -> None:
        self._creds = credentials
        self._handle = SecHandle()
        self._started = False
        self.done = False

    def step(self, client_token: bytes) -> bytes:
        lib = _secur32()
        in_desc, in_buf, in_raw = _token_desc(client_token)
        out_desc, out_buf, _ = _token_desc(None)
        attrs = wintypes.ULONG()
        expiry = ctypes.c_longlong()
        status = lib.AcceptSecurityContext(
            ctypes.byref(self._creds.handle),
            ctypes.byref(self._handle) if self._started else None,
            ctypes.byref(in_desc),
            ASC_REQ_ALLOCATE_MEMORY | ASC_REQ_CONNECTION,
            SECURITY_NATIVE_DREP,
            ctypes.byref(self._handle),
            ctypes.byref(out_desc),
            ctypes.byref(attrs),
            ctypes.byref(expiry),
        )
        del in_buf, in_raw
        self._started = True
        token = _take_output(lib, out_buf)
        if status == SEC_E_OK:
            self.done = True
        elif status != SEC_I_CONTINUE_NEEDED:
            raise SspiError("AcceptSecurityContext", status)
        return token

    def close(self) -> None:
        if self._started:
            _secur32().DeleteSecurityContext(ctypes.byref(self._handle))
            self._started = False
