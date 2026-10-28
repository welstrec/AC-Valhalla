"""Game-specific operations ported from the supplied 1.6.2 CT.

Signature matches establish a candidate layout, never certify a game version.
No executable on disk is patched. The caller must serialize session operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
import struct
import threading
from typing import Callable, Mapping

from . import assembly
from .memory import ProcessMemory, MemoryAccessError
from .resources import RATIONS_HASH
from .quantities import QuantityPolicy, MAX_STACK_QUANTITY


class CompatibilityError(RuntimeError):
    pass


class StaleInventoryError(RuntimeError):
    pass


@dataclass
class FeatureState:
    key: str
    title: str
    available: bool = False
    reason: str = "Pendiente de diagnóstico"
    enabled: bool = False


@dataclass(frozen=True)
class InventoryItem:
    hash_id: int
    name: str
    quantity: int
    address: int
    identity: tuple
    generation: int
    vector: tuple[int, int, int] = (0, 0, 0)
    settings: int = 0


@dataclass
class GrantResult:
    requested: int
    completed: int = 0
    cancelled: bool = False
    hashes: list[int] = field(default_factory=list)
    confirmed: int = 0
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0
    adjusted: int = 0


@dataclass(frozen=True)
class Patch:
    address: int
    original: bytes
    replacement: bytes


PATTERNS = {
    "grant_call": "48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18 48 89 7C 24 20 41 56 48 81 EC E0 00 00 00 48 8B D9",
    "grant_world": "48 8B 0D ?? ?? ?? ?? 33 D2 48 81 C1 80 04 00 00 E8 ?? ?? ?? ?? 48 8B D8",
    "grant_thread": "48 8B 05 ?? ?? ?? ?? 8B 90 ?? ?? 00 00 3? ?? 0F 93 ?0 C3",
    "thread_reference": "FF 50 18 48 8B 03 48 ?? ?? C7 83 ?4 00 00 00 01 00 00 00",
    "inv2": "48 8B 0D ?? ?? ?? ?? 41 B0 01 49 8B D6 E8 ?? ?? ?? ?? 48 85 C0 74 ?? 48 8B C8 E8 ?? ?? ?? ?? 48 89 44 24 ?? 48 8D 4C 24 ?? 0F B6 84 24 ?? ?? ?? ?? 45",
    "inv3": "48 8B 1D ?? ?? ?? ?? 48 8B 48 ?? 48 8B 01 FF 90 ?? ?? ?? ?? 0F B6 4B ?? 48 8B 00 48 8B 0C C8 48 85 C9 74 ?? 48 8B 01 0F 28 D6 48 8B D6",
    "inv4": "E8 ?? ?? ?? ?? 48 85 C0 74 ?? 48 8B 90 ?? ?? ?? ?? 48 85 D2 74 ?? 48 8B 80 ?? ?? ?? ?? 48",
    "inv_cfg": "40 53 48 83 EC ?? 48 8B 12 48 81 C1 D0 FC FF FF 49 8B D8",
    "persist1": "?? ?? ?? ?? ?? ?? ?? 4D 8B C4 48 89 4D C0 33 C9",
    "persist2": "?? ?? ?? 45 33 C9 89 44 24 20 45 8D 41 01",
    "horse": "48 8D 05 73 FF 99 FE 89 74 24 28 48 89 44 24 20 48 8D 97 88 01 00 00",
    "drink": "80 7C 24 30 00 0F 85 ?? ?? ?? ?? 48 8B CB C6 44 24 30 00",
    "orlog": "83 7A 10 00 4C 8B D1 75 57 4C 8B 49 18 49 8B 81 F0 03 00 00 83 78 04 00 74 1E",
    "stealth": "80 78 7E 00 74 ?? 32 C0",
    "boost_call": "40 55 57 41 55 41 57 48 8D 6C 24 D8 48 81 EC 28 01 00 00 0F 29 B4 24 F0 00 00 00",
    "boost_owner": "48 8B 05 ?? ?? ?? ?? 48 8D 97 30 01 00 00 48 8D 4C 24 ??",
    "boost_offset": "48 8D 8F F0 03 00 00 E8 ?? ?? ?? ?? 80 BF 89 09 00 00 00",
    "boost_thread": "48 8B 05 ?? ?? ?? ?? 87 A8 48 04 00 00",
    "map": "48 83 3D ?? ?? ?? ?? 00 48 8B 1F 74 5B E8 ?? ?? ?? ?? 48 85 C0 74 51 45 32 F6 E8 ?? ?? ?? ?? 4C 8B C8 48 8D 0D ?? ?? ?? ?? 45 33 C0 48 8B D3 E8 ?? ?? ?? ?? 48 8B D8 48 8B 48 08 48 C1 E1 20 48 C1 F9 3F 48 23 08 74 09 E8 ?? ?? ?? ?? 44 0F B6 F0 48 8B CB E8 ?? ?? ?? ?? 45 84 F6 40 0F B6 F6 B8 01 00 00 00 0F 45 F0 48 83 C7 08 49 3B FF",
}

FEATURE_PATTERNS = {
    "inventory": ("inv2", "inv3", "inv4", "inv_cfg"),
    "grant": ("grant_call", "grant_world", "grant_thread", "thread_reference"),
    "persistence": ("persist1", "persist2"),
    "horse": ("horse",), "drink": ("drink",), "orlog": ("orlog",),
    "stealth": ("stealth",),
    "boost": ("boost_call", "boost_owner", "boost_offset", "boost_thread", "thread_reference"),
    "map": ("map",),
}
TITLES = {
    "inventory": "Leer y editar cantidades", "grant": "Añadir objetos",
    "persistence": "Conservar objetos durante esta sesión", "horse": "Velocidad del caballo",
    "drink": "Desafío de bebida", "orlog": "Victoria de Orlog", "stealth": "Sigilo",
    "boost": "Multiplicador de plata y experiencia", "map": "Descubrir iconos del mapa",
    "equipment": "Editar equipo", "shops": "Abrir tiendas",
}
BLOCKED = {
    "equipment": "La tabla usa un gancho dentro de un hilo del juego; no se ha portado una liberación segura y verificable.",
    "shops": "La tabla original declara esta función averiada y capaz de cerrar el juego. No está habilitada.",
}
MAX_INVENTORY = 32767
MAX_QUANTITY = MAX_STACK_QUANTITY


def normalize_hash(value: int | str) -> int:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        if not re.fullmatch(r"[0-9A-Fa-f]{1,16}", cleaned):
            raise ValueError("El hash debe ser un hexadecimal de 1 a 16 dígitos")
        result = int(cleaned, 16)
    elif type(value) is int:
        result = value
    else:
        raise ValueError("Hash de objeto no válido")
    if not 0 < result <= 0xffffffffffffffff:
        raise ValueError("El hash debe ser un hexadecimal de 1 a 16 dígitos")
    return result


def _cancelled(cancel) -> bool:
    return bool(cancel and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()))


class GameSession:
    def __init__(self, pid: int | None = None, *, memory=None,
                 names: Mapping | None = None, quantity_policy=None):
        self.memory = memory
        self.names = {normalize_hash(k): v for k, v in (names or {}).items()}
        self.quantity_policy = quantity_policy if quantity_policy is not None else QuantityPolicy()
        # The desktop app enables this before granting anything. Keeping the
        # low-level default explicit preserves compatibility for isolated callers.
        self.require_quantity_snapshot = False
        self.features = {key: FeatureState(key, title) for key, title in TITLES.items()}
        self._addresses: dict[str, int] = {}
        self._patches: dict[str, list[Patch]] = {}
        self._generation = 0
        self._vector = None
        self._lock = threading.RLock()
        if pid is not None:
            self.attach(pid)

    @property
    def tainted(self) -> bool:
        return bool(self.memory and self.memory.tainted)

    @property
    def identity(self):
        return self.memory.identity if self.memory else None

    def attach(self, pid: int):
        with self._lock:
            if self.memory is not None:
                self.close()
            self.memory = ProcessMemory(pid)
            self._addresses.clear()
            self._patches.clear()
            self._vector = None
            self._generation += 1
            self.features = {key: FeatureState(key, title) for key, title in TITLES.items()}
        return self

    def _alive(self):
        if not self.memory or not self.memory.is_alive():
            raise CompatibilityError("El juego no está conectado o ha terminado")
        if self.memory.tainted:
            raise CompatibilityError("Una operación remota no terminó; cierra el juego antes de reconectar")

    def diagnose(self, cancel=None, progress: Callable | None = None):
        with self._lock:
            self._alive()
            scanned = {}
            for number, (key, state) in enumerate(self.features.items(), 1):
                if _cancelled(cancel):
                    break
                if key in BLOCKED:
                    state.available, state.reason = False, BLOCKED[key]
                else:
                    try:
                        if key in self._patches:
                            for patch in self._patches[key]:
                                if self.memory.read(patch.address, len(patch.replacement)) != patch.replacement:
                                    raise CompatibilityError("El código activado fue modificado por otra herramienta")
                        else:
                            resolved = {}
                            for name in FEATURE_PATTERNS[key]:
                                if name not in scanned:
                                    scanned[name] = self.memory.scan_unique(PATTERNS[name],
                                                                           cancel=lambda: _cancelled(cancel))
                                resolved[name] = scanned[name]
                            self._addresses.update(resolved)
                            if key in ("persistence", "horse", "drink", "orlog", "stealth"):
                                self._make_patches(key)
                        state.available = True
                        state.reason = "Firmas encontradas; funcionamiento en juego pendiente de comprobar"
                    except (MemoryAccessError, CompatibilityError, ValueError) as exc:
                        state.available, state.reason = False, str(exc)
                if progress:
                    progress(number, len(self.features), state.title)
            return dict(self.features)

    def _require(self, key: str):
        self._alive()
        state = self.features.get(key)
        if not state or not state.available:
            raise CompatibilityError(state.reason if state else "Función desconocida")

    def _rip(self, key: str, offset=0, displacement=3, length=7):
        return self.memory.resolve_rip(self._addresses[key] + offset,
                                       disp_offset=displacement, instruction_size=length)

    def _run(self, source: str, timeout_ms=10000) -> tuple[int, int]:
        self._alive()
        data = self.memory.alloc(64)
        code = None
        try:
            self.memory.write(data, bytes(64))
            bytecode = assembly.assemble(source)
            code = self.memory.alloc(len(bytecode), executable=True)
            self.memory.write_code(code, bytecode)
            exit_code = self.memory.run_thread(code, parameter=data, timeout_ms=timeout_ms)
            if exit_code != 0:
                # An exception may have skipped flag restoration or game TLS
                # teardown even though the operating-system thread has exited.
                self.memory.tainted = True
                raise CompatibilityError(f"La operación del juego terminó con código 0x{exit_code:08X}")
            return struct.unpack("<QQ", self.memory.read(data, 16))
        finally:
            # A timed-out worker still owns code/data. ProcessMemory quarantines
            # this session; never reclaim its stack arguments while it is live.
            if not self.memory.tainted:
                if code is not None:
                    self.memory.free(code)
                self.memory.free(data)

    def _thread_functions(self, key: str):
        global_address = self._rip(key)
        manager = self.memory.u64(global_address)
        if not manager:
            raise CompatibilityError("El sistema del juego todavía no está preparado")
        vtable = self.memory.u64(manager)
        create, destroy = self.memory.u64(vtable + 0x18), self.memory.u64(vtable + 0x20)
        if not create or not destroy:
            raise CompatibilityError("No se encontraron las funciones del hilo del juego")
        self._validate_function(create)
        self._validate_function(destroy)
        return create, destroy

    def _validate_function(self, address: int):
        if not self.memory.base <= address < self.memory.base + self.memory.size:
            raise CompatibilityError("Una función resuelta apunta fuera del módulo del juego")
        if not self.memory.is_executable(address):
            raise CompatibilityError("Una función resuelta no apunta a memoria ejecutable")

    def _resolve_inventory(self) -> tuple[int, int, int]:
        call1 = self._rip("inv4", displacement=1, length=5)
        call2 = self._rip("inv2", offset=0x1a, displacement=1, length=5)
        index = self._rip("inv3")
        self._validate_function(call1)
        self._validate_function(call2)
        status, vector = self._run(assembly.inventory_worker(call1, call2, index))
        if status != 1 or not vector:
            raise CompatibilityError("Inventario no disponible. Carga una partida y vuelve a leerlo")
        begin, packed_count = struct.unpack("<QI", self.memory.read(vector, 12))
        count = packed_count & 0x7fff
        if count > MAX_INVENTORY or (count and not begin):
            raise CompatibilityError("Estructura de inventario no válida")
        return vector, begin, count

    def list_inventory(self) -> list[InventoryItem]:
        with self._lock:
            self._require("inventory")
            for _ in range(2):
                vector, begin, count = signature = self._resolve_inventory()
                raw = self.memory.read(begin, count * 0x48) if count else b""
                items = []
                for index in range(count):
                    quantity = struct.unpack_from("<I", raw, index * 0x48)[0]
                    settings = struct.unpack_from("<Q", raw, index * 0x48 + 8)[0]
                    if not settings:
                        continue
                    hash_id = self.memory.u64(settings + 0x10)
                    if hash_id:
                        items.append((hash_id, quantity, begin + index * 0x48, settings))
                after = self._resolve_inventory()
                if after == signature:
                    if self._vector != signature:
                        self._generation += 1
                        self._vector = signature
                    return [InventoryItem(h, self.names.get(h, f"Objeto {h:016X}"), q, a,
                                          self.identity, self._generation, signature, s)
                            for h, q, a, s in items]
            raise StaleInventoryError("El inventario cambió durante la lectura. Vuelve a cargar la lista")

    def set_quantity(self, item: InventoryItem, new_quantity: int) -> InventoryItem:
        with self._lock:
            self._require("inventory")
            if item.hash_id == RATIONS_HASH:
                raise CompatibilityError("Las raciones requieren un tratamiento especial. No se modifica su cantidad directamente; utiliza su pack del catálogo y revisa el resultado en la partida.")
            maximum = self.quantity_policy.limit(item.hash_id)
            if isinstance(new_quantity, bool) or not isinstance(new_quantity, int) or not 0 <= new_quantity <= maximum:
                raise ValueError(f"La cantidad de este objeto debe estar entre 0 y {maximum}")
            if item.identity != self.identity:
                raise StaleInventoryError("La fila pertenece a otra sesión del juego")
            rows = self.list_inventory()
            current = next((row for row in rows if row.address == item.address and
                            row.hash_id == item.hash_id and row.settings == item.settings), None)
            if (current is None or current.vector != item.vector or
                    current.generation != item.generation or current.quantity != item.quantity):
                raise StaleInventoryError("El inventario cambió; vuelve a leerlo antes de editar")
            if self.quantity_policy.is_unique(item.hash_id) and new_quantity:
                other_quantity = sum(row.quantity for row in rows
                                     if row.hash_id == item.hash_id and row.address != item.address)
                if other_quantity + new_quantity > maximum:
                    raise ValueError("Ya existe otro registro de este equipo. No se incrementa su cantidad para evitar duplicados.")
            if (self._resolve_inventory() != item.vector or
                    self.memory.u64(item.address + 8) != item.settings or
                    self.memory.u64(item.settings + 0x10) != item.hash_id or
                    self.memory.u32(item.address) != item.quantity):
                raise StaleInventoryError("La dirección ya no corresponde al objeto seleccionado")
            self.memory.write(item.address, struct.pack("<I", new_quantity))
            if self.memory.u32(item.address) != new_quantity:
                raise MemoryAccessError("No se pudo verificar la cantidad escrita")
            if (self._resolve_inventory() != item.vector or
                    self.memory.u64(item.address + 8) != item.settings or
                    self.memory.u64(item.settings + 0x10) != item.hash_id):
                raise StaleInventoryError("El inventario cambió durante la escritura; vuelve a leerlo para comprobar el resultado")
            return replace(current, quantity=new_quantity)

    def grant_items(self, hashes, quantity=1, cancel=None, progress=None) -> GrantResult:
        with self._lock:
            self._require("grant")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
                raise ValueError(f"La cantidad debe estar entre 1 y {MAX_QUANTITY}")
            requested = list(dict.fromkeys(normalize_hash(h) for h in hashes))
            if not requested or len(requested) > 10000:
                raise ValueError("Selecciona entre 1 y 10000 objetos diferentes")
            result = GrantResult(len(requested))
            unique_hashes = {h for h in requested if self.quantity_policy.is_unique(h)}
            requires_snapshot = self.require_quantity_snapshot or bool(unique_hashes)
            if requires_snapshot and not self.features["inventory"].available:
                raise CompatibilityError("Hace falta leer el inventario para comprobar la cantidad actual antes de añadir objetos. No se envió la solicitud.")
            before = None
            if self.features["inventory"].available:
                try:
                    identity = self.identity
                    snapshot = self.list_inventory()
                    if self.require_quantity_snapshot and (not snapshot or self.identity != identity):
                        raise CompatibilityError("No se pudo confirmar una lectura actual de la partida.")
                    before = self._totals(snapshot)
                    if self.require_quantity_snapshot:
                        self._alive()
                except (MemoryAccessError, CompatibilityError, StaleInventoryError) as exc:
                    if requires_snapshot:
                        raise CompatibilityError("No se pudo comprobar la cantidad actual. Vuelve a leer el inventario antes de añadir objetos.") from exc
                    result.warnings.append(f"No se pudo leer el inventario antes de añadir: {exc}")
            worker_context = None
            sent_quantities = {}
            for number, hash_id in enumerate(requested, 1):
                if _cancelled(cancel):
                    result.cancelled = True
                    break
                maximum = self.quantity_policy.limit(hash_id)
                item_quantity = 1 if hash_id in unique_hashes else quantity
                if item_quantity > maximum:
                    raise ValueError(f"La solicitud excede el límite permitido para este objeto ({maximum}).")
                if hash_id in unique_hashes:
                    identity = self.identity
                    fresh = self.list_inventory()
                    if not fresh or self.identity != identity:
                        raise CompatibilityError("No se pudo confirmar una lectura actual de la partida. No se solicitó el equipo único.")
                    current = self._totals(fresh).get(hash_id, 0)
                    self._alive()
                    if current > 0:
                        result.skipped += 1
                        if progress:
                            progress(number, result.requested, f"{hash_id:016X}")
                        continue
                    before[hash_id] = current
                if before is not None and hash_id in before and before[hash_id] + item_quantity > maximum:
                    raise ValueError(f"La cantidad actual más la solicitud excede el rango permitido para este objeto ({maximum}). No se envió la solicitud.")
                # Resolve the game call only when there is an actual request.
                # A pack made entirely of owned equipment performs no worker.
                if worker_context is None:
                    create, destroy = self._thread_functions("grant_thread")
                    self._validate_function(self._addresses["grant_call"])
                    world = self._rip("grant_world")
                    if not self.memory.u64(world):
                        raise CompatibilityError("Carga una partida antes de añadir objetos")
                    worker_context = create, destroy, world
                create, destroy, world = worker_context
                status, _ = self._run(assembly.grant_worker(self._addresses["grant_call"], world,
                                                          create, destroy, hash_id, item_quantity))
                if status != 1:
                    raise CompatibilityError("El juego no estaba preparado para recibir el objeto")
                result.completed += 1
                result.hashes.append(hash_id)
                result.adjusted += int(item_quantity != quantity)
                sent_quantities[hash_id] = item_quantity
                if progress:
                    progress(number, result.requested, f"{hash_id:016X}")
            if before is not None:
                try:
                    after = self._totals(self.list_inventory())
                    result.confirmed = sum(after.get(h, 0) >= before.get(h, 0) + sent_quantities[h] for h in result.hashes)
                except (MemoryAccessError, CompatibilityError, StaleInventoryError) as exc:
                    result.warnings.append(f"No se pudo verificar el inventario final: {exc}")
            if result.confirmed < result.completed:
                result.warnings.append(f"El juego respondió a {result.completed} solicitudes; se verificaron {result.confirmed} aumentos. Los objetos únicos, desbloqueos y duplicados pueden no aumentar la cantidad.")
            if result.skipped:
                result.warnings.append(f"Se omitieron {result.skipped} objetos de equipo que ya estaban presentes; no se solicitaron duplicados.")
            if result.adjusted:
                result.warnings.append(f"Se ajustó a una unidad la cantidad de {result.adjusted} objetos de equipo único.")
            return result

    @staticmethod
    def _totals(items):
        result = {}
        for item in items:
            result[item.hash_id] = result.get(item.hash_id, 0) + item.quantity
        return result

    def _make_patches(self, key: str) -> list[Patch]:
        definitions = {
            "horse": (("horse", 0, bytes.fromhex("48 8D 05 73 FF 99 FE"), bytes.fromhex("48 8B 07 48 8B 40 40")),),
            "drink": (("drink", 0, bytes.fromhex("80 7C 24 30 00"), bytes.fromhex("48 83 C8 FF 90")),),
            "orlog": (("orlog", 0xd, bytes.fromhex("49 8B 81 F0 03 00 00 83 78 04 00 74 1E"), bytes.fromhex("49 8B 81 F8 03 00 00 31 C9 89 48 04 90")),),
            "stealth": (("stealth", 0, bytes.fromhex("80 78 7E 00"), bytes.fromhex("80 78 7E 01")),),
        }
        if key == "persistence":
            address = self._addresses["persist1"]
            original = self.memory.read(address, 7)
            if original[:3] != bytes.fromhex("48 8D 0D"):
                raise CompatibilityError("El primer parche de persistencia no conserva las instrucciones originales esperadas")
            definitions[key] = (("persist1", 0, original, bytes.fromhex("48 8B 0E 48 8B 49 40")),
                                ("persist2", 0, bytes.fromhex("48 8B 13"), bytes.fromhex("48 31 D2")))
        patches = []
        for name, offset, expected, replacement in definitions[key]:
            address = self._addresses[name] + offset
            if self.memory.read(address, len(expected)) != expected:
                raise CompatibilityError("El código ya está modificado o pertenece a una versión incompatible")
            patches.append(Patch(address, expected, replacement))
        return patches

    def toggle(self, feature: str, enabled: bool):
        with self._lock:
            self._require(feature)
            if feature not in ("persistence", "horse", "drink", "orlog", "stealth"):
                raise CompatibilityError("Esta función no es un interruptor reversible")
            if enabled and feature not in self._patches:
                patches = self._make_patches(feature)
                applied = []
                try:
                    for patch in patches:
                        self.memory.write_code(patch.address, patch.replacement, expected=patch.original)
                        applied.append(patch)
                except Exception:
                    unrestored = []
                    for patch in reversed(applied):
                        try:
                            self.memory.write_code(patch.address, patch.original, expected=patch.replacement)
                        except Exception:
                            unrestored.append(patch)
                    if unrestored:
                        self._patches[feature] = unrestored
                        self.features[feature].enabled = True
                    raise
                self._patches[feature] = patches
            elif not enabled and feature in self._patches:
                remaining = list(self._patches[feature])
                for patch in reversed(self._patches[feature]):
                    self.memory.write_code(patch.address, patch.original, expected=patch.replacement)
                    remaining.remove(patch)
                    self._patches[feature] = remaining
                del self._patches[feature]
            self.features[feature].enabled = feature in self._patches
            return self.features[feature]

    def boost(self, multiplier: float):
        with self._lock:
            self._require("boost")
            multiplier = float(multiplier)
            if not math.isfinite(multiplier) or not 1 <= multiplier <= 100:
                raise ValueError("El multiplicador debe estar entre 1 y 100")
            create, destroy = self._thread_functions("boost_thread")
            self._validate_function(self._addresses["boost_call"])
            status, _ = self._run(assembly.boost_worker(self._addresses["boost_call"],
                                self._rip("boost_owner"), create, destroy, multiplier))
            if status != 1:
                raise CompatibilityError("No se aplicó el multiplicador: carga una partida")
            return "La llamada al multiplicador terminó; comprueba los indicadores de plata y experiencia en el juego."

    def unlock_map(self, cancel=None, progress=None):
        with self._lock:
            self._require("map")
            args = dict(environment_global=self._rip("map", displacement=3, length=8),
                        get_world=self._rip("map", 0xd, 1, 5),
                        get_module=self._rip("map", 0x1a, 1, 5),
                        service=self._rip("map", 0x22),
                        get_item=self._rip("map", 0x2f, 1, 5),
                        reveal=self._rip("map", 0x48, 1, 5),
                        release=self._rip("map", 0x54, 1, 5))
            for key in ("get_world", "get_module", "get_item", "reveal", "release"):
                self._validate_function(args[key])
            completed = 0
            for index, hash_id in enumerate(range(0x000001D5B2E91BF5, 0x000001D5B2E91BF9), 1):
                if _cancelled(cancel):
                    break
                status, _ = self._run(assembly.map_worker(**args, hash_id=hash_id))
                if status != 1:
                    raise CompatibilityError("El juego no pudo resolver un marcador del mapa; no se declara completado")
                completed += 1
                if progress:
                    progress(index, 4, f"Mapa {index}/4")
            return completed

    def read_equipment(self):
        raise CompatibilityError(BLOCKED["equipment"])

    def write_equipment(self, *args, **kwargs):
        raise CompatibilityError(BLOCKED["equipment"])

    def close(self):
        with self._lock:
            if self.memory is None:
                return {"restored": True, "pending": False, "tainted": False}
            errors = []
            restored = not self._patches
            if self.memory.is_alive() and not self.memory.tainted:
                for key in list(self._patches):
                    try:
                        self.toggle(key, False)
                    except Exception as exc:
                        errors.append(f"{TITLES[key]}: {exc}")
                restored = not self._patches
            if not self.memory.is_alive():
                restored = True  # The old address space no longer exists.
            closed = self.memory.close()
            if errors:
                raise CompatibilityError("No se pudieron restaurar todos los cambios; reinicia el juego. " + "; ".join(errors))
            return {"restored": restored, "pending": not closed, "tainted": self.memory.tainted}
