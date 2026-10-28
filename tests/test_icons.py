"""Original semantic art: honest labels, conservative inference and bounded memory."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QByteArray
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from valhalla.catalog import Catalog, Item, Pack
from valhalla.icons import IconProvider, LABELS, SHAPES, _PALETTES, classify, svg_art
from valhalla.i18n import Translator


class SemanticIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.catalog = Catalog.load()

    def item(self, name, categories=(), hash_id="0000000000000001"):
        return Item(hash_id, name, tuple(categories))

    def provider_for(self, name="Mystery", packs=(), aliases=None):
        item = self.item(name)
        catalog = SimpleNamespace(items=[item], by_hash={item.hash_id: item},
                                  packs=list(packs), _search_text=aliases or {})
        return item, IconProvider(catalog)

    def pack(self, name, ident="1", category="HELIX ITEMS"):
        return Pack(ident, name, category, ("0000000000000001",), 1)

    def test_explicit_nouns_outrank_decorative_material_and_theme(self):
        for name, expected in (("Frost Raven Axe", "axe"), ("Bone Sickle", "scythe"),
                               ("Silver Shield", "shield"), ("Iron Bow", "bow"),
                               ("Bear Helmet", "helmet"), ("Dragon Cloak", "hood"),
                               ("Wolf Bracers", "bracers"), ("Runestone Tattoos", "tattoo")):
            with self.subTest(name=name):
                self.assertEqual(classify(self.item(name)), expected)

    def test_plural_and_camel_case_family_names_are_recognized(self):
        for name, kind in (("GreatSwords", "sword"), ("Dane Axes", "axe"),
                           ("Hammers", "hammer"), ("Hairstyles", "beard"),
                           ("Hunter Bows", "bow"), ("Redfish (small)", "fish")):
            with self.subTest(name=name):
                self.assertEqual(classify(self.item(name)), kind)

    def test_familiar_named_symbols_and_unknown_weapon_remain_distinct(self):
        provider = IconProvider()
        for name, kind in (("Excalibur", "sword"), ("Gungnir", "spear"), ("Mjölnir", "hammer")):
            self.assertEqual(provider.describe(self.item(name)).kind, kind)
        generic = provider.describe(self.item("Mystery", ("WEAPONS",)))
        self.assertEqual((generic.kind, generic.label), ("weapon", "Arma"))
        self.assertEqual(classify(self.item("Raven Clan", ("ARMOR",))), "torso")

    def test_description_language_changes_without_changing_semantics_or_artwork(self):
        translator = Translator("en")
        provider = IconProvider(translator=translator)
        item = self.item("Frost Axe")
        english = provider.describe(item)
        picture = provider.pixmap(item)
        self.assertEqual(english.label, "Axe")
        self.assertEqual(english.source, "Semantic illustration")
        pack = self.pack("Example pack")
        self.assertEqual(provider.describe_pack(pack).label, "Pack of 1 item")
        translator.set_language("es")
        provider.retranslate()
        spanish = provider.describe(item)
        self.assertEqual(spanish.label, "Hacha")
        self.assertEqual(spanish.source, "Ilustración semántica")
        self.assertEqual((spanish.kind, spanish.theme, spanish.exact),
                         (english.kind, english.theme, english.exact))
        self.assertEqual(provider.pixmap(item).cacheKey(), picture.cacheKey())

    def test_catalogue_aliases_can_restore_missing_names(self):
        item, provider = self.provider_for("0000000000000001", aliases={"0000000000000001": "frost spear"})
        result = provider.describe(item)
        self.assertEqual((result.kind, result.theme), ("spear", "frost"))

    def test_weapon_pack_does_not_fabricate_a_specific_weapon_type(self):
        item, provider = self.provider_for(packs=[self.pack("Mythical Weapon Pack A")])
        self.assertEqual(provider.describe(item).kind, "weapon")

    def test_specific_collection_narrows_generic_weapon_membership(self):
        item, provider = self.provider_for(packs=[self.pack("Weapons", "1"), self.pack("Hammers", "2")])
        self.assertEqual(provider.describe(item).kind, "hammer")

    def test_conflicting_and_mixed_packs_leave_unknown_item_generic(self):
        for packs in ([self.pack("Axes", "1"), self.pack("Bows", "2")],
                      [self.pack("Iridescent Axe & Shimmering Shield")],
                      [self.pack("Mounts & Raven Skin")],
                      [self.pack("Blood Eagle Pack")], [self.pack("Black Raven Pack")]):
            with self.subTest(names=[pack.name for pack in packs]):
                item, provider = self.provider_for(packs=packs)
                self.assertEqual(provider.describe(item).kind, "chest")

    def test_semantic_source_never_claims_exact_game_art(self):
        provider = IconProvider(self.catalog)
        for item in self.catalog.items:
            description = provider.describe(item)
            self.assertFalse(description.exact)
            self.assertEqual(description.source, "Ilustración semántica")
            self.assertIn(description.kind, SHAPES)
            self.assertIn(description.theme, _PALETTES)

    def test_all_original_svg_families_and_themes_render_without_external_assets(self):
        self.assertEqual(set(LABELS), set(SHAPES))
        self.assertGreaterEqual(len(SHAPES), 40)
        for kind in SHAPES:
            for theme in _PALETTES:
                with self.subTest(kind=kind, theme=theme):
                    data = svg_art(kind, theme).encode("utf-8")
                    self.assertTrue(QSvgRenderer(QByteArray(data)).isValid())
                    self.assertNotIn(b"__INK__", data)
                    self.assertNotIn(b"__ACCENT__", data)
                    self.assertNotIn(b"href=", data)

    def test_full_catalogue_and_every_pack_render_with_bounded_cache(self):
        provider = IconProvider(self.catalog, cache_limit=32)
        kinds = Counter()
        for item in self.catalog.items:
            kinds[provider.describe(item).kind] += 1
            self.assertFalse(provider.pixmap(item, 48).isNull())
            self.assertLessEqual(provider.cache_size, 32)
        for pack in self.catalog.packs:
            self.assertFalse(provider.pack_pixmap(pack, size=48).isNull())
            self.assertLessEqual(provider.cache_size, 32)
        self.assertEqual(sum(kinds.values()), 2327)
        self.assertLess(kinds["chest"], 350)

    def test_cache_reuses_pixmaps_and_evicts_least_recently_used(self):
        provider = IconProvider(cache_limit=2)
        item = self.item("Frost Axe")
        first = provider.pixmap(item, 32)
        self.assertEqual(first.cacheKey(), provider.pixmap(item, 32).cacheKey())
        provider.pixmap(self.item("Fire Bow"), 32)
        provider.pixmap(item, 32)
        provider.pixmap(self.item("Silver Shield"), 32)
        self.assertEqual(first.cacheKey(), provider.pixmap(item, 32).cacheKey())
        self.assertEqual(provider.cache_size, 2)
        provider.clear_cache()
        self.assertEqual(provider.cache_size, 0)

    def test_small_and_large_pack_previews_and_missing_catalogue_are_supported(self):
        provider = IconProvider(self.catalog)
        pack = self.catalog.packs[0]
        for size in (8, 16, 24, 128, 512):
            preview = provider.pack_pixmap(pack, size=size)
            self.assertEqual((preview.width(), preview.height()), (size, size))
        self.assertFalse(IconProvider().pack_pixmap(pack).isNull())
        self.assertFalse(provider.icon(self.catalog.items[0]).isNull())

    def test_invalid_dimensions_and_cache_limits_are_rejected(self):
        item = self.catalog.items[0]
        for size in (True, 0, 7, 1025, 12.5, "128"):
            with self.subTest(size=size), self.assertRaises(ValueError):
                IconProvider().pixmap(item, size)
        for limit in (False, 0, -1, 2.0):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                IconProvider(cache_limit=limit)

    def test_exported_original_sources_match_runtime_geometry(self):
        folder = Path(__file__).resolve().parent.parent / "assets" / "semantic_icons"
        for kind in SHAPES:
            self.assertEqual((folder / f"{kind}.svg").read_text(encoding="utf-8"), svg_art(kind))


if __name__ == "__main__":
    unittest.main()
