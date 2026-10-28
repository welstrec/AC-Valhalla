"""Original, scalable semantic artwork for catalogue entries.

These illustrations describe names and categories. They are never presented as
screenshots, official artwork, item rarity or verified in-game appearance.
No downloaded graphics, fonts, game assets or network service are required.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, Qt, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .i18n import Translator


@dataclass(frozen=True)
class IconDescription:
    kind: str
    label: str
    source: str = "Ilustración semántica"
    exact: bool = False
    theme: str = "neutral"


LABELS = {
    "weapon": "Arma", "sword": "Espada", "axe": "Hacha", "dagger": "Daga", "spear": "Lanza",
    "scythe": "Guadaña", "hammer": "Martillo", "flail": "Mangual",
    "bow": "Arco", "arrow": "Flechas", "shield": "Escudo",
    "helmet": "Casco", "hood": "Capucha", "torso": "Armadura",
    "bracers": "Brazales", "trousers": "Pantalones", "boots": "Botas",
    "horse": "Montura", "bird": "Ave", "wolf": "Lobo", "bear": "Oso",
    "ship": "Barco", "sail": "Vela", "tree": "Decoración",
    "tattoo": "Tatuaje", "rune": "Runa", "ingot": "Lingote",
    "ore": "Mineral", "leather": "Cuero o piel", "cloth": "Tela",
    "currency": "Moneda", "food": "Alimento", "fish": "Pez",
    "gem": "Gema", "key": "Llave", "book": "Libro o documento",
    "token": "Ficha o recompensa", "chest": "Objeto", "beard": "Cabello o barba",
    "bone": "Material de animal", "cat": "Felino", "structure": "Construcción decorativa",
}

# Hand-drawn geometry in a common 128-unit square. Every shape is original to
# this project. __INK__ and __ACCENT__ are thematic colours, never rarity data.
SHAPES = {
    "weapon": '<path d="m34 97 49-59 13-10-5 17-48 59Zm-5-13 27 23M91 97 49 41m-8-12 15 4 8 13-17 5-13-15Z" fill="__INK__" fill-opacity=".18"/><path d="m58 68 21-25m-10 40 15 19" stroke="__ACCENT__"/>',
    "sword": '<path d="M49 84 87 34 96 29 94 40 58 91Z" fill="__INK__"/><path d="m43 78 23 19m-15-9-15 19m-5-2 9 7"/><path d="m63 74 23-30" stroke="__ACCENT__"/>',
    "axe": '<path d="m47 101 33-70" stroke-width="7"/><path d="M72 37 55 31 42 53 66 61 86 69 99 47 84 41l5-14-11-5Z" fill="__INK__"/><path d="m49 47 18 7m12 1 12 4" stroke="__ACCENT__"/>',
    "dagger": '<path d="m59 82 21-43 14-12-3 19-24 41Z" fill="__INK__"/><path d="m48 78 28 15m-17-8-14 22m-7-3 13 8"/><path d="m70 64 12-20" stroke="__ACCENT__"/>',
    "spear": '<path d="m40 106 42-72" stroke-width="5"/><path d="m77 43 5-21 12-6 1 15-12 18Z" fill="__INK__"/><path d="m70 54 14 8m-18-1 14 8" stroke="__ACCENT__"/>',
    "scythe": '<path d="m50 107 22-75" stroke-width="6"/><path d="M70 34c21-10 36 1 39 22-11-12-23-16-42-12Z" fill="__INK__"/><path d="m51 78 15 5" stroke="__ACCENT__"/>',
    "hammer": '<path d="m48 105 27-66" stroke-width="7"/><path d="m61 32 10-10 30 13 1 17-10 10-31-13Z" fill="__INK__"/><path d="m75 30-5 18m17-12-4 15" stroke="__ACCENT__"/>',
    "flail": '<path d="m38 106 16-45" stroke-width="7"/><path d="M54 60c-3-18 20-22 24-12 5 11-10 19-12 6-1-10 13-20 24-14"/><path d="m91 28 5 8 10 3-6 9 1 11-11-3-8 7-3-12-9-6 10-7 1-10Z" fill="__INK__"/>',
    "bow": '<path d="M50 26c51 17 51 59 0 76 20-22 20-53 0-76Z"/><path d="m50 26 10 38-10 38m-13-38h60m-8-7 8 7-8 7" stroke="__ACCENT__"/>',
    "arrow": '<path d="m36 102 52-67m-40 73 52-67"/><path d="m80 39 13-16 2 19m-2 3 12-16 2 19" fill="__INK__"/><path d="m39 88-10 6 9 2 1 10 8-10m3-2-3 13 10-8" stroke="__ACCENT__"/>',
    "shield": '<path d="M64 23 95 35l-4 39c-4 15-15 27-27 35-13-8-24-20-27-35l-4-39Z" fill="__INK__" fill-opacity=".17"/><path d="m64 31 23 9-3 31c-3 12-11 22-20 29-9-7-17-17-20-29l-3-31Z"/><path d="M64 32v65M43 62h42" stroke="__ACCENT__"/><circle cx="64" cy="62" r="9" fill="__INK__"/>',
    "helmet": '<path d="M37 76V60c0-41 54-41 54 0v16l-10 23-14-10V71h-6v18L47 99Z" fill="__INK__" fill-opacity=".17"/><path d="M64 28v42M38 59l24 9m28-9-23 9m-25 7 14 2m16 0 14-2"/><path d="M45 36 33 28l-5 17 13 10m42-19 12-8 5 17-13 10" stroke="__ACCENT__"/>',
    "hood": '<path d="M64 22C40 35 30 66 29 89l22 16 13-13 13 13 22-16C98 66 88 35 64 22Z" fill="__INK__" fill-opacity=".18"/><path d="M64 34C51 44 45 61 43 76l21 14 21-14C83 61 77 44 64 34Z"/><path d="M49 70c8-5 22-5 30 0m-15 6v13" stroke="__ACCENT__"/>',
    "torso": '<path d="m48 28-21 18 11 22 9-6-3 38h40l-3-38 9 6 11-22-21-18c-8 9-24 9-32 0Z" fill="__INK__" fill-opacity=".17"/><path d="M48 28c-1 22 33 22 32 0M47 52l17 11 17-11M46 81h36m-34 9h32"/><path d="M64 63v36m-8-28h16" stroke="__ACCENT__"/>',
    "bracers": '<path d="m29 47 24-10 14 50-24 10Zm35-10 24-9 13 50-24 10Z" fill="__INK__" fill-opacity=".18"/><path d="m34 58 23-9m-18 24 23-9m7-13 23-9m-18 24 22-9"/><path d="m43 49 10 36m25-44 9 35" stroke="__ACCENT__"/>',
    "trousers": '<path d="M42 28h44l-2 73H66l-3-40-4 40H40Z" fill="__INK__" fill-opacity=".18"/><path d="M42 41h43m-22 0v20M41 88h19m6 0h18"/><path d="m46 51 12 7m23-7-12 7" stroke="__ACCENT__"/>',
    "boots": '<path d="M34 28h22l-1 47 12 10v15H29V84l5-10Zm38-7h22l-1 48 12 10v15H67V78l5-9Z" fill="__INK__" fill-opacity=".18"/><path d="M34 39h22m-23 9h22m17-16h22m-22 9h22M29 92h37m1-6h37" stroke="__ACCENT__"/>',
    "horse": '<path d="m39 103 6-35-8-18 8-12 6-17 14 10 21 4 15 23-12 12-17-4-4 17 16 20Z" fill="__INK__" fill-opacity=".18"/><path d="m53 30-1 24 10 11m4-28 11 13m-31 18 14 8m-14 8 12 5"/><circle cx="79" cy="47" r="2.5" fill="__ACCENT__"/><path d="m37 50-9 11 8 17-2 17" stroke="__ACCENT__"/>',
    "bird": '<path d="M62 56c-14-17-29-26-42-27 4 23 15 39 36 45l-5 27 13-10 13 10-5-27c21-6 32-22 36-45-13 1-28 10-42 27V43l13-5-11-7-9 5Z" fill="__INK__" fill-opacity=".18"/><path d="m28 39 25 22m-18-6 18 12m47-28L75 61m18-6L75 67" stroke="__ACCENT__"/>',
    "wolf": '<path d="m32 26 25 15h14l25-15-2 38-13 27-17 14-17-14-13-27Z" fill="__INK__" fill-opacity=".17"/><path d="m35 31 8 27 14-6m36-21-8 27-14-6M47 69l12 6m22-6-12 6m-12 8 7 8 7-8Z"/><path d="m64 91-9 8m9-8 9 8" stroke="__ACCENT__"/>',
    "bear": '<circle cx="38" cy="40" r="13"/><circle cx="90" cy="40" r="13"/><path d="M34 48c0-22 60-22 60 0v26c0 37-60 37-60 0Z" fill="__INK__" fill-opacity=".18"/><path d="m45 62 10 2m18 0 10-2m-30 16c0-17 22-17 22 0v12H53Z"/><path d="m58 76 6 5 6-5m-6 5v9" stroke="__ACCENT__"/>',
    "ship": '<path d="M24 78c23 9 58 9 81-3l-10 21H41L24 78Zm0 0-6-11 9-5 9 7" fill="__INK__" fill-opacity=".2"/><path d="M64 27v53M44 40h40v30H44Z"/><path d="m45 42 37 26m-37 1 37-26M33 103c9-4 12 4 20 0s12 4 20 0 12 4 22 0" stroke="__ACCENT__"/>',
    "sail": '<path d="M66 22v85M37 34h57M42 36c9 17-5 36-8 51h58c-3-15-17-34-8-51Z" fill="__INK__" fill-opacity=".12"/><path d="M44 41h38M41 79h44M65 40 49 58l16 17 16-17Z" stroke="__ACCENT__"/>',
    "tree": '<path d="M64 20 42 48h12L33 72h19l-19 19h24v16h14V91h24L76 72h19L74 48h12Z" fill="__INK__" fill-opacity=".2"/><path d="M64 38v54m0-25-9-9m9 20 11-12M45 109h38" stroke="__ACCENT__"/>',
    "tattoo": '<path d="M65 24c-27 0-39 29-22 46 18 18 48-1 34-17-11-12-29 1-19 10m7-39c28 0 40 29 23 46-6 6-12 9-18 14L64 106 51 85"/><path d="m39 39-12-3 4 13m59-10 12-3-4 13M58 91h12" stroke="__ACCENT__"/>',
    "rune": '<path d="m64 22 31 19-4 48-27 19-27-19-4-48Z" fill="__INK__" fill-opacity=".16"/><path d="M64 38v53m0-51 18 14-18 12m0 0L48 79" stroke="__ACCENT__" stroke-width="5"/><path d="m42 45 5-5m35 48 5-5"/>',
    "ingot": '<path d="m36 48 49-10 18 18-5 27-49 10-24-20Z" fill="__INK__" fill-opacity=".2"/><path d="m36 48 21 19 46-11M57 67l-8 26"/><path d="m49 47 13 12 23-5m-22 24 25-6" stroke="__ACCENT__"/>',
    "ore": '<path d="m27 78 9-32 23-19 24 8 21 35-19 24-38 7Z" fill="__INK__" fill-opacity=".16"/><path d="m36 46 23 13 24-24M59 59l-12 42m12-42 26 35m-26-35 45 11"/><path d="m42 53 11 10-6 16m24-30 9 12 10 6" stroke="__ACCENT__"/>',
    "leather": '<path d="M44 25c11 14 29 14 40 0l18 18-9 17 10 23-17 18c-13-11-31-11-44 0L25 83l10-23-9-17Z" fill="__INK__" fill-opacity=".18"/><path d="M45 42c12 9 26 9 38 0m-39 43c14-8 26-8 40 0M40 52l4 21m40-21-4 21" stroke="__ACCENT__"/>',
    "cloth": '<path d="M34 33h57v54l-8 10-9-6-10 9-11-7-12 6-7-11Z" fill="__INK__" fill-opacity=".18"/><path d="M45 34v53m13-53v55m13-55v55m13-55v52M35 47h54M35 62h54m-54 15h54" stroke="__ACCENT__" stroke-width="1.8"/>',
    "currency": '<ellipse cx="54" cy="82" rx="28" ry="11"/><path d="M26 82v12c0 14 56 14 56 0V82"/><circle cx="76" cy="50" r="26" fill="#132435"/><circle cx="76" cy="50" r="19"/><path d="m76 36 7 14-7 14-7-14Z" fill="__ACCENT__" stroke="__ACCENT__"/>',
    "food": '<path d="M29 71c0-38 42-52 62-30 23 25-5 53-43 48Z" fill="__INK__" fill-opacity=".18"/><path d="m45 51 12 13m3-22 13 14M47 89l-9 11m-9-8c-10 0-10 15 0 15 0 10 15 10 15 0"/><path d="M64 78c10-3 18-9 23-17" stroke="__ACCENT__"/>',
    "fish": '<path d="M37 65c18-35 46-34 69 0-23 33-51 33-69 0L22 47v36Z" fill="__INK__" fill-opacity=".18"/><path d="m59 41 12-15 10 12M59 88l13 15 10-13M80 43c-7 13-7 31 0 44"/><circle cx="91" cy="61" r="3" fill="__ACCENT__"/><path d="m49 58 8 8-8 8" stroke="__ACCENT__"/>',
    "gem": '<path d="m44 30 40 0 21 25-41 51-41-51Z" fill="__INK__" fill-opacity=".18"/><path d="M23 55h82M44 30l-5 25 25 51 25-51-5-25M44 30l20 25 20-25"/><path d="m43 49 14-13m11 25 12 0-16 32" stroke="__ACCENT__"/>',
    "key": '<circle cx="47" cy="45" r="20"/><circle cx="47" cy="45" r="8"/><path d="m61 60 36 36m-9-8-10 10m1-19-10 10" stroke-width="6"/><path d="m37 34 3-2" stroke="__ACCENT__"/>',
    "book": '<path d="M64 39c-13-12-28-13-40-7v61c15-5 26-2 40 8 14-10 25-13 40-8V32c-12-6-27-5-40 7Z" fill="__INK__" fill-opacity=".16"/><path d="M64 39v62M35 45l18 6m-18 8 18 6m-18 8 18 6m21-28 18-6m-18 20 18-6m-18 20 18-6" stroke="__ACCENT__"/>',
    "token": '<path d="m64 25 31 18v38l-31 20-31-20V43Z" fill="__INK__" fill-opacity=".18"/><circle cx="64" cy="63" r="23"/><path d="m64 45 6 12 14 2-10 10 2 14-12-7-12 7 2-14-10-10 14-2Z" stroke="__ACCENT__" stroke-width="2.2"/>',
    "chest": '<path d="M28 59c0-43 72-43 72 0v41H28Z" fill="__INK__" fill-opacity=".17"/><path d="M28 60h72M42 37v62m44-62v62M59 55h12v23H59Z"/><path d="M64 64v6m-28 19h56" stroke="__ACCENT__"/>',
    "beard": '<path d="M35 34c4-17 19-22 29-13 10-9 25-4 29 13v33c-4 19-15 32-29 42-14-10-25-23-29-42Z" fill="__INK__" fill-opacity=".14"/><path d="m39 40 14 17 11-8 11 8 14-17M48 67l16-7 16 7m-25 8 9 9 9-9m-9 9v15" stroke="__ACCENT__"/>',
    "bone": '<path d="M38 32c-14-7-24 7-15 17-7 13 8 23 18 13l43 35c2 14 21 17 24 2 16-2 14-21-1-23L65 41c6-13-9-25-19-14Z" fill="__INK__" fill-opacity=".2"/><path d="m47 48 35 30m-44-34 5 5m46 36 6 4" stroke="__ACCENT__"/>',
    "cat": '<path d="m33 27 22 16h18l22-16-3 43c-2 19-15 29-28 33-13-4-26-14-28-33Z" fill="__INK__" fill-opacity=".16"/><path d="m37 35 8 23 10-9m36-14-8 23-10-9m-26 19 11 4m23-4-11 4m-12 9 7 6 7-6m-7 6v10"/><path d="m31 80 21 4m-20 5 20-1m45-8-21 4m20 5-20-1" stroke="__ACCENT__"/>',
    "structure": '<path d="M31 103V52l33-24 33 24v51ZM24 52h80M43 42v-9h11m20 0h11v9M54 103V77h20v26" fill="__INK__" fill-opacity=".16"/><path d="M43 59h10v10H43Zm32 0h10v10H75ZM23 109h82M64 30v15" stroke="__ACCENT__"/>',
}

_MOTIFS = {
    "frost": '<path d="M32 7v50M10 20l44 25M10 45l44-25M22 12l10 8 10-8M14 31l6 6-1 11m26-1-1-11 6-6"/>',
    "fire": '<path d="M35 5c3 18 21 24 17 41-3 17-35 19-40 1-4-13 9-21 11-32 0 15 9 15 12-10Z"/><path d="M32 33c10 11 13 22 0 24-11-3-9-13 0-24Z" fill="__ACCENT__"/>',
    "thunder": '<path d="M35 5 12 34h17l-4 25 27-34H35Z" fill="__ACCENT__"/>',
    "raven": '<path d="M29 29 8 13c1 17 7 27 20 31l4 12 4-12c13-4 19-14 20-31L35 29V19l9-4-12-5-5 5Z" fill="__ACCENT__"/>',
    "dragon": '<path d="M14 49c-4-13 4-20 15-25l-5-13 14 4 12-5-2 12 10 9-14 8-8-2c2 11-8 20-22 12Z"/><path d="m18 45 13-10m9-14 4 3" stroke="__ACCENT__"/>',
    "forest": '<path d="M32 8 13 32h10L9 48h18v10h10V48h18L41 32h10Z" fill="__ACCENT__"/>',
}

_PALETTES = {
    "neutral": ("#ddbf87", "#80d6c7"), "frost": ("#b5e4f4", "#78c5ee"),
    "fire": ("#e8bc80", "#e88e62"), "thunder": ("#e5cb82", "#bcb8f0"),
    "raven": ("#c5c0e4", "#88d8d2"), "dragon": ("#ddbe84", "#acbf88"),
    "forest": ("#c5d7a0", "#8ec5a0"),
}


def _text(item):
    name = getattr(item, "name", str(item))
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        c for c in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(c))))


def classify(item) -> str:
    text = _text(item)
    words = set(text.split())
    categories = " ".join(getattr(item, "categories", ())).casefold()
    has = lambda *values: bool(words.intersection(values))
    if has("tattoo", "tattoos", "scheme") or "tattoos" in categories:
        return "tattoo"
    if re.search(r"\((?:back|front|head|chest|left arm|right arm)\)", getattr(item, "name", ""), re.IGNORECASE):
        return "tattoo"
    if has("rune", "runes", "runestone") or "runes" in categories:
        return "rune"
    if has("key", "keys"):
        return "key"
    if has("sail", "sails"):
        return "sail"
    if has("ship", "ships", "figurehead", "figureheads", "hull", "tailpiece", "tailpieces") or "ships" in categories:
        return "ship"
    if has("hair", "beard", "beards", "hairstyle", "hairstyles") or "beard" in categories:
        return "beard"
    # Weapon and armour nouns outrank their decorative themes or materials.
    for kind, names in (
        ("scythe", ("scythe", "scythes", "sickle", "sickles")), ("hammer", ("hammer", "hammers", "mjolnir", "mjoelnir")),
        ("flail", ("flail", "flails", "ironstar")), ("dagger", ("dagger", "daggers", "seax", "knife", "knives")),
        ("spear", ("spear", "spears", "pike", "lance", "gungnir", "gae", "halberd", "atgeir")),
        ("axe", ("axe", "axes", "hatchet", "bardiche")),
        ("sword", ("sword", "swords", "blade", "blades", "greatsword", "excalibur", "saber", "sabre", "katana", "khopesh", "kopesh", "claymore", "rapier", "scimitar")),
        ("bow", ("bow", "bows", "longbow", "shortbow", "crossbow")),
        ("arrow", ("arrow", "arrows")), ("shield", ("shield", "shields", "buckler", "aegis")),
        ("helmet", ("helmet", "helm", "headpiece", "mask", "eyepatch", "crown")),
        ("hood", ("hood", "cloak", "cape", "cowl")),
        ("bracers", ("bracer", "bracers", "gauntlet", "gauntlets", "wristguards", "gloves", "vambraces")),
        ("trousers", ("pants", "trousers", "breeches", "leggings", "plates", "greaves", "grieves")),
        ("boots", ("boot", "boots", "shoes", "footwear")),
        ("torso", ("armor", "armour", "torso", "chestpiece", "tunic", "outfit", "robe", "vest", "breastplate", "cuirass")),
    ):
        if has(*names):
            return kind
    if "armor" in categories or "armour" in categories:
        return "torso"
    if "weapons" in categories:
        return "weapon"
    if has("ingot", "ingots"):
        return "ingot"
    if has("leather", "hide", "pelt", "fur", "furs", "skins"):
        return "leather"
    if has("bone", "bones", "heart", "guts", "fang", "fangs", "claw", "claws", "blood", "feather", "tooth", "teeth", "tusk", "hoof", "antler", "antlers"):
        return "bone"
    if has("ore", "titanium", "silica", "platinum", "iron", "carbon", "nickel", "tungsten"):
        return "ore"
    if has("fabric", "cloth", "clothing", "linen", "silk"):
        return "cloth"
    if has("silver", "deniers", "coins", "coin", "money", "gold"):
        return "currency"
    if has("gem", "gems", "gemstone", "diamond", "crystal", "opal", "opals", "sunstone", "moonstones"):
        return "gem"
    if has("token", "tokens", "energy", "infamy", "renown", "medal", "medals"):
        return "token"
    if has("fish", "salmon", "bream", "bullhead", "burbot", "cod", "eel", "flatfish", "halibut", "hake", "mackerel", "perch", "pollock", "shad", "sturgeon", "trout", "bass", "char", "haddock", "redfish"):
        return "fish"
    if has("rations", "ration", "food", "bread", "meat", "delicacies", "mushroom", "berries"):
        return "food"
    if has("book", "books", "texts", "scroll", "scrolls", "knowledge", "manuscript", "page", "pages", "letter", "note", "map"):
        return "book"
    if has("wolf", "hound", "dog", "fox"):
        return "wolf"
    if has("bear", "panda"):
        return "bear"
    if has("tiger", "lion", "cat", "lynx", "leopard", "puma", "cougar"):
        return "cat"
    if has("horse", "mount", "rideable", "stallion", "steed", "unicorn", "pegasus"):
        return "horse"
    if has("raven", "eagle", "bird", "falcon", "crow", "owl", "heron", "pigeon"):
        return "bird"
    if has("tower", "chapel", "brazier", "altar", "offering", "sarcophagus", "cross", "totem", "bonfire", "banner", "fountain", "well", "treehouse", "circle", "sacrifice", "lighting", "column", "pillar", "monument", "monolith", "shrine", "windmill"):
        return "structure"
    if has("tree", "decoration", "ornament", "ornamentation", "shrub", "statue", "scarecrow") or "settlement" in categories:
        return "tree"
    if has("weapon", "weapons"):
        return "weapon"
    if "rideables" in categories:
        return "horse"
    return "chest"


def _theme(item):
    words = set(_text(item).split())
    for theme, values in (
        ("frost", {"frost", "frozen", "ice", "icy", "winter", "snow", "jotun", "niflheim", "frostbita"}),
        ("fire", {"fire", "flame", "flames", "ember", "inferno", "magma", "muspelheim", "damnation", "hell"}),
        ("thunder", {"thunder", "lightning", "thor", "thors", "mjolnir"}),
        ("raven", {"raven", "crow", "draugr", "huldofolk", "huldra", "gothic", "eclipse"}),
        ("dragon", {"dragon", "drake", "serpent", "wyrm", "jormungandr"}),
        ("forest", {"forest", "druid", "druids", "celtic", "green", "leaf", "leaves"}),
    ):
        if words.intersection(values):
            return theme
    return "neutral"


def _collection_kind(pack):
    """Use only an explicit, unambiguous family shared by a collection.

    Mixed cosmetic packs and bundles such as 'Axe & Shield' cannot establish
    the family of a nameless member. Generic weapon bundles never establish a
    specific weapon type.
    """
    words = set(_text(pack).split())
    category = getattr(pack, "category", "").casefold().strip()
    if category in {"armor", "armour"}:
        return "torso"
    if words.intersection({"tattoo", "tattoos"}):
        return "tattoo"
    if words.intersection({"rune", "runes"}):
        return "rune"
    candidates = set()
    for kind, terms in (
        ("axe", {"axe", "axes"}), ("sword", {"sword", "swords"}),
        ("dagger", {"dagger", "daggers", "seax"}),
        ("spear", {"spear", "spears"}), ("bow", {"bow", "bows"}),
        ("hammer", {"hammer", "hammers"}), ("scythe", {"scythe", "scythes", "sickle", "sickles"}),
        ("flail", {"flail", "flails"}), ("shield", {"shield", "shields"}),
        ("beard", {"hair", "hairstyle", "hairstyles", "beard", "beards"}),
        ("ship", {"ship", "ships"}), ("tree", {"settlement", "ravensthorpe"}),
        ("horse", {"mount", "mounts", "rideable", "rideables"}),
        ("bird", {"eagles", "birds"}),
        ("food", {"rations"}),
    ):
        if words.intersection(terms):
            candidates.add(kind)
    if "raven" in words and words.intersection({"skin", "skins", "mount", "mounts"}):
        candidates.add("bird")
    weapon_types = {"axe", "sword", "dagger", "spear", "bow", "hammer", "scythe", "flail", "shield"}
    if words.intersection({"weapon", "weapons"}) and not candidates.intersection(weapon_types):
        candidates.add("weapon")
    if words.intersection({"armor", "armour"}):
        candidates.add("torso")
    if len(candidates) == 1:
        return next(iter(candidates))
    if candidates:
        return None
    return {"armor": "torso", "armour": "torso", "weapons": "weapon",
            "tattoos": "tattoo", "runes": "rune", "ships": "ship"}.get(category)


def svg_art(kind, theme="neutral", variant=0):
    if kind not in SHAPES:
        kind = "chest"
    ink, accent = _PALETTES.get(theme, _PALETTES["neutral"])
    geometry = SHAPES[kind].replace("__INK__", ink).replace("__ACCENT__", accent)
    marks = ("M16 31V16h15M97 112h15V97", "M16 24v-8h8m80 88v8h8", "M16 29l13-13m70 96 13-13", "M16 18h14m68 92h14")[variant % 4]
    badge = ""
    if theme in _MOTIFS:
        motif = _MOTIFS[theme].replace("__ACCENT__", accent)
        badge = f'<circle cx="101" cy="27" r="14" fill="#132435" stroke="{accent}" stroke-width="1"/><g transform="translate(89 15) scale(.38)" fill="none" stroke="{accent}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">{motif}</g>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
    <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#24394d"/><stop offset="1" stop-color="#0b1723"/></linearGradient></defs>
    <rect x="1" y="1" width="126" height="126" rx="17" fill="url(#bg)" stroke="#405367" stroke-width="1.3"/>
    <path d="M20 110 108 22M32 117 116 33" stroke="{accent}" stroke-opacity=".05" stroke-width="10"/>
    <path d="{marks}" stroke="{ink}" stroke-opacity=".6" stroke-width="1.2" fill="none"/>
    <g fill="none" stroke="{ink}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">{geometry}</g>{badge}
    </svg>'''


class IconProvider:
    """GUI-thread artwork cache bounded to avoid 2,327 full-sized bitmaps."""
    def __init__(self, catalog=None, cache_limit=256, translator=None):
        if type(cache_limit) is not int or cache_limit < 1:
            raise ValueError("El límite de la caché debe ser positivo")
        self.catalog = catalog
        self.translator = translator if translator is not None else Translator("es")
        self.cache_limit = cache_limit
        self._cache = OrderedDict()
        self._aliases = getattr(catalog, "_search_text", {}) if catalog is not None else {}
        self._pack_kinds = {}
        if catalog is not None:
            for pack in catalog.packs:
                kind = _collection_kind(pack)
                if kind:
                    for hash_id in pack.hashes:
                        self._pack_kinds.setdefault(hash_id, set()).add(kind)

    @property
    def cache_size(self):
        return len(self._cache)

    def clear_cache(self):
        self._cache.clear()

    def describe(self, item):
        kind = classify(item)
        context = item
        hash_id = getattr(item, "hash_id", "")
        if kind == "chest" and hash_id in self._aliases:
            context = SimpleNamespace(name=self._aliases[hash_id], categories=getattr(item, "categories", ()))
            kind = classify(context)
        if kind == "chest":
            candidates = set(self._pack_kinds.get(hash_id, ()))
            if candidates.intersection({"axe", "sword", "dagger", "spear", "bow", "hammer", "scythe", "flail", "shield"}):
                candidates.discard("weapon")
            if len(candidates) == 1:
                kind = next(iter(candidates))
        return IconDescription(kind, self.translator.text(LABELS[kind]),
                               self.translator.text("Ilustración semántica"), theme=_theme(context))

    @staticmethod
    def _size(size):
        if type(size) is not int or not 8 <= size <= 1024:
            raise ValueError("El tamaño de la miniatura debe estar entre 8 y 1024 píxeles")
        return size

    def _remember(self, key, pixmap):
        self._cache[key] = pixmap
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_limit:
            self._cache.popitem(last=False)
        return pixmap

    def pixmap(self, item, size=128):
        self._size(size)
        description = self.describe(item)
        name = getattr(item, "name", str(item))
        variant = hashlib.blake2b(name.encode("utf-8"), digest_size=1).digest()[0] % 4
        key = (description.kind, description.theme, variant, size)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        renderer = QSvgRenderer(QByteArray(svg_art(description.kind, description.theme, variant).encode("utf-8")))
        if not renderer.isValid():
            raise RuntimeError("No se pudo dibujar la ilustración semántica")
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        return self._remember(key, pixmap)

    def icon(self, item, size=64):
        return QIcon(self.pixmap(item, size))

    def describe_pack(self, pack):
        title = "Pack de {count} objeto" if len(pack.hashes) == 1 else "Pack de {count} objetos"
        return IconDescription("chest", self.translator.text(title, count=len(pack.hashes)),
                               self.translator.text("Composición de ilustraciones semánticas"))

    def retranslate(self):
        """Labels are evaluated live; language changes preserve the artwork cache."""
        return None

    def pack_pixmap(self, pack, catalog=None, size=128):
        self._size(size)
        catalog = catalog or self.catalog
        hashes = tuple(pack.hashes)
        key = ("pack", str(pack.id), hashes[:4], len(hashes), size)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if catalog is None or not hashes or size < 24:
            return self.pixmap(pack, size)
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#445c70"))
        painter.setBrush(QColor("#152535"))
        painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size / 8, size / 8)
        gap = max(2, size // 32)
        tile = (size - gap * 3) // 2
        for index, hash_id in enumerate(hashes[:4]):
            item = catalog.by_hash.get(hash_id)
            if item is None:
                continue
            x, y = gap + (index % 2) * (tile + gap), gap + (index // 2) * (tile + gap)
            painter.drawPixmap(x, y, self.pixmap(item, tile))
        badge_width, badge_height = max(24, size * .32), max(18, size * .20)
        rect = QRectF(size - badge_width - gap, size - badge_height - gap, badge_width, badge_height)
        painter.setBrush(QColor("#ddbf87"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, badge_height / 3, badge_height / 3)
        painter.setPen(QColor("#12202e"))
        font = QFont("Segoe UI")
        font.setPixelSize(max(9, round(size * .115)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(len(hashes)))
        painter.end()
        return self._remember(key, result)

    # Natural spelling for consumers rendering a pack thumbnail.
    pixmap_pack = pack_pixmap
