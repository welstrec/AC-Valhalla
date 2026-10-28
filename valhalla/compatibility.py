"""Read-only executable identity for meaningful compatibility diagnostics."""
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path


class FixedFileInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "signature", "structure_version", "file_ms", "file_ls", "product_ms",
        "product_ls", "flags_mask", "flags", "os", "file_type", "subtype", "date_ms", "date_ls")]


def executable_identity(path, cancel=None):
    file = Path(path)
    result = {"path": str(file), "version": "No declarada", "sha256": None}
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        unused = wintypes.DWORD()
        size = version.GetFileVersionInfoSizeW(str(file), ctypes.byref(unused))
        if size and size <= 16 * 1024 * 1024:
            data = ctypes.create_string_buffer(size)
            if version.GetFileVersionInfoW(str(file), 0, size, data):
                pointer, length = ctypes.c_void_p(), wintypes.UINT()
                if version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) and length.value >= ctypes.sizeof(FixedFileInfo):
                    info = ctypes.cast(pointer, ctypes.POINTER(FixedFileInfo)).contents
                    if info.signature == 0xFEEF04BD:
                        result["version"] = ".".join(str(x) for x in (info.file_ms >> 16, info.file_ms & 65535, info.file_ls >> 16, info.file_ls & 65535))
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            while chunk := stream.read(2 * 1024 * 1024):
                if cancel and cancel.is_set():
                    result["hash_cancelled"] = True
                    return result
                digest.update(chunk)
        result["sha256"] = digest.hexdigest()
        result["size"] = file.stat().st_size
    except (OSError, ValueError, AttributeError) as exc:
        result["read_error"] = str(exc)
    return result
