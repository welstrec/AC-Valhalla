"""Qt integration tests with inert game doubles; no process is opened."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from dataclasses import replace
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from valhalla.catalog import Catalog
from valhalla.engine import FeatureState, GrantResult, InventoryItem, StaleInventoryError
from valhalla import ui


KEYS = ("grant", "inventory", "persistence", "horse", "drink", "orlog", "stealth", "boost", "map")


class FakeSession:
    """Implements only the UI contract, with inspectable effects."""
    def __init__(self, pid, names):
        self.memory = SimpleNamespace(name="ACValhalla.exe", pid=pid,
            path="C:/test-only/ACValhalla.exe", base=0x140000000, size=4096,
            tainted=False, is_alive=lambda: True, identity=(pid, 1))
        self.features = {key: FeatureState(key, key, available=True, reason="") for key in KEYS}
        self.calls = []
        self.inventory = [InventoryItem(0x1AF7031F3F9, "Iron Ore", 5, 0x1000, (pid,), 1)]
        self.write_error = None
        self.close_error = None
        self.names = names

    @property
    def tainted(self):
        return self.memory.tainted

    @property
    def identity(self):
        return self.memory.identity

    def diagnose(self, cancel=None, progress=None):
        self.calls.append(("diagnose",))
        if progress:
            progress(1, 1, "Comprobado")
        return self.features

    def close(self):
        self.calls.append(("close",))
        if self.close_error:
            raise self.close_error

    def toggle(self, key, enabled):
        self.calls.append(("toggle", key, enabled))
        self.features[key].enabled = enabled

    def list_inventory(self):
        self.calls.append(("list_inventory",))
        return list(self.inventory)

    def set_quantity(self, item, quantity):
        self.calls.append(("set_quantity", item, quantity))
        if self.write_error:
            raise self.write_error
        updated = replace(item, quantity=quantity)
        self.inventory = [updated if row.address == item.address and row.hash_id == item.hash_id else row
                          for row in self.inventory]
        return updated

    def grant_items(self, hashes, quantity, cancel=None, progress=None):
        self.calls.append(("grant_items", tuple(hashes), quantity))
        return GrantResult(requested=len(hashes), completed=len(hashes), confirmed=len(hashes))

    def boost(self, value):
        self.calls.append(("boost", value))
        return "Completado"

    def unlock_map(self):
        self.calls.append(("unlock_map",))
        return 12


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.catalog = Catalog.load()

    def setUp(self):
        self.sessions = []
        self.release_events = []
        self.patches = [
            patch.object(ui, "list_games", return_value=[SimpleNamespace(name="ACValhalla.exe", pid=1234)]),
            patch.object(ui, "GameSession", side_effect=self._new_session),
            patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.window = ui.MainWindow(self.catalog)
        self.window.timer.stop()
        self.window.show()
        QTest.qWait(10)

    def tearDown(self):
        self.window.worker.cancel.set()
        for event in self.release_events:
            event.set()
        self.wait_until(lambda: not self.window.busy)
        self.window.close_pending = False
        self.window.disconnect_pending = False
        self.window.session = None
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(10)
        for p in reversed(self.patches):
            p.stop()

    def _new_session(self, **kwargs):
        session = FakeSession(**kwargs)
        self.sessions.append(session)
        return session

    def wait_until(self, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), "La interfaz no alcanzó el estado esperado")

    def attach(self):
        QTest.mouseClick(self.window.attach_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: self.window.session is not None and not self.window.busy)
        return self.window.session

    def load_inventory(self):
        self.window.navigate(2)
        QTest.mouseClick(self.window.inventory_refresh, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(self.window.inventory_table.rowCount(), 1)

    def test_constructs_full_catalogue_without_connecting(self):
        self.assertEqual(self.window.item_table.rowCount(), 2327)
        self.assertEqual(self.window.pack_table.rowCount(), 290)
        self.assertEqual(self.window.processes.currentData(), 1234)
        self.assertEqual(self.sessions, [])
        self.assertTrue(self.window.attach_button.isEnabled())
        self.assertFalse(self.window.grant_button.isEnabled())
        self.assertFalse(self.window.inventory_refresh.isEnabled())
        self.assertTrue(all(not widget.isEnabled() for widget in self.window.helper_buttons.values()))

    def test_unicode_search_is_applied_through_the_debounce(self):
        self.window.focus_search()
        QTest.keyClicks(self.window.search, "frostbita")
        self.wait_until(lambda: self.window.item_table.rowCount() == 1)
        self.assertEqual(self.window.display_items[0].hash_id, "000001F142C45A17")
        self.assertEqual(self.window.pack_table.rowCount(), 0)
        self.window.search.clear()
        self.wait_until(lambda: self.window.item_table.rowCount() == 2327)

    def test_attach_diagnoses_without_enabling_any_helper(self):
        session = self.attach()
        self.assertEqual(session.calls, [("diagnose",), ("list_inventory",)])
        self.assertTrue(all(not state.enabled for state in session.features.values()))
        self.assertEqual(len(session.names), 2327)
        self.assertFalse(self.window.attach_button.isEnabled())
        self.assertTrue(self.window.detach_button.isEnabled())
        self.assertEqual(self.window.feature_table.rowCount(), len(KEYS))

    def test_feature_gating_and_tainted_session_block_actions(self):
        session = self.attach()
        self.window.navigate(1)
        self.window.item_table.selectRow(0)
        self.assertTrue(self.window.grant_button.isEnabled())
        session.features["grant"].available = False
        session.features["horse"].available = False
        self.window._refresh_controls()
        self.assertFalse(self.window.grant_button.isEnabled())
        self.assertFalse(self.window.helper_buttons["horse"].isEnabled())
        self.assertTrue(self.window.helper_buttons["drink"].isEnabled())
        self.window.grant_selection()
        self.window.toggle_helper("horse")
        self.assertEqual(session.calls, [("diagnose",), ("list_inventory",)])
        session.memory.tainted = True
        self.window._refresh_controls()
        self.assertFalse(self.window.inventory_refresh.isEnabled())
        self.assertTrue(all(not w.isEnabled() for w in self.window.helper_buttons.values()))

    def test_only_one_worker_runs_and_cancel_is_cooperative(self):
        self.attach()
        started, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        second_calls = []
        def operation():
            started.set()
            release.wait(2)
            return "first"
        self.window._run("First", operation)
        self.wait_until(started.is_set)
        self.window._run("Second", lambda: second_calls.append(True))
        self.assertTrue(self.window.busy)
        self.assertTrue(self.window.worker.running)
        self.assertFalse(self.window.inventory_refresh.isEnabled())
        QTest.mouseClick(self.window.cancel_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.worker.cancel.is_set())
        release.set()
        self.wait_until(lambda: not self.window.busy)
        self.assertFalse(self.window.worker.running)
        self.assertEqual(second_calls, [])

    def test_inventory_write_requires_selection_and_refreshes_snapshot(self):
        session = self.attach()
        self.load_inventory()
        self.assertFalse(self.window.quantity_button.isEnabled())
        self.window.apply_quantity()
        self.assertFalse(any(call[0] == "set_quantity" for call in session.calls))
        self.window.inventory_table.selectRow(0)
        selected = session.inventory[0]
        self.assertEqual(self.window.new_quantity.value(), 5)
        self.window.new_quantity.setValue(17)
        QTest.mouseClick(self.window.quantity_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.busy)
        writes = [call for call in session.calls if call[0] == "set_quantity"]
        self.assertEqual(writes, [("set_quantity", selected, 17)])
        self.assertEqual(self.window.inventory_table.rowCount(), 1)
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "17")
        self.assertEqual(self.window.inventory_table.currentRow(), -1)
        self.assertFalse(self.window.quantity_button.isEnabled())

    def test_inventory_reload_invalidates_previous_selection(self):
        session = self.attach()
        self.load_inventory()
        self.window.inventory_table.selectRow(0)
        session.inventory = [replace(session.inventory[0], name="Leather", hash_id=0x1AF7031F403, quantity=9)]
        self.load_inventory()
        self.assertEqual(self.window.inventory_table.currentRow(), -1)
        self.assertFalse(self.window.quantity_button.isEnabled())
        self.window.apply_quantity()
        self.assertFalse(any(call[0] == "set_quantity" for call in session.calls))

    def test_rejected_stale_write_is_reported_without_claiming_success(self):
        session = self.attach()
        self.load_inventory()
        self.window.inventory_table.selectRow(0)
        session.write_error = StaleInventoryError("El objeto cambió durante la lectura")
        self.window.new_quantity.setValue(13)
        with self.assertLogs("valhalla.ui", level="ERROR"):
            QTest.mouseClick(self.window.quantity_button, Qt.MouseButton.LeftButton)
            self.wait_until(lambda: not self.window.busy)
        self.assertEqual(session.inventory[0].quantity, 5)
        self.mocks[2].assert_called_once()
        self.assertIn(str(session.write_error), self.window.activity.toPlainText())

    def test_grant_requires_review_and_automatically_refreshes_inventory(self):
        session = self.attach()
        self.load_inventory()
        self.window.navigate(1)
        self.window.item_table.selectRow(0)
        self.mocks[3].return_value = QMessageBox.StandardButton.No
        QTest.mouseClick(self.window.grant_button, Qt.MouseButton.LeftButton)
        self.assertFalse(any(call[0] == "grant_items" for call in session.calls))
        self.mocks[3].return_value = QMessageBox.StandardButton.Yes
        self.window.grant_quantity.setValue(3)
        QTest.mouseClick(self.window.grant_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual([call for call in session.calls if call[0] == "grant_items"],
                         [("grant_items", (self.window.display_items[0].hash_id,), 3)])
        self.assertEqual(self.window.inventory, session.inventory)
        self.assertEqual(self.window.inventory_table.currentRow(), -1)
        self.assertGreaterEqual(session.calls.count(("list_inventory",)), 2)

    def test_open_inventory_reads_automatically_and_explains_absent_resources(self):
        session = self.attach()
        self.window.navigate(2)
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(self.window.inventory, session.inventory)
        statuses = self.window.resource_statuses
        self.assertEqual(sum(s.rule.automatic for s in statuses), 13)
        silver = next(s for s in statuses if s.rule.name == "Plata")
        self.assertIsNone(silver.quantity)
        self.assertIn("no confirma", silver.detail.casefold())

    def test_resource_repair_sends_only_missing_basics_once(self):
        from valhalla.resources import BASE_NAMES
        session = self.attach()
        self.window.navigate(2)
        self.wait_until(lambda: not self.window.busy)
        QTest.mouseClick(self.window.repair_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.busy)
        calls = [c for c in session.calls if c[0] == "grant_items"]
        self.assertEqual(len(calls), 12)  # Iron already has a record.
        self.assertTrue(all(c[2] == 1 and c[1][0] in BASE_NAMES for c in calls))
        self.assertEqual(self.window.inventory_tabs.currentIndex(), 1)
        self.assertIn("0 recursos localizados", self.window.activity.toPlainText())
        QTest.mouseClick(self.window.repair_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual([c for c in session.calls if c[0] == "grant_items"], calls)

    def test_rations_direct_edit_is_disabled(self):
        from valhalla.resources import RATIONS_HASH
        session = self.attach()
        session.inventory = [replace(session.inventory[0], hash_id=RATIONS_HASH, name="Raciones")]
        self.load_inventory()
        self.window.inventory_table.selectRow(0)
        self.assertFalse(self.window.quantity_button.isEnabled())
        self.window.apply_quantity()
        self.assertFalse(any(c[0] == "set_quantity" for c in session.calls))

    def test_close_waits_for_active_operation_then_disconnects_once(self):
        session = self.attach()
        started, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def operation():
            started.set()
            release.wait(2)
        self.window._run("Pending", operation)
        self.wait_until(started.is_set)
        self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.worker.cancel.is_set())
        self.assertTrue(self.window.close_pending)
        release.set()
        self.wait_until(lambda: not self.window.isVisible() and not self.window.busy)
        self.assertIsNone(self.window.session)
        self.assertEqual(session.calls.count(("close",)), 1)

    def test_failed_disconnect_during_close_does_not_loop(self):
        session = self.attach()
        session.close_error = RuntimeError("No fue posible restaurar una ayuda")
        with self.assertLogs("valhalla.ui", level="ERROR"):
            self.window.close()
            self.wait_until(lambda: session.calls.count(("close",)) >= 1 and not self.window.busy)
            QTest.qWait(50)
        self.assertEqual(session.calls.count(("close",)), 1)
        self.assertFalse(self.window.close_pending)
        self.assertIs(self.window.session, session)
        self.assertTrue(self.window.isVisible())
        self.mocks[2].assert_called_once()
        session.close_error = None


if __name__ == "__main__":
    unittest.main()
