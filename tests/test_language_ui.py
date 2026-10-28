"""English/Spanish UI behavior with isolated preferences and inert sessions."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtCore import QLocale
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from valhalla.catalog import Catalog
from valhalla.engine import FeatureState, InventoryItem
from valhalla.i18n import resolve_language
from valhalla.library import UserLibrary
from valhalla import ui


IRON = "000001AF7031F3F9"
LEATHER = "000001AF7031F403"


class InertSession:
    """No native process API; every observable operation is recorded."""
    def __init__(self):
        keys = ("grant", "inventory", "persistence", "horse", "drink", "orlog", "stealth", "boost", "map")
        self.features = {key: FeatureState(key, key, available=True) for key in keys}
        self.identity = (123, 456)
        self.tainted = False
        self.memory = SimpleNamespace(tainted=False)
        self.calls = []
        self.inventory = []

    def list_inventory(self):
        self.calls.append(("read",))
        return list(self.inventory)


class LanguageUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.catalog = Catalog.load()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.settings = self.folder / "settings.json"
        self.library = UserLibrary(self.folder / "library")
        self.original_locale = QLocale()
        self.windows = []
        self.release_events = []
        self.patches = [
            patch.object(ui, "list_games", return_value=[]),
            patch.object(ui, "GameSession", side_effect=AssertionError("No real game process permitted")),
            patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        ]
        self.mocks = [entry.start() for entry in self.patches]
        self.window = self.make_window("es")

    def make_window(self, language):
        window = ui.MainWindow(self.catalog, library=self.library,
                               language=language, settings_path=self.settings)
        self.windows.append(window)
        window.timer.stop()
        window.show()
        QTest.qWait(5)
        return window

    def tearDown(self):
        for event in self.release_events:
            event.set()
        for window in reversed(self.windows):
            window.worker.cancel.set()
            self.wait_until(lambda w=window: not w.busy)
            window.session = None
            window.close_pending = False
            window.disconnect_pending = False
            window.close()
            window.deleteLater()
        QTest.qWait(5)
        for entry in reversed(self.patches):
            entry.stop()
        QLocale.setDefault(self.original_locale)
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), "The interface did not reach the expected state")

    def filter(self, query):
        self.window.search.setText(query)
        self.window.search_timer.stop()
        self.window._filter_catalog()

    def select(self, hash_id):
        self.assertTrue(self.window.gallery.select_hash(hash_id))
        self.assertEqual(self.window.detail_item.hash_id, hash_id)

    def row(self, session, hash_id, quantity, address=0x1000):
        return InventoryItem(int(hash_id, 16), self.catalog.by_hash[hash_id].name,
                             quantity, address, session.identity, 1)

    def test_spanish_remains_available_and_english_translates_primary_navigation(self):
        self.assertEqual(self.window.translator.language, "es")
        self.assertEqual(self.window.nav_buttons[0].text(), "Inicio")
        self.assertEqual(self.window.attach_button.text().replace("&", ""), "Adjuntar")
        self.window.set_language("en", persist=False)
        self.assertEqual([entry.text() for entry in self.window.nav_buttons],
                         ["Home", "Items and packs", "My inventory", "Game helpers", "Compatibility", "Activity"])
        self.assertEqual(self.window.refresh_button.text().replace("&", ""), "Find game")
        self.assertEqual(self.window.attach_button.text().replace("&", ""), "Attach")
        self.assertEqual(self.window.attach_button.accessibleName(), "Attach")
        self.assertEqual(self.window.item_table.horizontalHeaderItem(0).text(), "Item")
        self.assertEqual(self.window.show_quantities.text(), "Show quantities")
        self.assertIn("Language / Idioma", [action.text() for action in self.window.menuBar().actions()])
        self.assertIn("&Help", [action.text() for action in self.window.menuBar().actions()])
        self.assertTrue(self.window.language_actions["en"].isChecked())
        self.assertFalse(self.window.language_actions["es"].isChecked())
        pack_row = next(i for i, pack in enumerate(self.window.display_packs) if pack.id == "99201")
        self.assertIn("Store and events / Helix items", self.window.pack_table.item(pack_row, 0).toolTip())
        self.mocks[1].assert_not_called()

    def test_english_can_be_selected_at_construction_without_changing_the_catalogue(self):
        window = self.make_window("en")
        self.assertEqual(window.translator.language, "en")
        self.assertEqual(window.attach_button.text().replace("&", ""), "Attach")
        self.assertEqual(window.gallery.count(), len(self.catalog.items))
        self.assertIs(window.catalog, self.catalog)
        self.assertIsNone(window.session)
        self.assertFalse(self.settings.exists())
        self.mocks[1].assert_not_called()

    def test_switch_keeps_scope_query_selection_quantities_snapshot_and_request_list(self):
        self.window.navigate(1)
        session = InertSession()
        self.window.session = session
        rows = [self.row(session, IRON, 100_000), self.row(session, LEATHER, 0, 0x2000)]
        self.window._inventory_loaded(rows)
        pack = next(pack for pack in self.catalog.packs if IRON in pack.hashes)
        self.window.navigator.select_key("pack:" + pack.id)
        target = self.catalog.by_hash[IRON]
        self.window.category.setCurrentIndex(self.window.category.findData(target.categories[0]))
        kind = self.window.icon_provider.describe(target).kind
        self.window.kind_filter.setCurrentIndex(self.window.kind_filter.findData(kind))
        self.library.toggle_favorite(IRON)
        self.window.favorite_filter.setChecked(True)
        self.filter(IRON)
        self.select(IRON)
        self.window.grant_quantity.setValue(100_000)
        self.window.inventory_table.selectRow(0)
        self.window.new_quantity.setValue(50_000)
        self.window.request_queue.add([IRON], 70_000, target.name)
        self.window._fill_queue()
        self.window.process_details.setPlainText("Proceso: ACValhalla.exe\nPID: 123")
        self.window._activity("Inventario actualizado: 2 registros.")
        before = {
            "scope": self.window.navigator.current_scope(),
            "query": self.window.search.text(),
            "category": self.window.category.currentData(),
            "kind": self.window.kind_filter.currentData(),
            "inventory": list(self.window.inventory),
            "totals": dict(self.window.inventory_totals),
            "identity": self.window.inventory_identity,
            "time": self.window.inventory_loaded_at,
            "queue": self.window.request_queue.entries,
        }
        self.window.set_language("en", persist=False)
        self.assertEqual(self.window.navigator.current_scope(), before["scope"])
        self.assertEqual(self.window.search.text(), before["query"])
        self.assertEqual(self.window.category.currentData(), before["category"])
        self.assertEqual(self.window.kind_filter.currentData(), before["kind"])
        self.assertTrue(self.window.favorite_filter.isChecked())
        self.assertEqual(self.window.detail_item.hash_id, IRON)
        self.assertEqual(self.window.detail_item.name, target.name)
        self.assertEqual(self.window.grant_quantity.value(), 100_000)
        self.assertEqual(self.window.new_quantity.value(), 50_000)
        self.assertEqual(self.window.inventory, before["inventory"])
        self.assertEqual(self.window.inventory_totals, before["totals"])
        self.assertEqual(self.window.inventory_identity, before["identity"])
        self.assertEqual(self.window.inventory_loaded_at, before["time"])
        self.assertEqual(self.window.request_queue.entries, before["queue"])
        self.assertEqual(session.calls, [])
        self.assertTrue(self.library.is_favorite(IRON))
        self.window.set_language("es", persist=False)
        self.assertEqual(self.window.detail_item.hash_id, IRON)
        self.assertEqual(self.window.search.text(), IRON)
        self.assertEqual(self.window.grant_quantity.value(), 100_000)
        self.assertEqual(session.calls, [])

    def test_translated_category_and_type_labels_keep_their_original_filter_ids(self):
        self.window.navigate(1)
        target = next(item for item in self.catalog.items
                      if "ARMOR" in item.categories and self.window.icon_provider.describe(item).kind != "chest")
        self.window.category.setCurrentIndex(self.window.category.findData("ARMOR"))
        kind = self.window.icon_provider.describe(target).kind
        self.window.kind_filter.setCurrentIndex(self.window.kind_filter.findData(kind))
        self.filter(target.hash_id)
        self.select(target.hash_id)
        category_text = self.window.category.currentText()
        self.window.set_language("en", persist=False)
        self.assertEqual(self.window.category.currentData(), "ARMOR")
        self.assertEqual(self.window.kind_filter.currentData(), kind)
        self.assertEqual(self.window.display_items, [target])
        self.assertEqual(self.window.detail_item, target)
        self.assertNotEqual(self.window.category.currentText(), category_text)
        self.assertEqual(self.window.category.currentText(), "Armor")
        self.assertIn("Armor", self.window.item_table.item(0, 0).toolTip().splitlines()[1])
        self.window.set_language("es", persist=False)
        self.assertEqual(self.window.display_items, [target])
        self.assertEqual(self.window.category.currentData(), "ARMOR")
        self.assertIn("Armaduras", self.window.item_table.item(0, 0).toolTip().splitlines()[1])

    def test_backend_error_and_image_dialog_are_presented_in_the_selected_language(self):
        self.window.navigate(1)
        self.select(IRON)
        self.window.set_language("en", persist=False)
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            self.window.import_item_image()
        self.assertIn("image", dialog.call_args.args[1].lower())
        self.assertNotIn("Elegir", dialog.call_args.args[1])
        self.window._run("Leer inventario", lambda: (_ for _ in ()).throw(RuntimeError("Carga una partida antes de añadir objetos")))
        self.wait_until(lambda: not self.window.busy)
        self.wait_until(lambda: self.mocks[2].called)
        args = self.mocks[2].call_args.args
        self.assertIn("operation", args[1].lower())
        self.assertEqual(args[2], "Load a save before adding items")
        self.assertNotIn("Carga una partida", self.window.activity.toPlainText())
        self.window.set_language("es", persist=False)
        self.assertIn("Carga una partida antes de añadir objetos", self.window.activity.toPlainText())
        self.mocks[1].assert_not_called()

    def test_language_menu_saves_only_temporary_preferences_and_is_used_on_relaunch(self):
        self.settings.write_text('{"unrelated": {"keep": true}}', encoding="utf-8")
        self.window.language_actions["en"].trigger()
        self.assertEqual(self.window.translator.language, "en")
        saved = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(saved["language"], "en")
        self.assertEqual(saved["unrelated"], {"keep": True})
        with patch("valhalla.i18n._system_language", return_value="es"):
            language = resolve_language(settings_path=self.settings)
        other = self.make_window(language)
        self.assertEqual(other.translator.language, "en")
        self.assertEqual(other.attach_button.text().replace("&", ""), "Attach")
        self.mocks[1].assert_not_called()

    def test_language_switch_does_not_cancel_or_duplicate_an_active_operation(self):
        session = InertSession()
        self.window.session = session
        gate, started = threading.Event(), threading.Event()
        self.release_events.append(gate)
        calls, results = [], []

        def delayed():
            calls.append("start")
            started.set()
            if not gate.wait(3):
                raise RuntimeError("The inert operation was not released")
            calls.append("finish")
            return "result"

        self.window._run("Leer inventario", delayed, results.append)
        self.wait_until(started.is_set)
        self.assertTrue(self.window.busy)
        self.window.set_language("en", persist=False)
        self.assertTrue(self.window.busy)
        self.assertTrue(self.window.worker.running)
        self.assertFalse(self.window.worker.cancel.is_set())
        self.assertEqual(calls, ["start"])
        self.assertEqual(session.calls, [])
        gate.set()
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(calls, ["start", "finish"])
        self.assertEqual(results, ["result"])
        self.assertEqual(self.window.translator.language, "en")
        self.assertEqual(session.calls, [])
