"""Bounded Windows process access for the Valhalla desktop application.

Importing this module never opens a process. Remote allocations belong to the
caller: close() deliberately does not free them, since game hooks may still use
them. A timed-out remote worker permanently taints its session.
"""
from __future__ import annotations

import ctypes
import ntpath
import os
import re
import struct
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable


GAME_NAMES = frozenset({"acvalhalla.exe", "acvalhalla_plus.exe"})
MIN_ADDRESS = 0x10000
MAX_ADDRESS = 0x00007FFFFFFFFFFF
MAX_TRANSFER = 64 * 1024 * 1024
SCAN_CHUNK = 1024 * 1024
MEM_COMMIT, MEM_RESERVE, MEM_RELEASE = 0x1000, 0x2000, 0x8000
PAGE_READWRITE, PAGE_EXECUTE_READWRITE = 0x04, 0x40
PAGE_GUARD, PAGE_NOACCESS = 0x100, 0x01
READABLE = frozenset({0x02, 0x04, 0x08, 0x20, 0x40, 0x80})
WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED = 0, 258, 0xFFFFFFFF
INVALID_HANDLE = ctypes.c_void_p(-1).value
_QUARANTINE: list["ProcessMemory"] = []
_TAINTED_IDENTITIES: set[tuple[int, int]] = set()


class MemoryAccessError(RuntimeError):
    """The requested operation could not be completed and verified."""


class TargetTaintedError(MemoryAccessError):
    """A remote operation may still run; the session cannot be reused."""


class ScanCancelled(MemoryAccessError):
    pass


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    path: str
    created: int


@dataclass(frozen=True)
class CompiledPattern:
    values: bytes
    masks: bytes
    regex: re.Pattern[bytes]

    def __len__(self) -> int:
        return len(self.values)


def compile_pattern(pattern: str) -> CompiledPattern:
    """Parse CE byte patterns, including 3?, ?0, ?, ??, * and compact hex."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("La firma de bytes está vacía.")
    raw = pattern.split()
    tokens: list[str] = []
    for token in raw:
        if len(token) <= 2:
            tokens.append(token)
        elif len(token) % 2 == 0 and "*" not in token:
            tokens.extend(token[i:i + 2] for i in range(0, len(token), 2))
        else:
            raise ValueError(f"Componente de firma inválido: {token!r}.")
    if len(tokens) > 4096:
        raise ValueError("La firma excede 4096 bytes.")
    values, masks, parts = bytearray(), bytearray(), []
    for token in tokens:
        if token in {"?", "??", "*"}:
            value, mask = 0, 0
        elif re.fullmatch(r"[0-9A-Fa-f?]{2}", token):
            value = int(token.replace("?", "0"), 16)
            mask = (0 if token[0] == "?" else 0xF0) | (0 if token[1] == "?" else 0x0F)
        else:
            raise ValueError(f"Componente de firma inválido: {token!r}.")
        values.append(value)
        masks.append(mask)
        if mask == 0:
            parts.append(b".")
        elif mask == 255:
            parts.append(re.escape(bytes([value])))
        else:
            choices = bytes(n for n in range(256) if n & mask == value)
            parts.append(b"[" + re.escape(choices) + b"]")
    if not any(masks):
        raise ValueError("La firma debe contener al menos un dígito hexadecimal.")
    # Lookahead deliberately preserves overlapping matches.
    regex = re.compile(b"(?=" + b"".join(parts) + b")", re.DOTALL)
    return CompiledPattern(bytes(values), bytes(masks), regex)


def find_matches(data: bytes, pattern: str | CompiledPattern, base: int = 0) -> list[int]:
    compiled = compile_pattern(pattern) if isinstance(pattern, str) else pattern
    return [base + match.start() for match in compiled.regex.finditer(data)]


def validate_range(address: int, size: int) -> None:
    if type(address) is not int or type(size) is not int or size <= 0:
        raise ValueError("Dirección o tamaño de memoria inválido.")
    if size > MAX_TRANSFER:
        raise ValueError("La operación excede el límite de 64 MiB.")
    if address < MIN_ADDRESS or address > MAX_ADDRESS or size - 1 > MAX_ADDRESS - address:
        raise ValueError("La dirección está fuera del espacio de usuario de 64 bits.")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
                ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
                ("szModule", wintypes.WCHAR * 256), ("szExePath", wintypes.WCHAR * 260)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


def _load_kernel():
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    size = ctypes.c_size_t
    declarations = {
        "OpenProcess": (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        "CreateToolhelp32Snapshot": (wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
        "Process32FirstW": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]),
        "Process32NextW": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]),
        "Module32FirstW": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]),
        "Module32NextW": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]),
        "QueryFullProcessImageNameW": (wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]),
        "GetProcessTimes": (wintypes.BOOL, [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4),
        "ReadProcessMemory": (wintypes.BOOL, [wintypes.HANDLE, pointer, pointer, size, ctypes.POINTER(size)]),
        "WriteProcessMemory": (wintypes.BOOL, [wintypes.HANDLE, pointer, pointer, size, ctypes.POINTER(size)]),
        "VirtualQueryEx": (size, [wintypes.HANDLE, pointer, ctypes.POINTER(MEMORY_BASIC_INFORMATION), size]),
        "VirtualAllocEx": (pointer, [wintypes.HANDLE, pointer, size, wintypes.DWORD, wintypes.DWORD]),
        "VirtualFreeEx": (wintypes.BOOL, [wintypes.HANDLE, pointer, size, wintypes.DWORD]),
        "VirtualProtectEx": (wintypes.BOOL, [wintypes.HANDLE, pointer, size, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]),
        "FlushInstructionCache": (wintypes.BOOL, [wintypes.HANDLE, pointer, size]),
        "CreateRemoteThread": (wintypes.HANDLE, [wintypes.HANDLE, pointer, size, pointer, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]),
        "WaitForSingleObject": (wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD]),
        "GetExitCodeThread": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]),
        "IsWow64Process": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]),
    }
    for name, (restype, argtypes) in declarations.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = restype, argtypes
    return kernel


_kernel = _load_kernel()


def _require_windows():
    if _kernel is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise MemoryAccessError("Esta función requiere Windows y una aplicación de 64 bits.")
    return _kernel


def _win_error(message: str) -> MemoryAccessError:
    code = ctypes.get_last_error() if sys.platform == "win32" else 0
    return MemoryAccessError(f"{message} (Windows: {code}).")


def _creation_time(handle) -> int:
    values = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
        raise _win_error("No se pudo verificar la identidad del proceso")
    return values[0].dwLowDateTime | values[0].dwHighDateTime << 32


def _process_info(pid: int, handle) -> ProcessInfo:
    length = wintypes.DWORD(32768)
    path = ctypes.create_unicode_buffer(length.value)
    if not _kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
        raise _win_error("No se pudo leer la ruta del proceso")
    return ProcessInfo(pid, ntpath.basename(path.value), path.value, _creation_time(handle))


def list_games() -> list[ProcessInfo]:
    """List recognized game instances without requesting memory-write access."""
    kernel = _require_windows()
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot in (None, INVALID_HANDLE):
        raise _win_error("No se pudo obtener la lista de procesos")
    found = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() in GAME_NAMES:
                handle = kernel.OpenProcess(0x1000 | 0x100000, False, entry.th32ProcessID)
                if handle:
                    try:
                        info = _process_info(entry.th32ProcessID, handle)
                        if info.name.casefold() in GAME_NAMES and kernel.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT:
                            found.append(info)
                    except MemoryAccessError:
                        pass  # A process may disappear during enumeration.
                    finally:
                        kernel.CloseHandle(handle)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return sorted(found, key=lambda item: (item.name.casefold(), item.pid))


def _owned_test_target(info: ProcessInfo) -> bool:
    """Testing exception is limited to this Python interpreter and its child."""
    paths = {os.path.normcase(os.path.abspath(sys.executable)),
             os.path.normcase(os.path.abspath(getattr(sys, "_base_executable", sys.executable)))}
    if info.name.casefold() not in {"python.exe", "pythonw.exe"} or os.path.normcase(info.path) not in paths:
        return False
    if info.pid == os.getpid():
        return True
    snapshot = _kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot in (None, INVALID_HANDLE):
        return False
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = _kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == info.pid:
                return entry.th32ParentProcessID == os.getpid()
            ok = _kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel.CloseHandle(snapshot)
    return False


class ProcessMemory:
    def __init__(self, pid: int, *, allow_test_target: bool = False):
        kernel = _require_windows()
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("Identificador de proceso inválido.")
        self.pid, self.name, self.path = pid, "", ""
        self.base, self.size = 0, 0
        self.identity = (pid, 0)
        self.tainted = False
        self._closing = False
        self._threads: set[int] = set()
        self._allocations: dict[int, int] = {}
        rights = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400 | 0x1000 | 0x100000
        self._handle = kernel.OpenProcess(rights, False, pid)
        if not self._handle:
            raise _win_error("No se pudo conectar al juego; revisa los permisos")
        try:
            info = _process_info(pid, self._handle)
            if info.name.casefold() not in GAME_NAMES and not (allow_test_target is True and _owned_test_target(info)):
                raise MemoryAccessError("El proceso seleccionado no es AC Valhalla.")
            self.name, self.path = info.name, info.path
            self.identity = (pid, info.created)
            if self.identity in _TAINTED_IDENTITIES:
                raise TargetTaintedError("Esta instancia tiene una operación sin confirmar. Reinicia el juego antes de conectar.")
            wow = wintypes.BOOL()
            if not kernel.IsWow64Process(self._handle, ctypes.byref(wow)) or wow.value:
                raise MemoryAccessError("El proceso seleccionado no es un juego de 64 bits compatible.")
            self._locate_module()
            self._ensure_open()
        except Exception:
            kernel.CloseHandle(self._handle)
            self._handle = None
            raise

    def _locate_module(self) -> None:
        # Toolhelp occasionally reports ERROR_BAD_LENGTH while modules load.
        snapshot = None
        for _ in range(4):
            snapshot = _kernel.CreateToolhelp32Snapshot(0x8 | 0x10, self.pid)
            if snapshot not in (None, INVALID_HANDLE):
                break
            if ctypes.get_last_error() != 24:
                break
        if snapshot in (None, INVALID_HANDLE):
            raise _win_error("No se pudieron leer los módulos del juego")
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            ok = _kernel.Module32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                if entry.szModule.casefold() == self.name.casefold():
                    self.base, self.size = int(entry.modBaseAddr), int(entry.modBaseSize)
                    if not self.base or self.size <= 0 or self.base + self.size - 1 > MAX_ADDRESS:
                        raise MemoryAccessError("El módulo del juego tiene un rango inválido.")
                    return
                ok = _kernel.Module32NextW(snapshot, ctypes.byref(entry))
        finally:
            _kernel.CloseHandle(snapshot)
        raise MemoryAccessError("No se encontró el módulo principal del juego.")

    def is_alive(self) -> bool:
        if not self._handle or _kernel.WaitForSingleObject(self._handle, 0) != WAIT_TIMEOUT:
            return False
        try:
            return _creation_time(self._handle) == self.identity[1]
        except MemoryAccessError:
            return False

    def _ensure_open(self) -> None:
        if self.tainted:
            raise TargetTaintedError("Una operación quedó sin confirmar. Reinicia el juego antes de volver a conectar.")
        if self._closing or not self.is_alive():
            raise MemoryAccessError("La sesión del juego terminó. Vuelve a conectar a la instancia actual.")

    def _mark_tainted(self) -> None:
        self.tainted = True
        _TAINTED_IDENTITIES.add(self.identity)

    def read(self, address: int, size: int) -> bytes:
        validate_range(address, size)
        self._ensure_open()
        buffer = ctypes.create_string_buffer(size)
        transferred = ctypes.c_size_t()
        if not _kernel.ReadProcessMemory(self._handle, address, buffer, size, ctypes.byref(transferred)) or transferred.value != size:
            raise _win_error(f"No se pudieron leer {size} bytes en 0x{address:X}")
        return buffer.raw

    def write(self, address: int, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Los datos de memoria deben ser bytes.")
        data = bytes(data)
        validate_range(address, len(data))
        self._ensure_open()
        buffer = ctypes.create_string_buffer(data, len(data))
        transferred = ctypes.c_size_t()
        if not _kernel.WriteProcessMemory(self._handle, address, buffer, len(data), ctypes.byref(transferred)) or transferred.value != len(data):
            raise _win_error(f"No se pudieron escribir {len(data)} bytes en 0x{address:X}")
        if self.read(address, len(data)) != data:
            raise MemoryAccessError("La comprobación posterior a la escritura no coincide.")

    def _region(self, address: int) -> MEMORY_BASIC_INFORMATION:
        self._ensure_open()
        info = MEMORY_BASIC_INFORMATION()
        returned = _kernel.VirtualQueryEx(self._handle, address, ctypes.byref(info), ctypes.sizeof(info))
        if returned != ctypes.sizeof(info) or not info.RegionSize:
            raise _win_error("No se pudo consultar una región de memoria")
        if not info.BaseAddress or info.BaseAddress > address or info.BaseAddress + info.RegionSize <= address:
            raise MemoryAccessError("La consulta de memoria devolvió una región inválida.")
        return info

    def write_code(self, address: int, data: bytes, expected: bytes | None = None) -> None:
        """Verify and patch bytes, restoring every affected region's protection.

        This does not suspend game threads and is not an atomic code replacement.
        Hook owners must separately establish when patching is safe.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("El código debe proporcionarse como bytes.")
        data = bytes(data)
        validate_range(address, len(data))
        self._ensure_open()
        if expected is not None:
            if not isinstance(expected, bytes) or len(expected) != len(data):
                raise ValueError("Los bytes originales deben tener el mismo tamaño que el parche.")
        original = self.read(address, len(data))
        if expected is not None and original != expected:
            raise MemoryAccessError("Los bytes originales no coinciden; no se aplicó el parche.")
        segments = []
        cursor, end = address, address + len(data)
        while cursor < end:
            region = self._region(cursor)
            if region.State != MEM_COMMIT or region.Protect & PAGE_GUARD:
                raise MemoryAccessError("El parche alcanza memoria no disponible.")
            length = min(end, region.BaseAddress + region.RegionSize) - cursor
            segments.append((cursor, length))
            cursor += length
        changed = []
        failure = None
        restoration_errors = []
        write_attempted = False
        try:
            for start, length in segments:
                old = wintypes.DWORD()
                if not _kernel.VirtualProtectEx(self._handle, start, length, PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
                    raise _win_error("No se pudo habilitar la escritura del código")
                changed.append((start, length, old.value))
            write_attempted = True
            self.write(address, data)
        except Exception as exc:
            failure = exc
            if write_attempted:
                try:
                    self.write(address, original)
                except Exception:
                    restoration_errors.append("no se pudo verificar la restauración de los bytes originales")
        finally:
            if changed and not _kernel.FlushInstructionCache(self._handle, address, len(data)):
                restoration_errors.append("no se pudo actualizar la caché de instrucciones")
            for start, length, protection in reversed(changed):
                unused = wintypes.DWORD()
                if not _kernel.VirtualProtectEx(self._handle, start, length, protection, ctypes.byref(unused)):
                    restoration_errors.append("no se pudo restaurar la protección de memoria")
        if restoration_errors:
            self._mark_tainted()
            raise TargetTaintedError("; ".join(restoration_errors) + ". Reinicia el juego.") from failure
        if failure:
            raise failure

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def resolve_rip(self, address: int, disp_offset: int = 3, instruction_size: int = 7) -> int:
        if type(disp_offset) is not int or type(instruction_size) is not int or disp_offset < 0 or instruction_size < disp_offset + 4:
            raise ValueError("Descripción de instrucción RIP inválida.")
        target = address + instruction_size + self.i32(address + disp_offset)
        validate_range(target, 1)
        return target

    def is_executable(self, address: int) -> bool:
        try:
            validate_range(address, 1)
        except ValueError:
            return False
        region = self._region(address)
        return (region.State == MEM_COMMIT and not region.Protect & PAGE_GUARD
                and (region.Protect & 0xFF) in {0x10, 0x20, 0x40, 0x80})

    def alloc(self, size: int, executable: bool = False) -> int:
        validate_range(MIN_ADDRESS, size)
        self._ensure_open()
        protection = PAGE_EXECUTE_READWRITE if executable else PAGE_READWRITE
        address = _kernel.VirtualAllocEx(self._handle, None, size, MEM_COMMIT | MEM_RESERVE, protection)
        if not address:
            raise _win_error("No se pudo reservar memoria temporal en el juego")
        self._allocations[int(address)] = size
        return int(address)

    def free(self, address: int) -> None:
        self._ensure_open()
        if type(address) is not int or address not in self._allocations:
            raise ValueError("La reserva no pertenece a esta sesión.")
        if self._threads:
            raise MemoryAccessError("No se libera memoria mientras hay un trabajador remoto pendiente.")
        if not _kernel.VirtualFreeEx(self._handle, address, 0, MEM_RELEASE):
            raise _win_error("No se pudo liberar la memoria temporal")
        del self._allocations[address]

    def scan(self, pattern: str, *, cancel: Callable[[], bool] | None = None,
             progress: Callable[[int, int], None] | None = None) -> list[int]:
        compiled = compile_pattern(pattern)
        self._ensure_open()
        cursor, end = self.base, self.base + self.size
        tail, tail_end = b"", None
        matches = []
        while cursor < end:
            if cancel and cancel():
                raise ScanCancelled("Búsqueda cancelada.")
            region = self._region(cursor)
            region_end = min(end, region.BaseAddress + region.RegionSize)
            readable = region.State == MEM_COMMIT and not region.Protect & PAGE_GUARD and (region.Protect & 0xFF) in READABLE
            if not readable:
                tail, tail_end = b"", None
                cursor = region_end
            else:
                while cursor < region_end:
                    if cancel and cancel():
                        raise ScanCancelled("Búsqueda cancelada.")
                    length = min(SCAN_CHUNK, region_end - cursor)
                    data = self.read(cursor, length)
                    if tail_end != cursor:
                        tail = b""
                    combined = tail + data
                    matches.extend(find_matches(combined, compiled, cursor - len(tail)))
                    tail = combined[-(len(compiled) - 1):] if len(compiled) > 1 else b""
                    cursor += length
                    tail_end = cursor
                    if progress:
                        progress(cursor - self.base, self.size)
            if progress:
                progress(cursor - self.base, self.size)
        return matches

    def scan_unique(self, pattern: str, **kwargs) -> int:
        matches = self.scan(pattern, **kwargs)
        if not matches:
            raise MemoryAccessError("No se encontró una firma compatible para esta función.")
        if len(matches) != 1:
            raise MemoryAccessError(f"La firma tiene {len(matches)} coincidencias; la función se bloqueó.")
        return matches[0]

    def run_thread(self, start: int, parameter: int = 0, timeout_ms: int = 10000) -> int:
        validate_range(start, 1)
        if type(parameter) is not int or not 0 <= parameter <= MAX_ADDRESS:
            raise ValueError("Parámetro remoto inválido.")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 300000:
            raise ValueError("El tiempo de espera debe estar entre 1 y 300000 ms.")
        self._ensure_open()
        if self._threads:
            raise MemoryAccessError("Ya existe una operación remota pendiente.")
        if not self.is_executable(start):
            raise MemoryAccessError("La dirección del trabajador no contiene memoria ejecutable.")
        thread_id = wintypes.DWORD()
        handle = _kernel.CreateRemoteThread(self._handle, None, 0, start, parameter, 0, ctypes.byref(thread_id))
        if not handle:
            raise _win_error("No se pudo iniciar la operación dentro del juego")
        self._threads.add(handle)
        result = _kernel.WaitForSingleObject(handle, timeout_ms)
        if result != WAIT_OBJECT_0:
            self._mark_tainted()
            if self not in _QUARANTINE:
                _QUARANTINE.append(self)
            raise TargetTaintedError("El juego no confirmó el fin de la operación. Reinicia el juego; se conservó la memoria que podría seguir en uso.")
        exit_code = wintypes.DWORD()
        try:
            if not _kernel.GetExitCodeThread(handle, ctypes.byref(exit_code)):
                raise _win_error("La operación terminó, pero no se pudo leer su resultado")
            return exit_code.value
        finally:
            _kernel.CloseHandle(handle)
            self._threads.discard(handle)

    def reap_threads(self) -> bool:
        """Close only signaled remote handles; taint remains permanent."""
        for handle in tuple(self._threads):
            if _kernel.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
                _kernel.CloseHandle(handle)
                self._threads.discard(handle)
        return not self._threads

    def close(self) -> bool:
        """Release handles when workers finish; never implicitly free game memory.

        False means a worker is still pending. Calling close() again is safe.
        The quarantine retains this session until its handle can be collected.
        """
        self._closing = True
        if not self.reap_threads():
            if self not in _QUARANTINE:
                _QUARANTINE.append(self)
            return False
        if self._handle:
            _kernel.CloseHandle(self._handle)
            self._handle = None
        if self in _QUARANTINE:
            _QUARANTINE.remove(self)
        return True


def reap_quarantine() -> None:
    for session in tuple(_QUARANTINE):
        session.close()
