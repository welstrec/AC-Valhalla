"""English/Spanish presentation without changing catalogue or game identifiers.

Spanish source strings are the stable translation keys. Backend exceptions may
remain in their source language until presentation; ``message`` handles their
registered formatted templates without interpreting captured names or paths.
"""
from __future__ import annotations

from functools import lru_cache
from collections import OrderedDict
import json
import logging
import os
from pathlib import Path
import re
import string
import tempfile
import time

from .paths import user_dir

LOG = logging.getLogger(__name__)
SUPPORTED_LANGUAGES = ("en", "es")
LANGUAGE_NAMES = {"en": "English", "es": "Español"}
_FORMATTER = string.Formatter()
_MAX_SETTINGS_BYTES = 65536


def normalize_language(language: str) -> str:
    if not isinstance(language, str):
        raise ValueError("Language must be 'en' or 'es'.")
    value = language.strip().lower().replace("_", "-").split("-", 1)[0]
    if value not in SUPPORTED_LANGUAGES:
        raise ValueError("Language must be 'en' or 'es'.")
    return value


def _settings_path(settings_path=None) -> Path:
    return Path(settings_path) if settings_path is not None else user_dir() / "settings.json"


def _read_settings(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("The language settings file must not be a symbolic link.")
    if not path.exists():
        return {}
    if path.stat().st_size > _MAX_SETTINGS_BYTES:
        raise ValueError("The language settings file is too large.")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("The language settings must be a JSON object.")
    return document


def load_language(settings_path=None) -> str | None:
    try:
        value = _read_settings(_settings_path(settings_path)).get("language")
        return normalize_language(value) if value is not None else None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        LOG.warning("The saved language preference could not be loaded.")
        return None


def save_language(language: str, settings_path=None) -> None:
    """Atomically merge one preference, keeping unrelated settings intact."""
    value = normalize_language(language)
    path = _settings_path(settings_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("The language settings file must not be a symbolic link.")
    try:
        document = _read_settings(path)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        backup = path.with_name(path.stem + f".invalid-{time.time_ns()}" + path.suffix)
        os.replace(path, backup)
        document = {}
    document["language"] = value
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                        prefix="language-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _system_language() -> str:
    from PySide6.QtCore import QLocale
    return "es" if QLocale.system().language() == QLocale.Language.Spanish else "en"


def resolve_language(explicit=None, settings_path=None) -> str:
    if explicit is not None:
        return normalize_language(explicit)
    return load_language(settings_path) or _system_language()


@lru_cache(maxsize=1)
def _catalogue() -> dict[str, str]:
    translations = {}
    folder = Path(__file__).resolve().parent / "translations"
    for path in sorted(folder.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"Invalid translation catalogue: {path.name}")
        for source, target in document.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError(f"Invalid translation in {path.name}")
            if source in translations and translations[source] != target:
                raise ValueError(f"Conflicting translation key: {source!r}")
            translations[source] = target
    for source, target in list(translations.items()):
        if "&" in source:
            translations.setdefault(source.replace("&", ""), target.replace("&", ""))
    return translations


def _template_pattern(source):
    pieces, fields = [], []
    segments = list(_FORMATTER.parse(source))
    for index, (literal, field, spec, conversion) in enumerate(segments):
        pieces.append(re.escape(literal))
        if field is not None:
            group = "v" + str(len(fields))
            following = segments[index + 1][0] if index + 1 < len(segments) else ""
            # Counts must not consume whole sentences ending in "objetos".
            # Otherwise a generic count template masks a more specific error.
            counted = re.match(r" (?:objetos|packs|solicitudes|registros|unidades|recursos)(?:\b|$)", following)
            capture = r"[0-9][0-9.,]*" if counted else ".*?"
            pieces.append(f"(?P<{group}>{capture})")
            fields.append(field)
    return re.compile("".join(pieces), re.DOTALL), fields


_CATEGORIES = {
    "ARMOR": ("Armaduras", "Armor"),
    "BEARD & HAIR": ("Barba y cabello", "Beard and hair"),
    "CROSSOVER STORIES": ("Historias de crossover", "Crossover stories"),
    "DAWN OF RAGNARÖK": ("Dawn of Ragnarök", "Dawn of Ragnarök"),
    "DESTINY CROSSOVER": ("Crossover de Destiny", "Destiny crossover"),
    "HELIX ITEMS": ("Objetos Helix", "Helix items"),
    "HILDIRAN ITEMS": ("Objetos de Hildiran", "Hildiran items"),
    "MYTHICAL WEAPONS": ("Armas míticas", "Mythical weapons"),
    "PRIME GAMING": ("Prime Gaming", "Prime Gaming"),
    "RIDEABLES & EAGLES": ("Monturas y aves", "Mounts and birds"),
    "RIVER RAIDS": ("Incursiones fluviales", "River raids"),
    "RUNES": ("Runas", "Runes"),
    "SEASONAL EVENTS": ("Eventos de temporada", "Seasonal events"),
    "SETTLEMENT": ("Asentamiento", "Settlement"),
    "SHIPS": ("Barcos", "Ships"),
    "SIEGE OF PARIS": ("Siege of Paris", "Siege of Paris"),
    "TATTOOS": ("Tatuajes", "Tattoos"),
    "THE FINAL CHAPTER": ("The Final Chapter", "The Final Chapter"),
    "THE FORGOTTEN SAGA": ("The Forgotten Saga", "The Forgotten Saga"),
    "TOMBS OF THE FALLEN": ("Tombs of the Fallen", "Tombs of the Fallen"),
    "UBISOFT CLUB & COMMUNITY CHALLENGES": ("Ubisoft Club y desafíos de comunidad", "Ubisoft Club and community challenges"),
    "UTILITIES": ("Utilidades", "Utilities"),
    "WEAPONS": ("Armas", "Weapons"),
    "WRATH OF THE DRUIDS": ("Wrath of the Druids", "Wrath of the Druids"),
}


class Translator:
    """A per-window translator; changing it never changes game/catalogue data."""

    def __init__(self, language="es"):
        self.language = normalize_language(language)
        self._cache = OrderedDict()
        self.translations = dict(_catalogue())
        self._patterns = []
        for source, target in self.translations.items():
            try:
                if any(field is not None for _, field, _, _ in _FORMATTER.parse(source)):
                    pattern, fields = _template_pattern(source)
                    literal_length = len(re.sub(r"\{[^{}]*\}", "", source))
                    if literal_length >= 3 or source == "{0}: {1}":
                        self._patterns.append((literal_length, pattern, fields, target))
            except ValueError:
                continue
        self._patterns.sort(key=lambda row: row[0], reverse=True)
        self._fragments = sorted(((source, target) for source, target in self.translations.items()
                                  if len(source) >= 18 and " " in source and "{" not in source
                                  and source != target), key=lambda pair: len(pair[0]), reverse=True)

    def set_language(self, language):
        self.language = normalize_language(language)

    def text(self, source: str, **kwargs) -> str:
        if not kwargs:
            return self.message(source)
        if self.language == "es":
            return source.format(**kwargs)
        exact = self.translations.get(source)
        if exact is not None:
            return exact.format(**kwargs)
        canonical, values = [], []
        for literal, field, spec, conversion in _FORMATTER.parse(source):
            canonical.append(literal)
            if field is not None:
                canonical.append("{" + str(len(values)) + "}")
                expression = "{" + field + ("!" + conversion if conversion else "") + (":" + spec if spec else "") + "}"
                values.append(expression.format(**kwargs))
        translated = self.translations.get("".join(canonical))
        return translated.format(*values) if translated is not None else self.message(source.format(**kwargs))

    def category(self, canonical: str) -> str:
        names = _CATEGORIES.get(canonical)
        return names[0 if self.language == "es" else 1] if names else self.text(canonical)

    def message(self, value: str) -> str:
        value = str(value)
        key = (self.language, value)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        translated = self._message(value, 0)
        self._cache[key] = translated
        if len(self._cache) > 8192:
            self._cache.popitem(last=False)
        return translated

    def _message(self, value: str, depth: int) -> str:
        if self.language == "es" or not value or depth > 5:
            return value
        exact = self.translations.get(value)
        if exact is not None:
            return exact
        timestamp = re.match(r"^(\d{2}:\d{2}:\d{2}\s+)(.+)$", value, re.DOTALL)
        if timestamp:
            return timestamp[1] + self._message(timestamp[2], depth + 1)
        for _, pattern, fields, target in self._patterns:
            matched = pattern.fullmatch(value)
            if matched:
                values = [self._message(matched[f"v{index}"], depth + 1) for index in range(len(fields))]
                named = {field: values[index] for index, field in enumerate(fields) if not field.isdecimal()}
                try:
                    return target.format(*values, **named)
                except (KeyError, IndexError, ValueError):
                    continue
        if value.endswith("…"):
            return self._message(value[:-1], depth + 1) + "…"
        if "\n" in value:
            return "\n".join(self._message(line, depth + 1) for line in value.split("\n"))
        result = value
        for source, target in self._fragments:
            result = result.replace(source, target)
        return result
