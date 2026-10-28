import hashlib
from copy import deepcopy
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from valhalla.catalog import Catalog, build_catalog, normalize_hash


ROOT = Path(__file__).resolve().parent.parent


def tiny_table(body: str) -> bytes:
    return f'''<CheatTable CheatEngineTableVersion="42"><CheatEntries><CheatEntry>
<ID>1</ID><Description>"Test (2 Pcs)"</Description><AssemblerScript>[ENABLE]
GI_pIIDList:
dq 0000000000000001 // Quantity
{body}
GI_TLock:
db 1
[DISABLE]
</AssemblerScript></CheatEntry></CheatEntries></CheatTable>'''.encode()


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
        cls.catalog = Catalog(cls.document)

    def test_source_identity_and_expected_catalogue(self):
        raw = (ROOT / "data/source.CT").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest().upper(), self.catalog.source["sha256"])
        self.assertEqual(len(self.catalog.items), 2327)
        self.assertEqual(len(self.catalog.packs), 290)
        self.assertEqual(self.catalog.source["statistics"]["source_hash_references"], 4085)

    def test_premature_stop_retains_both_ignored_items(self):
        pack = self.catalog.by_pack["99295"]
        self.assertEqual(len(pack.hashes), 15)
        self.assertIn("000001E9BC55CE53", pack.hashes)
        self.assertIn("000001E82D13E204", pack.hashes)
        evidence = [d for d in self.catalog.source["diagnostics"] if d["code"] == "item_after_terminator"]
        self.assertEqual({d["source_line"] for d in evidence}, {4437, 4438})
        document = build_catalog(tiny_table("dq 0000000000000010 // First\ndq 0000000000000000\ndq 0000000000000020 // Second"))
        self.assertEqual(document["packs"][0]["hashes"], ["0000000000000010", "0000000000000020"])

    def test_prefixes_and_wildcard_nibbles(self):
        self.assertEqual(normalize_hash("0x000001f142c45a17"), "000001F142C45A17")
        for bad in ("48 8B ??", "000001F142C45A1?", "000001F142C45A17F", "1"):
            with self.assertRaises(ValueError):
                normalize_hash(bad)
        frost = self.catalog.by_pack["99715"]
        self.assertEqual(len(frost.hashes), 4)
        self.assertIn("000001F142C46398", frost.hashes)
        document = build_catalog(tiny_table("dq 000000000000001? // Not an ID\ndq 0000000000000000"))
        self.assertFalse(document["items"])
        self.assertTrue(any(d["code"] == "invalid_dq_literal" for d in document["source"]["diagnostics"]))

    def test_aggregates_cover_subpacks_without_dropping_original_ids(self):
        aggregate = next(p for p in self.document["packs"] if p["id"] == "99199")
        self.assertEqual(len(aggregate["source_hashes"]), 618)
        self.assertEqual(len(aggregate["hashes"]), 804)
        for member in aggregate["aggregate_members"]:
            self.assertTrue(set(self.catalog.by_pack[member].hashes) <= set(aggregate["hashes"]))
        self.assertTrue(set(aggregate["source_hashes"]) <= set(aggregate["hashes"]))
        self.assertEqual(len(self.catalog.by_pack["99724"].hashes), 71)  # Scoped rune aggregate.
        self.assertEqual(self.catalog.by_pack["99674"].name, "Blessed Warrior Pack (24 Pcs)")

    def test_unicode_search_and_category_filter(self):
        matches = self.catalog.search("frostbita")
        self.assertTrue(any(item.hash_id == "000001F142C45A17" for item in matches))
        self.assertEqual(self.catalog.search("0x000001f142c45a17"), matches)
        self.assertTrue(self.catalog.search("", "HELIX ITEMS"))
        self.assertFalse(self.catalog.search("frostbita", "WEAPONS"))
        self.assertNotIn("Todos", self.catalog.categories)

    def test_duplicate_source_literals_are_deduplicated_with_evidence(self):
        document = build_catalog(tiny_table("dq 0000000000000010 // First\ndq 0x0000000000000010 // Alias\ndq 0000000000000000"))
        self.assertEqual(document["packs"][0]["hashes"], ["0000000000000010"])
        self.assertEqual(len(document["packs"][0]["source_hashes"]), 2)
        self.assertIn("Alias", document["items"][0]["aliases"])
        self.assertIn("duplicate_hashes", document["packs"][0]["repairs"])

    def test_static_parser_ignores_code_and_rejects_entity_expansion(self):
        document = build_catalog(tiny_table("{$lua}\nerror('must never execute')\n{$asm}\ndq 0000000000000010 // Literal\ndq 0000000000000000"))
        self.assertEqual(len(document["items"]), 1)
        self.assertFalse(document["source"]["build"]["scripts_executed"])
        serialized = json.dumps(document)
        self.assertNotIn("must never execute", serialized)
        with self.assertRaises(ValueError):
            build_catalog(b'<!DOCTYPE x [<!ENTITY e "expanded">]><CheatTable/>')

    def test_rebuild_is_reproducible(self):
        rebuilt = build_catalog((ROOT / "data/source.CT").read_bytes(), self.catalog.source["filename"])
        self.assertEqual(rebuilt, self.document)

    def test_source_hierarchy_distinguishes_repeated_category_names(self):
        self.assertEqual(self.catalog.pack_paths["99199"], ("Tienda y eventos", "HELIX ITEMS"))
        self.assertEqual(self.catalog.pack_paths["99354"], ("Juego base", "WEAPONS"))
        self.assertEqual(self.catalog.pack_paths["99495"], ("Expansiones y extras", "DAWN OF RAGNARÖK"))
        utility_paths = {self.catalog.pack_paths[ident] for ident in ("99178", "99295", "99486")}
        self.assertEqual(len(utility_paths), 3)
        self.assertTrue(all(path[-1] == "UTILITIES" for path in utility_paths))
        self.assertEqual(set(self.catalog.pack_paths), set(self.catalog.by_pack))
        for pack in self.catalog.packs:
            path = self.catalog.pack_paths[pack.id]
            self.assertNotIn(pack.name, path)
            self.assertFalse(any("GetItem" in part or "==Done" in part for part in path))

    def test_legacy_catalogue_paths_fall_back_and_invalid_paths_fail(self):
        document = deepcopy(self.document)
        for row in document["packs"]:
            row.pop("navigation_path", None)
        catalog = Catalog(document)
        self.assertEqual(catalog.pack_paths["99354"], ("WEAPONS",))
        for malformed in ("not a list", [None], [""], [" "]):
            with self.subTest(malformed=malformed):
                document["packs"][0]["navigation_path"] = malformed
                with self.assertRaises(ValueError):
                    Catalog(document)

    def test_nested_collection_folder_and_sibling_heading_keep_their_scope(self):
        pack = ET.fromstring(tiny_table("dq 0000000000000010 // Test\ndq 0000000000000000")).find("CheatEntries/CheatEntry")
        root = ET.fromstring('''<CheatTable><CheatEntries><CheatEntry><ID>10</ID>
<Description>+Bulk GetItem: Base Game Weapons/Armor/More...</Description><CheatEntries>
<CheatEntry><ID>11</ID><Description>------------ WEAPONS ------------</Description></CheatEntry>
<CheatEntry><ID>12</ID><Description>+Axes</Description><CheatEntries/></CheatEntry>
<CheatEntry><ID>13</ID><Description>------------ ARMOR ------------</Description></CheatEntry>
</CheatEntries></CheatEntry></CheatEntries></CheatTable>''')
        group = root.find("CheatEntries/CheatEntry/CheatEntries")
        group.findall("CheatEntry")[1].find("CheatEntries").append(pack)
        second = deepcopy(pack)
        second.find("ID").text = "2"
        group.append(second)
        document = build_catalog(ET.tostring(root))
        catalog = Catalog(document)
        self.assertEqual(catalog.pack_paths["1"], ("Juego base", "WEAPONS", "Axes"))
        self.assertEqual(catalog.pack_paths["2"], ("Juego base", "ARMOR"))
        self.assertEqual(catalog.by_pack["1"].category, "WEAPONS")
        self.assertEqual(catalog.by_pack["2"].category, "ARMOR")


if __name__ == "__main__":
    unittest.main()
