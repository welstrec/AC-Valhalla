"""Conservative quantity policy, independent of graphics and game processes."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from valhalla.catalog import Catalog, Item, Pack
from valhalla.quantities import MAX_STACK_QUANTITY, QuantityPolicy


class QuantityPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.load()
        cls.policy = QuantityPolicy(cls.catalog)

    def test_armour_weapon_and_named_rune_shield_use_duplicate_prevention(self):
        for name in ("Berserker Armor", "Rune Shield", "Mjolnir"):
            item = next(item for item in self.catalog.items if item.name == name)
            with self.subTest(name=name):
                rule = self.policy.rule(item.hash_id)
                self.assertTrue(rule.unique)
                self.assertEqual(rule.maximum, 1)
                self.assertEqual(rule.source, "equipment_policy")
                self.assertFalse(rule.limit_verified)
                self.assertIn("No es un límite nativo", rule.reason)

    def test_large_resource_quantities_are_not_capped_to_999_or_observed_values(self):
        for hash_id in (0x1BFDB9F655, 0x1AF7031F403, 0x1AF7031F3F9):
            with self.subTest(hash_id=hash_id):
                rule = self.policy.rule(hash_id)
                self.assertFalse(rule.unique)
                self.assertEqual(rule.maximum, MAX_STACK_QUANTITY)
                self.assertGreater(rule.maximum, 100_000)
                self.assertFalse(rule.limit_verified)

    def test_dlc_torso_gear_and_vambracers_are_distinct_from_tattoos_and_ship_shields(self):
        for name in ("Carolingian Torso", "Charlemagne Torso", "Wayland Torso", "Jotun Vambracers",
                     "Torso (Ah Puch)"):
            item = next(item for item in self.catalog.items if item.name == name)
            with self.subTest(name=name):
                self.assertTrue(self.policy.is_unique(item.hash_id))
                self.assertEqual(self.policy.limit(item.hash_id), 1)
        for name in ("Hel Skein (Torso)", "Berserker Shields", "Alfheim Scheme (Torso Tattoo)"):
            item = next(item for item in self.catalog.items if item.name == name)
            with self.subTest(name=name):
                self.assertFalse(self.policy.is_unique(item.hash_id))

    def test_armour_runes_and_bulk_runes_are_not_mistaken_for_equipment(self):
        rune = next(item for item in self.catalog.items if "(Armor Rune)" in item.name)
        self.assertFalse(self.policy.is_unique(rune.hash_id))
        pack = self.catalog.by_pack["99322"]
        self.assertEqual(pack.quantity, 8)
        self.assertTrue(all(not self.policy.is_unique(hash_id) for hash_id in pack.hashes))

    def test_unknown_identifiers_keep_range_without_asserting_a_game_limit(self):
        rule = QuantityPolicy().rule("0000000000000001")
        self.assertFalse(rule.unique)
        self.assertFalse(rule.limit_verified)
        self.assertEqual(rule.source, "int32_range")
        self.assertEqual(rule.maximum, MAX_STACK_QUANTITY)
        self.assertIn("No se ha comprobado", rule.reason)
        with self.assertRaises(FrozenInstanceError):
            rule.maximum = 99

    def test_identifiers_are_exact_and_numeric_forms_agree(self):
        self.assertEqual(self.policy.rule("0x000001AF7031F3F9"), self.policy.rule(0x1AF7031F3F9))
        for value in (True, False, 0, -1, 1 << 64, "1", "0x1", "0000000000000000", "XYZ", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.policy.rule(value)

    def test_aliases_identify_equipment_without_using_icon_descriptions(self):
        first, second, third = (f"{value:016X}" for value in (1, 2, 3))
        catalog = SimpleNamespace(items=[Item(first, first, ("HELIX ITEMS",)),
                                        Item(second, "Armor Rune", ("DLC",)),
                                        Item(third, "Helmet Statue", ("SETTLEMENT",))],
                                  packs=[], _search_text={first: "translated brave helmet"})
        policy = QuantityPolicy(catalog)
        self.assertTrue(policy.is_unique(first))
        self.assertFalse(policy.is_unique(second))
        self.assertFalse(policy.is_unique(third))

    def test_all_equipment_and_mixed_batches_have_different_controls(self):
        armour = next(item for item in self.catalog.items if item.name == "Berserker Armor")
        iron = "000001AF7031F3F9"
        self.assertEqual(self.policy.limit_for([armour.hash_id]), 1)
        self.assertEqual(self.policy.limit_for([armour.hash_id, iron]), MAX_STACK_QUANTITY)
        self.assertEqual(self.policy.limit_for([]), MAX_STACK_QUANTITY)
        with self.assertRaises(ValueError):
            self.policy.limit_for(iron)

    def test_explicit_bulk_membership_does_not_invent_an_equipment_limit(self):
        hash_id = "0000000000000001"
        catalog = SimpleNamespace(items=[Item(hash_id, "Armor Upgrade Material", ("DLC",))],
                                  packs=[Pack("1", "Materials", "DLC", (hash_id,), 10)])
        policy = QuantityPolicy(catalog)
        self.assertFalse(policy.is_unique(hash_id))
        self.assertEqual(policy.limit(hash_id), MAX_STACK_QUANTITY)


if __name__ == "__main__":
    unittest.main()
