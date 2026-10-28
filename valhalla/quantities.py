"""Quantity rules derived from inert catalogue data, never from artwork.

One unit for identified equipment is the user's duplicate-prevention policy.
It is not a native capacity discovered in game memory: the source CT explicitly
describes creating duplicate items. Other records retain the signed 32-bit edit
range already supported by the engine until a smaller game limit is verified.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .resources import BASE_NAMES, RATIONS_HASH


MAX_STACK_QUANTITY = 2_147_483_647
MIN_EDIT_QUANTITY = 0
MIN_REQUEST_QUANTITY = 1
_HASH = re.compile(r"(?:0x)?([0-9a-f]{16})\Z", re.IGNORECASE)
_EQUIPMENT_CATEGORIES = {"armor", "armour", "weapons", "mythical weapons"}
_NON_EQUIPMENT_CATEGORIES = {
    "runes", "tattoos", "ships", "settlement", "beard & hair", "rideables & eagles",
}
_NON_EQUIPMENT_WORDS = {
    "rune", "runes", "runestone", "runestones", "tattoo", "tattoos", "scheme",
    "arrow", "arrows", "ingot", "ingots", "ore", "supplies", "supply",
    "material", "materials", "resource", "resources", "token", "tokens",
    "currency", "currencies", "coin", "coins", "fabric", "fish", "decoration",
    "decorations", "ornament", "ornaments", "statue", "ship", "hull", "sail",
    "figurehead", "tailpiece", "hair", "beard", "hairstyle", "shields",
}
_EQUIPMENT_WORDS = {
    "armor", "armour", "helmet", "helm", "headpiece", "mask", "eyepatch",
    "hood", "cloak", "cape", "cowl", "bracer", "bracers", "gauntlet", "gauntlets",
    "wristguards", "gloves", "vambraces", "vambracers", "pants", "trousers", "breeches",
    "leggings", "greaves", "grieves", "boots", "boot", "chestpiece", "torso", "tunic",
    "outfit", "robe", "breastplate", "cuirass", "sword", "swords", "greatsword",
    "blade", "blades", "saber", "sabre", "katana", "khopesh", "kopesh", "claymore",
    "rapier", "scimitar", "axe", "axes", "hatchet", "bardiche", "dagger", "daggers",
    "seax", "knife", "knives", "spear", "spears", "pike", "lance", "halberd",
    "atgeir", "hammer", "hammers", "flail", "flails", "scythe", "scythes",
    "sickle", "sickles", "bow", "bows", "longbow", "shortbow", "crossbow",
    "shield", "shields", "buckler", "weapon", "weapons",
}


def _fold(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(character))


def _words(value: str) -> set[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[a-z0-9]+", _fold(spaced)))


def _normalize_id(value: int | str) -> str:
    if type(value) is int:
        if 0 < value <= 0xFFFFFFFFFFFFFFFF:
            return f"{value:016X}"
    elif isinstance(value, str):
        matched = _HASH.fullmatch(value.strip())
        if matched and int(matched[1], 16):
            return matched[1].upper()
    raise ValueError("El identificador de objeto debe ser un entero válido o 16 dígitos hexadecimales.")


@dataclass(frozen=True)
class QuantityRule:
    unique: bool
    label: str
    reason: str
    maximum: int
    limit_verified: bool = False
    source: str = "int32_range"


_UNKNOWN = QuantityRule(
    False, "Sin límite específico comprobado",
    "Rango de edición de 32 bits con signo: 0 a 2.147.483.647. "
    "No se ha comprobado la capacidad de este objeto dentro del juego.",
    MAX_STACK_QUANTITY,
)
_STACKABLE = QuantityRule(
    False, "Recurso o consumible",
    "La tabla identifica un recurso o permite varias unidades. "
    "No se ha comprobado un máximo específico del juego; se conserva el rango de edición de 32 bits con signo.",
    MAX_STACK_QUANTITY,
)
_EQUIPMENT = QuantityRule(
    True, "Equipo: 1 unidad",
    "Equipo identificado en el catálogo. Se solicita como máximo una unidad y se omite si ya existe "
    "para evitar duplicados, según la configuración de esta aplicación. "
    "No es un límite nativo leído del juego.",
    1, source="equipment_policy",
)


class QuantityPolicy:
    """Conservative rules: unknown names never acquire an invented capacity."""

    def __init__(self, catalog=None):
        self._rules: dict[str, QuantityRule] = {}
        if catalog is None:
            return
        stackable = {f"{value:016X}" for value in (*BASE_NAMES, RATIONS_HASH)}
        for pack in catalog.packs:
            if pack.quantity > 1:
                stackable.update(pack.hashes)
        aliases = getattr(catalog, "_search_text", {})
        for item in catalog.items:
            hash_id = _normalize_id(item.hash_id)
            categories = {_fold(value).strip() for value in item.categories}
            if hash_id in stackable:
                self._rules[hash_id] = _STACKABLE
            elif categories.intersection(_EQUIPMENT_CATEGORIES):
                # An explicit source group outranks words in a weapon's proper
                # name, e.g. "Rune Shield" is listed under WEAPONS.
                self._rules[hash_id] = _EQUIPMENT
            elif categories.intersection(_NON_EQUIPMENT_CATEGORIES):
                self._rules[hash_id] = _STACKABLE if "runes" in categories else _UNKNOWN
            else:
                words = _words(item.name) | _words(aliases.get(hash_id, ""))
                # The source also uses positional names for tattoo schemes,
                # e.g. "Hel Skein (Torso)". They are not wearable torso gear.
                tattoo_position = re.search(r"\((?:torso|front|back|head|left arm|right arm)\)",
                                           item.name, re.IGNORECASE)
                if words.intersection(_NON_EQUIPMENT_WORDS) or tattoo_position:
                    self._rules[hash_id] = _UNKNOWN
                elif words.intersection(_EQUIPMENT_WORDS):
                    self._rules[hash_id] = _EQUIPMENT
                else:
                    self._rules[hash_id] = _UNKNOWN

    def rule(self, hash_id: int | str) -> QuantityRule:
        return self._rules.get(_normalize_id(hash_id), _UNKNOWN)

    def is_unique(self, hash_id: int | str) -> bool:
        return self.rule(hash_id).unique

    def limit(self, hash_id: int | str) -> int:
        return self.rule(hash_id).maximum

    def limit_for(self, hashes) -> int:
        """All-equipment batches need one; mixed batches are handled per item."""
        if isinstance(hashes, (str, bytes)):
            raise ValueError("Se requiere una lista de identificadores de objeto.")
        rules = [self.rule(hash_id) for hash_id in hashes]
        return 1 if rules and all(rule.unique for rule in rules) else MAX_STACK_QUANTITY
