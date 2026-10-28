"""Static inventory catalogue. This module never runs Cheat Engine scripts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET


SCHEMA_VERSION = 1
DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "data" / "catalog.json"
_HASH = re.compile(r"(?:0[xX])?([0-9a-fA-F]{16})\Z")
_DQ = re.compile(r"^\s*dq\s+([^/]*?)(?:\s*//\s*(.*))?$", re.IGNORECASE)
_LABEL = re.compile(r"\((\d+)\s*Pcs\)", re.IGNORECASE)
_ALL = re.compile(r"^ALL-IN-ONE\b", re.IGNORECASE)


def normalize_hash(value: str) -> str:
    """Require a whole 64-bit textual ID; wildcard signatures are not IDs."""
    match = _HASH.fullmatch(value.strip())
    if not match:
        raise ValueError(f"HashID inválido: {value!r}; se requieren 16 dígitos hexadecimales")
    return match.group(1).upper()


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def _description(value: str) -> str:
    return value.strip().strip('"').strip().lstrip("+").strip()


def _unique(values):
    return list(dict.fromkeys(values))


@dataclass(frozen=True)
class Item:
    hash_id: str
    name: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    category: str
    hashes: tuple[str, ...]
    quantity: int


class Catalog:
    def __init__(self, document: dict):
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Versión de catálogo no compatible")
        self.source = document["source"]
        self.items = [Item(normalize_hash(row["hash_id"]), row["name"],
                           tuple(row["categories"])) for row in document["items"]]
        self.packs = [Pack(str(row["id"]), row["name"], row["category"],
                           tuple(normalize_hash(h) for h in row["hashes"]),
                           int(row["quantity"])) for row in document["packs"]]
        self.by_hash = {item.hash_id: item for item in self.items}
        self.by_pack = {pack.id: pack for pack in self.packs}
        if len(self.by_hash) != len(self.items) or len(self.by_pack) != len(self.packs):
            raise ValueError("El catálogo contiene identificadores duplicados")
        self.pack_paths: dict[str, tuple[str, ...]] = {}
        for row, pack in zip(document["packs"], self.packs):
            path = row.get("navigation_path")
            if path is None or path == []:
                path = [pack.category]
            if (not isinstance(path, (list, tuple)) or not 1 <= len(path) <= 16 or
                    any(not isinstance(part, str) or not part.strip() or len(part) > 500 for part in path)):
                raise ValueError(f"Ruta de navegación inválida en pack {pack.id}")
            self.pack_paths[pack.id] = tuple(part.strip() for part in path)
        for pack in self.packs:
            if pack.quantity <= 0 or pack.quantity > 0xFFFFFFFF:
                raise ValueError(f"Cantidad inválida en pack {pack.id}")
            if len(set(pack.hashes)) != len(pack.hashes):
                raise ValueError(f"HashID duplicado en pack {pack.id}")
            if any(h not in self.by_hash for h in pack.hashes):
                raise ValueError(f"HashID desconocido en pack {pack.id}")
        self.categories = sorted({c for item in self.items for c in item.categories}, key=_fold)
        self._search_text = {
            row["hash_id"]: _fold(" ".join([row["hash_id"], row["name"],
                                            *row.get("aliases", []), *row["categories"]]))
            for row in document["items"]
        }

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Catalog":
        return cls(json.loads(Path(path or DEFAULT_CATALOG).read_text(encoding="utf-8")))

    def search(self, query: str, category: str = "Todos") -> list[Item]:
        query = _fold(query.strip())
        if re.fullmatch(r"0x[0-9a-f]+", query):
            query = query[2:]
        terms = query.split()
        chosen = _fold(category)
        return [item for item in self.items
                if (chosen == "todos" or any(_fold(c) == chosen for c in item.categories))
                and all(term in self._search_text[item.hash_id] for term in terms)]


def build_catalog(source_bytes: bytes, source_name: str = "source.CT") -> dict:
    """Read XML and literal DQ records only; preserve all source IDs and provenance.

    FULL ALL-IN-ONE entries are expanded from their sibling category packs, retaining
    any exclusive original IDs. Scoped aggregates such as Runes-All-In-One are not
    widened to unrelated weapons or armour.
    """
    text = source_bytes.decode("utf-8-sig")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("No se admiten entidades ni DTD en una tabla CT")
    root = ET.fromstring(text)
    if root.tag != "CheatTable":
        raise ValueError("El archivo no es una CheatTable")
    entry_positions = {}
    for match in re.finditer(r"<CheatEntry>\s*<ID>([^<]+)</ID>", text):
        ident = match.group(1)
        if ident in entry_positions:
            raise ValueError(f"ID de entrada duplicado: {ident}")
        entry_positions[ident] = match.start()
    diagnostics = []
    packs = []
    items = {}

    def diagnostic(code, pack_id, line, **details):
        diagnostics.append({"code": code, "pack_id": pack_id, "source_line": line, **details})

    def bulk_label(name):
        if not name.casefold().startswith("bulk getitem:"):
            return None
        topic = name.partition(":")[2].strip()
        if topic.casefold().startswith("helix store/"):
            return "Tienda y eventos"
        if topic.casefold().startswith("base game "):
            return "Juego base"
        if topic.casefold().startswith("dlc/"):
            return "Expansiones y extras"
        return topic or "Packs"

    def contains_pack(container):
        # Structural metadata only. No script, comment or instruction is run.
        return any("GI_pIIDList" in entry.findtext("AssemblerScript", "") and
                   "GI_TLock" in entry.findtext("AssemblerScript", "")
                   for entry in container.findall(".//CheatEntry"))

    def visit(container, inherited_category="Sin categoría", inherited_section="root", inherited_path=()):
        category, section = inherited_category, inherited_section
        navigation = inherited_path
        for entry in container.findall("CheatEntry"):
            ident = entry.findtext("ID", "")
            name = _description(entry.findtext("Description", ""))
            if re.match(r"^-{4,}", name):
                category = name.strip("- ").strip()
                section = ident
                navigation = (*inherited_path, category) if category else inherited_path
            script = entry.findtext("AssemblerScript", "")
            # An ENABLE block is parsed as inert text, never interpreted.
            enabled = re.split(r"\[DISABLE\]", script, maxsplit=1, flags=re.IGNORECASE)[0]
            match = re.search(r"(?im)^[ \t]*GI_pIIDList[ \t]*:[ \t]*[^\r\n]*\r?\n", enabled)
            if match:
                tail = enabled[match.end():]
                marker = re.search(r"(?im)^[ \t]*GI_TLock[ \t]*:", tail)
                if marker:  # Excludes the core allocator's placeholder.
                    body = tail[:marker.start()]
                    position = entry_positions.get(ident, 0)
                    script_tag = re.search(r"<AssemblerScript\b[^>]*>", text[position:])
                    script_line = text.count("\n", 0, position + script_tag.end()) + 1 if script_tag else 1
                    body_line = script_line + enabled[:match.end()].count("\n")
                    slots = []
                    for offset, line in enumerate(body.splitlines()):
                        row = _DQ.fullmatch(line)
                        if not row:
                            continue
                        try:
                            hash_id = normalize_hash(row.group(1))
                        except ValueError:
                            diagnostic("invalid_dq_literal", ident, body_line + offset,
                                       literal=row.group(1).strip())
                            continue
                        slots.append((hash_id, (row.group(2) or "").strip(), body_line + offset))
                    if len(slots) >= 2:
                        quantity = int(slots[0][0], 16)
                        if not 0 < quantity <= 0xFFFFFFFF:
                            raise ValueError(f"Cantidad de pack inválida: {ident}")
                        hashes, reachable, seen_stop = [], [], False
                        for hash_id, item_name, line in slots[1:]:
                            if int(hash_id, 16) == 0:
                                seen_stop = True
                                continue
                            hashes.append(hash_id)
                            if not seen_stop:
                                reachable.append(hash_id)
                            else:
                                diagnostic("item_after_terminator", ident, line, hash_id=hash_id)
                            item = items.setdefault(hash_id, {"hash_id": hash_id,
                                "name": item_name or hash_id, "aliases": [], "categories": [], "sources": []})
                            if item_name and item_name != item["name"] and item_name not in item["aliases"]:
                                item["aliases"].append(item_name)
                            if category not in item["categories"]:
                                item["categories"].append(category)
                            item["sources"].append({"pack_id": ident, "line": line})
                        if not seen_stop:
                            diagnostic("missing_terminator", ident, body_line)
                        unique_hashes = _unique(hashes)
                        if len(hashes) != len(unique_hashes):
                            diagnostic("duplicate_hashes", ident, body_line,
                                       removed=len(hashes) - len(unique_hashes))
                        declared = _LABEL.search(name)
                        if declared and int(declared.group(1)) != len(unique_hashes):
                            diagnostic("declared_count_mismatch", ident, script_line,
                                       declared=int(declared.group(1)), actual=len(unique_hashes))
                        packs.append({"id": ident, "name": name, "category": category,
                            "hashes": unique_hashes, "quantity": quantity, "source_name": name,
                            "navigation_path": list(navigation or (category,)),
                            "source_hashes": hashes, "source_reachable_hashes": reachable,
                            "source_line": script_line, "source_section_id": section,
                            "repairs": []})
            children = entry.find("CheatEntries")
            if children is not None:
                child_path = navigation
                bulk = bulk_label(name)
                if bulk:
                    child_path = (bulk,)
                elif navigation and not match and not re.match(r"^-{4,}", name) and contains_pack(children):
                    # Preserve real collection folders, but not executable
                    # controls, status notes, pack names or their Done rows.
                    child_path = (*navigation, name) if name else navigation
                visit(children, category, section, child_path)

    top = root.find("CheatEntries")
    if top is None:
        raise ValueError("La tabla no contiene entradas")
    visit(top)
    sections = defaultdict(list)
    for pack in packs:
        sections[pack["source_section_id"]].append(pack)
    for section in sections.values():
        full = [pack for pack in section if _ALL.match(pack["name"])]
        if len(full) != 1:
            continue
        aggregate = full[0]
        parts = [pack for pack in section if pack is not aggregate]
        if not parts:
            continue
        if any(pack["quantity"] != aggregate["quantity"] for pack in parts):
            diagnostic("aggregate_quantity_conflict", aggregate["id"], aggregate["source_line"])
            continue
        union = _unique(h for pack in parts for h in pack["source_hashes"])
        additions = [h for h in union if h not in aggregate["hashes"]]
        retained = [h for h in aggregate["hashes"] if h not in union]
        aggregate["aggregate_members"] = [pack["id"] for pack in parts]
        aggregate["aggregate_retained_source_only"] = retained
        if additions:
            aggregate["hashes"] = _unique([*aggregate["hashes"], *union])
            diagnostic("aggregate_expanded", aggregate["id"], aggregate["source_line"],
                       added_hashes=additions, retained_source_only=retained)
    for pack in packs:
        pack["repairs"] = _unique(d["code"] for d in diagnostics if d["pack_id"] == pack["id"])
        pack["name"] = re.sub(r"\((?:\d+\s*|Lots Of )Pcs\)",
                               f"({len(pack['hashes'])} Pcs)", pack["name"], flags=re.IGNORECASE)
        for hash_id in pack["hashes"]:
            if pack["category"] not in items[hash_id]["categories"]:
                items[hash_id]["categories"].append(pack["category"])
    comments = root.findtext("Comments", "")
    versions = {label: re.search(rf"(?m)^{re.escape(label)}\s*:\s*(.+)$", comments)
                for label in ("Game Title", "Game Version", "Table Version", "Cheat Engine", "Date")}
    versions = {label: match.group(1).strip() for label, match in versions.items() if match}
    filename_version = re.search(r"(\d+\.\d+\.\d+)\.CT$", source_name, re.IGNORECASE)
    if filename_version:
        versions["filename_version"] = filename_version.group(1)
        if versions.get("Game Version") and versions["Game Version"] != versions["filename_version"]:
            diagnostic("version_claim_conflict", None, None,
                       filename_version=versions["filename_version"],
                       comment_game_version=versions["Game Version"])
    author = re.search(r"(?m)^Original Author\s*:\s*(.+)$", comments)
    contributors = re.search(r"Current/Known Table Contributors\s*:\s*\n(.*?)\n={4,}", comments, re.DOTALL)
    source = {
        "filename": source_name, "sha256": hashlib.sha256(source_bytes).hexdigest().upper(),
        "size_bytes": len(source_bytes), "xml_table_version": root.get("CheatEngineTableVersion"),
        "declared_versions": versions,
        "credits": {"original_author": author.group(1).strip() if author else None,
                    "contributors": contributors.group(1).splitlines() if contributors else []},
        "build": {"parser_version": 2, "static_only": True, "scripts_executed": False,
                  "game_compatibility_verified": False,
                  "aggregate_policy": "union_of_sibling_packs_preserving_source_only_hashes",
                  "source_preserved": "source.CT"},
        "statistics": {"entries": len(root.findall(".//CheatEntry")),
            "scripts": len(root.findall(".//AssemblerScript")), "packs": len(packs),
            "source_hash_references": sum(len(p["source_hashes"]) for p in packs),
            "source_reachable_references": sum(len(p["source_reachable_hashes"]) for p in packs),
            "unique_items": len(items), "generated_pack_references": sum(len(p["hashes"]) for p in packs)},
        "diagnostics": diagnostics,
    }
    return {"schema_version": SCHEMA_VERSION, "source": source,
            "items": sorted(items.values(), key=lambda item: (_fold(item["name"]), item["hash_id"])),
            "packs": packs}
