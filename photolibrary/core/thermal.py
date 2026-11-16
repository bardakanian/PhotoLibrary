"""How warm the Mac says it is, so long background work can ease off before it gets hot.

macOS reports a thermal state through NSProcessInfo: nominal, fair, serious or critical. A
fanless MacBook Air reaches "fair" and "serious" under sustained load. Read through the
Objective-C runtime with ctypes (no extra package); elsewhere, or if it can't be read, None.
"""

import ctypes
import ctypes.util
import sys

NOMINAL, FAIR, SERIOUS, CRITICAL = 0, 1, 2, 3
_reader = None


def _make_reader():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send_object = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    send_integer = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    info = send_object(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
    selector = objc.sel_registerName(b"thermalState")
    return lambda: int(send_integer(info, selector))


def thermal_state() -> int | None:
    """NOMINAL to CRITICAL on macOS; None where it can't be read."""
    global _reader
    if sys.platform != "darwin":
        return None
    try:
        if _reader is None:
            _reader = _make_reader()
        state = _reader()
    except (OSError, AttributeError, TypeError, ValueError):
        return None
    return state if NOMINAL <= state <= CRITICAL else None


def allowed_workers(gentle: int, state: int | None) -> int:
    """How many photos to work on at once: `gentle` normally, 1 when warm, 0 (pause) when hot."""
    if state is None or state <= NOMINAL:
        return gentle
    if state == FAIR:
        return 1
    return 0
