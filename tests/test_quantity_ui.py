"""Quantity presentation and duplicate prevention with inert sessions only."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from valhalla.catalog import Catalog, Pack
from valhalla.engine import FeatureState, GrantResult, InventoryItem
from valhalla.library import UserLibrary
from valhalla.quantities import MAX_STACK_QUANTITY, QuantityRule
from valhalla import ui


IRON = "000001AF7031F3F9"
ARMOUR = "000001B91B7D42BD"


class ReadOnlyFixture:
    """Records attempted actions without exposing any native-process API."""
    def __init__(self):
        keys = ("grant", "inventory", "persistence", "horse", "drink", "orlog", "stealth", "boost", "map")
        self.features = {key: FeatureState(key, key, available=True) for key in keys}
        self.tainted = False
        self.identity = (123, 456)
        self.memory = SimpleNamespace(tainted=False)
        self.inventory = []
        self.reads = 0
        self.writes = []

    def list_inventory(self):
        self.reads += 1
        return list(self.inventory)

    def grant_items(self, hashes, quantity, cancel=None, progress=None):
        self.writes.append(("grant", tuple(hashes), quantity))
        return GrantResult(len(hashes), completed=len(hashes))

    def set_quantity(self, item, quantity):
        self.writes.append(("quantity", item.hash_id, quantity))
        return item


class QuantityUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.catalog = Catalog.load()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.library = UserLibrary(Path(self.temporary.name) / "library")
        self.patches = [
            patch.object(ui, "list_games", return_value=[]),
            patch.object(ui, "GameSession", side_effect=AssertionError("No real game process permitted")),
            patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        ]
        self.mocks = [entry.start() for entry in self.patches]
        self.window = ui.MainWindow(self.catalog, library=self.library)
        self.window.timer.stop()
        self.window.navigate(1)
        self.window.show()
        QTest.qWait(5)
        self.session = ReadOnlyFixture()

    def tearDown(self):
        self.window.worker.cancel.set()
        self.wait_until(lambda: not self.window.busy)
        self.window.session = None
        self.window.close_pending = False
        self.window.disconnect_pending = False
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(5)
        for entry in reversed(self.patches):
            entry.stop()
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), "La interfaz no alcanzó el estado esperado")

    def connect(self):
        self.window.session = self.session
        self.window._refresh_controls()

    def row(self, hash_id, quantity, address=0x1000):
        return InventoryItem(int(hash_id, 16), self.catalog.by_hash[hash_id].name,
                             quantity, address, self.session.identity, 1)

    def select(self, hash_id):
        self.assertTrue(self.window.gallery.select_hash(hash_id))
        self.assertEqual(self.window.detail_item.hash_id, hash_id)

    def displayed(self, hash_id):
        row = next(index for index, item in enumerate(self.window.display_items) if item.hash_id == hash_id)
        return self.window.item_table.item(row, 3).text()

    def test_no_read_is_unknown_and_explicit_refresh_reads_without_writing(self):
        self.assertTrue(self.window.show_quantities.isChecked())
        self.assertFalse(self.window.item_table.isColumnHidden(3))
        self.assertEqual(self.displayed(IRON), "Sin lectura")
        self.connect()
        self.session.inventory = [self.row(IRON, 100_000), self.row(ARMOUR, 1, 0x2000)]
        self.window.catalog_refresh.click()
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(self.displayed(IRON), "100.000")
        self.assertEqual(self.displayed(ARMOUR), "1")
        self.assertEqual(self.session.reads, 1)
        self.assertEqual(self.session.writes, [])
        self.mocks[1].assert_not_called()

    def test_present_zero_and_missing_record_are_distinct(self):
        self.connect()
        self.window._inventory_loaded([self.row(IRON, 0)])
        self.assertEqual(self.displayed(IRON), "0")
        self.assertEqual(self.displayed(ARMOUR), "Sin registro")
        self.assertEqual(self.session.writes, [])

    def test_owned_equipment_is_shown_without_clamping_and_cannot_be_readded(self):
        self.connect()
        self.window._inventory_loaded([self.row(ARMOUR, 1), self.row(ARMOUR, 1, 0x2000)])
        self.select(ARMOUR)
        self.assertEqual(self.displayed(ARMOUR), "2")
        self.assertEqual(self.window.grant_quantity.maximum(), 1)
        self.assertFalse(self.window.grant_button.isEnabled())
        self.assertFalse(self.window.queue_add_button.isEnabled())
        self.assertEqual(self.session.writes, [])

    def test_resources_allow_large_values_and_equipment_has_an_individual_control(self):
        self.select(IRON)
        self.assertEqual(self.window.grant_quantity.maximum(), MAX_STACK_QUANTITY)
        for quantity in (9_999, 100_000, MAX_STACK_QUANTITY):
            self.window.grant_quantity.setValue(quantity)
            self.assertEqual(self.window.grant_quantity.value(), quantity)
        self.select(ARMOUR)
        self.assertEqual(self.window.grant_quantity.maximum(), 1)
        self.assertEqual(self.window.grant_quantity.value(), 1)
        self.assertEqual(self.session.writes, [])

    def test_inventory_editor_uses_per_item_maximum_and_preserves_large_current_quantity(self):
        self.connect()
        self.window._inventory_loaded([self.row(IRON, 100_000), self.row(ARMOUR, 1, 0x2000)])
        self.window.inventory_table.selectRow(0)
        self.assertEqual(self.window.new_quantity.maximum(), MAX_STACK_QUANTITY)
        self.assertEqual(self.window.new_quantity.value(), 100_000)
        self.window.inventory_table.selectRow(1)
        self.assertEqual(self.window.new_quantity.maximum(), 1)
        self.assertEqual(self.window.new_quantity.value(), 1)
        self.assertEqual(self.session.writes, [])

    def test_verified_fixture_limit_is_used_without_hiding_the_actual_quantity(self):
        # This 99 cap is test metadata, never a claimed native game capacity.
        self.window.quantity_policy._rules[IRON] = QuantityRule(
            False, "Fixture con máximo comprobado", "Límite simulado solo para esta prueba.",
            99, limit_verified=True, source="test_fixture")
        self.window._filter_catalog()
        self.connect()
        self.window._inventory_loaded([self.row(IRON, 100_000)])
        self.select(IRON)
        self.assertEqual(self.displayed(IRON), "100.000")
        self.assertEqual(self.window.grant_quantity.maximum(), 99)
        row = next(index for index, item in enumerate(self.window.display_items) if item.hash_id == IRON)
        self.assertEqual(self.window.item_table.item(row, 4).text(), "Máx. 99")
        self.window.inventory_table.selectRow(0)
        self.assertEqual(self.window.new_quantity.maximum(), 99)
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "100000")
        self.assertEqual(self.session.writes, [])

    def test_current_quantity_99_does_not_become_an_unverified_capacity(self):
        self.connect()
        self.window._inventory_loaded([self.row(IRON, 99)])
        self.select(IRON)
        self.assertEqual(self.displayed(IRON), "99")
        self.assertFalse(self.window.quantity_policy.rule(IRON).limit_verified)
        self.assertEqual(self.window.grant_quantity.maximum(), MAX_STACK_QUANTITY)
        self.window.inventory_table.selectRow(0)
        self.assertEqual(self.window.new_quantity.maximum(), MAX_STACK_QUANTITY)
        self.assertEqual(self.window.new_quantity.value(), 99)
        self.assertEqual(self.session.writes, [])

    def test_hiding_quantities_preserves_data_and_reenabling_reads_without_writing(self):
        self.connect()
        self.session.inventory = [self.row(IRON, 9_999)]
        self.window._inventory_loaded(self.session.inventory)
        self.window.show_quantities.setChecked(False)
        self.assertTrue(self.window.item_table.isColumnHidden(3))
        self.assertEqual(self.session.reads, 0)
        self.window.show_quantities.setChecked(True)
        self.wait_until(lambda: not self.window.busy)
        self.assertFalse(self.window.item_table.isColumnHidden(3))
        self.assertEqual(self.displayed(IRON), "9.999")
        self.assertEqual(self.session.reads, 1)
        self.assertEqual(self.session.writes, [])

    def test_disconnect_clears_snapshot_instead_of_displaying_old_values(self):
        self.connect()
        self.window._inventory_loaded([self.row(IRON, 100_000)])
        self.window.session = None
        self.window._clear_inventory()
        self.assertEqual(self.displayed(IRON), "Sin lectura")
        self.assertEqual(self.window.inventory_totals, {})
        self.assertIsNone(self.window.inventory_identity)

    def test_mixed_pack_queue_keeps_resource_quantity_and_single_equipment(self):
        pack = Pack("test-only", "Mixed inert pack", "Prueba", (ARMOUR, IRON), 1)
        self.window._show_detail(pack=pack)
        self.window.grant_quantity.setValue(100_000)
        self.window.queue_selection()
        self.assertEqual({entry.hash_id: entry.quantity for entry in self.window.request_queue.entries},
                         {ARMOUR: 1, IRON: 100_000})
        self.assertEqual(self.session.reads, 0)
        self.assertEqual(self.session.writes, [])
        self.mocks[1].assert_not_called()


if __name__ == "__main__":
    unittest.main()
