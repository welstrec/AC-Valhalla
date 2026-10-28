"""Local visual preferences and an inert, bounded request queue.

Image imports decode raster files and write normalized thumbnails. No URL is
fetched, no SVG is interpreted and no image metadata is treated as executable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from PIL import Image, ImageOps, UnidentifiedImageError

from .paths import user_dir
from .quantities import QuantityPolicy, MAX_STACK_QUANTITY

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_HASH = re.compile(r"(?:0x)?([0-9a-fA-F]{16})\Z", re.IGNORECASE)


def normalize_id(value: str | int) -> str:
    if type(value) is int:
        if not 0 < value <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("El identificador de objeto no es válido.")
        return f"{value:016X}"
    if not isinstance(value, str):
        raise ValueError("El identificador debe contener 16 dígitos hexadecimales.")
    match = _HASH.fullmatch(value.strip())
    if not match or int(match[1], 16) == 0:
        raise ValueError("El identificador debe contener 16 dígitos hexadecimales.")
    return match[1].upper()


@dataclass(frozen=True)
class QueueEntry:
    hash_id: str
    quantity: int
    label: str


class RequestQueue:
    """A review list only; it never calls game code or persists submissions."""

    def __init__(self, max_entries: int = 9999, quantity_policy=None):
        if type(max_entries) is not int or not 1 <= max_entries <= 9999:
            raise ValueError("El límite de la lista debe estar entre 1 y 9999.")
        self.max_entries = max_entries
        self.quantity_policy = quantity_policy if quantity_policy is not None else QuantityPolicy()
        self._entries: dict[str, QueueEntry] = {}

    @property
    def entries(self) -> list[QueueEntry]:
        return list(self._entries.values())

    @property
    def count(self) -> int:
        return len(self._entries)

    def add(self, hashes, quantity: int, label: str = "") -> int:
        if type(quantity) is not int or not 1 <= quantity <= MAX_STACK_QUANTITY:
            raise ValueError(f"La cantidad debe estar entre 1 y {MAX_STACK_QUANTITY}.")
        if not isinstance(label, str) or len(label) > 500:
            raise ValueError("El nombre de la solicitud no es válido.")
        if isinstance(hashes, (str, bytes)):
            raise ValueError("Se requiere una lista de identificadores.")
        normalized = list(dict.fromkeys(normalize_id(value) for value in hashes))
        if not normalized:
            raise ValueError("Selecciona al menos un objeto.")
        if len(set(self._entries).union(normalized)) > self.max_entries:
            raise ValueError(f"La lista admite como máximo {self.max_entries} objetos distintos.")
        # Prepare the complete change before mutating the queue. Repeated IDs
        # use the last selected quantity rather than silently accumulating it.
        updates = {value: QueueEntry(value, min(quantity, self.quantity_policy.limit(value)), label) for value in normalized}
        self._entries.update(updates)
        return self.count

    def remove(self, hash_id: str | int) -> bool:
        return self._entries.pop(normalize_id(hash_id), None) is not None

    def clear(self) -> None:
        self._entries.clear()


class UserLibrary:
    SCHEMA_VERSION = 1

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root if root is not None else user_dir() / "library").expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.images = self.root / "images"
        self.images.mkdir(exist_ok=True)
        if not self.images.is_dir() or self.images.resolve().parent != self.root:
            raise ValueError("La carpeta de imágenes no pertenece a la biblioteca local.")
        self.manifest = self.root / "library.json"
        self.warnings: list[str] = []
        self._favorites: set[str] = set()
        self._images: dict[str, str] = {}
        self._lock = threading.RLock()
        self._blocked = self.manifest.is_symlink()
        if self._blocked:
            self.warnings.append("El archivo de biblioteca es un enlace externo; no se utilizará ni se sobrescribirá.")
        else:
            self._load()

    @property
    def favorites(self) -> set[str]:
        with self._lock:
            return set(self._favorites)

    def is_favorite(self, hash_id: str | int) -> bool:
        with self._lock:
            return normalize_id(hash_id) in self._favorites

    def _relative_image(self, hash_id: str) -> str:
        return f"images/{hash_id}.png"

    def _owned_image(self, hash_id: str) -> Path:
        path = self.images / f"{hash_id}.png"
        if self.images.resolve().parent != self.root or path.resolve().parent != self.images.resolve():
            raise ValueError("La ruta de la imagen está fuera de la biblioteca local.")
        return path

    def _preserve_invalid(self) -> None:
        backup = self.root / f"library.invalid-{time.time_ns()}.json"
        try:
            os.replace(self.manifest, backup)
            self.warnings.append(f"La configuración anterior se conservó en {backup.name}.")
        except OSError as exc:
            self._blocked = True
            self.warnings.append(f"No se pudo conservar la configuración anterior: {exc}. Se bloqueó su escritura.")

    def _load(self) -> None:
        if not self.manifest.exists():
            return
        malformed = False
        try:
            if self.manifest.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError("El archivo de biblioteca supera el límite de tamaño.")
            document = json.loads(self.manifest.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError("La versión de biblioteca no es compatible.")
            favorites, images = document.get("favorites", []), document.get("images", {})
            if not isinstance(favorites, list) or not isinstance(images, dict) or len(favorites) > 10000 or len(images) > 10000:
                raise ValueError("La estructura de biblioteca no es válida.")
            for value in favorites:
                try:
                    self._favorites.add(normalize_id(value))
                except ValueError:
                    malformed = True
            for value, relative in images.items():
                try:
                    hash_id = normalize_id(value)
                    if relative != self._relative_image(hash_id):
                        raise ValueError("Ruta de imagen inesperada")
                    self._owned_image(hash_id)
                    self._images[hash_id] = relative
                except ValueError:
                    malformed = True
            if malformed:
                self.warnings.append("Se ignoraron identificadores o rutas no válidos de la biblioteca.")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            malformed = True
            self.warnings.append(f"No se pudo cargar la biblioteca: {exc}")
        if malformed:
            self._preserve_invalid()

    def _save(self, favorites: set[str], images: dict[str, str]) -> None:
        if self._blocked or self.manifest.is_symlink():
            raise OSError("La biblioteca no se puede guardar sin sobrescribir una configuración protegida.")
        document = {"schema_version": self.SCHEMA_VERSION,
                    "favorites": sorted(favorites), "images": dict(sorted(images.items()))}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root,
                    prefix="library-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.manifest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def toggle_favorite(self, hash_id: str | int) -> bool:
        hash_id = normalize_id(hash_id)
        with self._lock:
            updated = self._favorites.symmetric_difference({hash_id})
            self._save(updated, self._images)
            self._favorites = updated
            return hash_id in self._favorites

    def image_path(self, hash_id: str | int) -> Path | None:
        hash_id = normalize_id(hash_id)
        with self._lock:
            if hash_id not in self._images:
                return None
            try:
                path = self._owned_image(hash_id)
                return path if path.is_file() else None
            except ValueError:
                return None

    def set_image(self, hash_id: str | int, source: Path | str) -> Path:
        hash_id = normalize_id(hash_id)
        source = Path(source)
        if source.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Selecciona una imagen PNG, JPEG o WebP estática.")
        if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("La imagen no existe o supera los 20 MB.")
        try:
            with source.open("rb") as stream:
                if os.fstat(stream.fileno()).st_size > MAX_IMAGE_BYTES:
                    raise ValueError("La imagen supera los 20 MB.")
                with Image.open(stream) as original:
                    if original.format not in {"PNG", "JPEG", "WEBP"}:
                        raise ValueError("El contenido del archivo no es PNG, JPEG ni WebP.")
                    width, height = original.size
                    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                        raise ValueError("La imagen supera los 20 millones de píxeles.")
                    if getattr(original, "n_frames", 1) != 1:
                        raise ValueError("La imagen debe ser estática; no se admiten animaciones.")
                    normalized = ImageOps.exif_transpose(original).convert("RGBA")
                    normalized.info.clear()
                    normalized.thumbnail((512, 512), Image.Resampling.LANCZOS)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            raise ValueError("No se pudo leer una imagen raster válida.") from exc
        with self._lock:
            if self._blocked:
                raise OSError("La configuración de biblioteca está protegida.")
            target = self._owned_image(hash_id)
            temporary = None
            previous = target.read_bytes() if target.is_file() else None
            try:
                descriptor, name = tempfile.mkstemp(dir=self.images, prefix="image-", suffix=".tmp")
                os.close(descriptor)
                temporary = Path(name)
                normalized.save(temporary, format="PNG")
                os.replace(temporary, target)
                updated = {**self._images, hash_id: self._relative_image(hash_id)}
                try:
                    self._save(self._favorites, updated)
                except Exception:
                    if previous is None:
                        target.unlink(missing_ok=True)
                    else:
                        temporary.write_bytes(previous)
                        os.replace(temporary, target)
                    raise
                self._images = updated
                return target
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def clear_image(self, hash_id: str | int) -> None:
        hash_id = normalize_id(hash_id)
        with self._lock:
            updated = {key: value for key, value in self._images.items() if key != hash_id}
            self._save(self._favorites, updated)
            self._images = updated
            try:
                self._owned_image(hash_id).unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                self.warnings.append(f"La imagen se desvinculó, pero no se pudo eliminar el archivo: {exc}")
