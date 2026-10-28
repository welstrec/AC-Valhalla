"""Offline UI behavior for the visual catalogue and reviewed request list."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from valhalla.catalog import Catalog
from valhalla.engine import FeatureState, GrantResult
from valhalla.gallery import GalleryView
from valhalla.library import UserLibrary
from valhalla import ui


class InertSession:
    """Only records requests; deliberately cannot open or modify any process."""
    def __init__(self):
        keys = ("grant", "inventory", "persistence", "horse", "drink", "orlog", "stealth", "boost", "map")
        self.features = {key: FeatureState(key, key, available=True) for key in keys}
        self.tainted = False
        self.identity = (123, 456)
        self.memory = SimpleNamespace(tainted=False)
        self.calls = []
        self.fail_on = None

    def grant_items(self, hashes, quantity, cancel=None, progress=None):
        self.calls.append((tuple(hashes), quantity))
        if hashes[0] == self.fail_on:
            raise RuntimeError("Resultado incierto de prueba")
        return GrantResult(requested=len(hashes), completed=len(hashes), confirmed=len(hashes))

    def list_inventory(self):
        return []


class VisualUITests(unittest.TestCase):
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
            patch.object(ui, "GameSession", side_effect=AssertionError("No real session permitted")),
            patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.window = ui.MainWindow(self.catalog, library=self.library)
        self.window.timer.stop()
        self.window.navigate(1)
        self.window.show()
        QTest.qWait(5)

    def tearDown(self):
        self.window.worker.cancel.set()
        self.wait_until(lambda: not self.window.busy)
        self.window.session = None
        self.window.close_pending = False
        self.window.disconnect_pending = False
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(5)
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), "La interfaz no alcanzó el estado esperado")

    def filter(self, query):
        self.window.search.setText(query)
        self.window.search_timer.stop()
        self.window._filter_catalog()

    def select(self, item):
        self.assertTrue(self.window.gallery.select_hash(item.hash_id))
        self.assertEqual(self.window.detail_item, item)

    def connect_inert(self):
        session = InertSession()
        self.window.session = session
        self.window._refresh_controls()
        return session

    def test_compact_list_is_default_and_optional_cards_share_selection(self):
        self.assertEqual(self.window.gallery.count(), len(self.catalog.items))
        self.assertEqual(self.window.view_mode.currentText(), "Lista")
        self.assertIs(self.window.item_views.currentWidget(), self.window.item_table)
        self.assertIsNone(self.window.session)
        self.mocks[1].assert_not_called()
        target = self.catalog.items[10]
        self.select(target)
        self.assertEqual(self.window.display_items[self.window.item_table.currentRow()], target)
        self.assertTrue(self.window.queue_add_button.isEnabled())
        self.assertFalse(self.window.grant_button.isEnabled())
        self.window.view_mode.setCurrentIndex(1)
        self.assertIs(self.window.item_views.currentWidget(), self.window.gallery)
        self.window.item_table.selectRow(15)
        self.assertEqual(self.window.gallery.selected_item(), self.window.display_items[15])
        self.assertEqual(self.window.detail_item, self.window.display_items[15])
        self.window.view_mode.setCurrentIndex(0)
        self.assertIs(self.window.item_views.currentWidget(), self.window.item_table)
        self.assertEqual(self.window.detail_item, self.window.gallery.selected_item())

    def test_combined_type_category_and_search_filters_clear_stale_actions(self):
        target = self.catalog.by_hash["000001F142C45A17"]  # Frostbita, unique source name.
        self.select(target)
        kind = self.window.icon_provider.describe(target).kind
        self.window.kind_filter.setCurrentIndex(self.window.kind_filter.findData(kind))
        self.window.category.setCurrentIndex(self.window.category.findData(target.categories[0]))
        self.filter("frostbita")
        self.assertEqual(self.window.display_items, [target])
        self.assertEqual(self.window.gallery.count(), 1)
        self.select(target)
        self.filter("no such catalogue object 12345")
        self.assertEqual(self.window.gallery.count(), 0)
        self.assertIsNone(self.window.gallery.selected_item())
        self.assertIsNone(self.window.detail_item)
        self.assertEqual(self.window.item_table.currentRow(), -1)
        self.assertFalse(self.window.queue_add_button.isEnabled())
        self.assertFalse(self.window.favorite_button.isEnabled())

    def test_tree_pack_opens_only_its_members_and_search_stays_within_pack(self):
        pack = next(pack for pack in self.catalog.packs if 2 <= len(pack.hashes) <= 8)
        self.assertTrue(self.window.navigator.select_key("pack:" + pack.id))
        self.assertEqual(self.window.catalog_tabs.currentIndex(), 0)
        self.assertEqual({item.hash_id for item in self.window.display_items}, set(pack.hashes))
        self.assertEqual(self.window.gallery.count(), len(pack.hashes))
        self.assertEqual(self.window.item_table.rowCount(), len(pack.hashes))
        target = self.catalog.by_hash[pack.hashes[-1]]
        self.filter(target.hash_id)
        self.assertEqual(self.window.display_items, [target])
        outsider = next(item for item in self.catalog.items if item.hash_id not in pack.hashes)
        self.filter(outsider.hash_id)
        self.assertEqual(self.window.display_items, [])
        self.filter("")
        self.window.navigator.reset_scope()
        self.assertEqual(len(self.window.display_items), len(self.catalog.items))
        self.mocks[1].assert_not_called()

    def test_tree_folders_and_item_types_choose_the_right_lists(self):
        self.assertTrue(self.window.navigator.select_key("packs"))
        self.assertEqual(self.window.catalog_tabs.currentIndex(), 1)
        self.assertEqual({pack.id for pack in self.window.display_packs}, set(self.catalog.by_pack))
        sample = next(item for item in self.catalog.items
                      if self.window.icon_provider.describe(item).kind == "axe")
        self.assertTrue(self.window.navigator.select_key("kind:axe"))
        self.assertEqual(self.window.catalog_tabs.currentIndex(), 0)
        expected = {item.hash_id for item in self.catalog.items
                    if self.window.icon_provider.describe(item).kind == "axe"}
        self.assertEqual({item.hash_id for item in self.window.display_items}, expected)
        self.assertIn(sample.hash_id, expected)
        self.select(sample)
        pack = next(pack for pack in self.catalog.packs if sample.hash_id not in pack.hashes)
        self.window.navigator.select_key("pack:" + pack.id)
        self.assertIsNone(self.window.detail_item)
        self.assertEqual(self.window.item_table.currentRow(), -1)
        self.assertFalse(self.window.grant_button.isEnabled())
        self.mocks[1].assert_not_called()

    def test_details_are_optional_and_open_pack_uses_the_explorer(self):
        self.assertTrue(self.window.detail_panel.isHidden())
        self.select(self.catalog.items[0])
        QTest.mouseClick(self.window.details_toggle, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.detail_panel.isHidden())
        self.assertEqual(self.window.detail_item, self.catalog.items[0])
        QTest.mouseClick(self.window.details_toggle, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.detail_panel.isHidden())
        self.window.navigator.select_key("packs")
        pack = next(pack for pack in self.window.display_packs if 2 <= len(pack.hashes) <= 8)
        self.window.pack_table.selectRow(self.window.display_packs.index(pack))
        self.assertTrue(self.window.contents_button.isEnabled())
        QTest.mouseClick(self.window.contents_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.navigator.current_scope().pack_id, pack.id)
        self.assertEqual({item.hash_id for item in self.window.display_items}, set(pack.hashes))
        self.assertTrue(self.window.catalog_path.text())
        self.mocks[1].assert_not_called()

    def test_tree_pack_grant_uses_whole_pack_until_an_individual_row_is_selected(self):
        session = self.connect_inert()
        pack = next(pack for pack in self.catalog.packs
                    if 2 <= len(pack.hashes) <= 8 and pack.quantity <= 999)
        previous = next(item for item in self.catalog.items if item.hash_id not in pack.hashes)
        self.select(previous)
        self.window.grant_quantity.setValue(17)
        self.assertTrue(self.window.navigator.select_key("pack:" + pack.id))
        self.assertEqual(self.window.item_table.currentRow(), -1)
        self.assertIsNone(self.window.detail_item)
        self.assertEqual(self.window.detail_pack, pack)
        self.assertEqual({item.hash_id for item in self.window.display_items}, set(pack.hashes))
        self.assertEqual(self.window.grant_quantity.value(), pack.quantity)
        self.assertTrue(self.window.grant_button.isEnabled())
        self.window.grant_selection()
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(session.calls, [(tuple(pack.hashes), pack.quantity)])
        target = self.catalog.by_hash[pack.hashes[-1]]
        self.select(target)
        self.assertIsNone(self.window.detail_pack)
        self.window.grant_quantity.setValue(7)
        self.window.grant_selection()
        self.wait_until(lambda: not self.window.busy)
        expected_quantity = min(7, self.window.quantity_policy.limit(target.hash_id))
        self.assertEqual(session.calls, [(tuple(pack.hashes), pack.quantity), ((target.hash_id,), expected_quantity)])
        self.assertTrue(all(previous.hash_id not in hashes for hashes, _ in session.calls))
        self.mocks[1].assert_not_called()

    def test_favorites_persist_and_removing_filtered_favorite_clears_selection(self):
        target = self.catalog.items[0]
        self.select(target)
        self.window.toggle_favorite()
        self.assertTrue(UserLibrary(self.library.root).is_favorite(target.hash_id))
        QTest.mouseClick(self.window.favorite_filter, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.display_items, [target])
        self.assertTrue(all(target.hash_id in pack.hashes for pack in self.window.display_packs))
        self.select(target)
        self.window.toggle_favorite()
        self.assertFalse(self.library.is_favorite(target.hash_id))
        self.assertEqual(self.window.gallery.count(), 0)
        self.assertIsNone(self.window.detail_item)
        self.assertFalse(self.window.queue_add_button.isEnabled())

    def test_image_import_updates_detail_and_can_return_to_original_illustration(self):
        target = self.catalog.items[0]
        self.select(target)
        source = Path(self.temporary.name) / "my-image.jpg"
        Image.new("RGB", (240, 160), "#ec1739").save(source)
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=(str(source), "")):
            self.window.import_item_image()
        self.assertIsNotNone(self.library.image_path(target.hash_id))
        self.assertIn("importada", self.window.detail_source.text())
        self.assertTrue(self.window.reset_image_button.isEnabled())
        color = self.window._item_pixmap(target, 100).toImage().pixelColor(50, 30)
        self.assertGreater(color.red(), 230)
        self.assertLess(color.green(), 30)
        self.window.reset_item_image()
        self.assertIsNone(self.library.image_path(target.hash_id))
        self.assertIn("semántica", self.window.detail_source.text())
        self.assertFalse(self.window.reset_image_button.isEnabled())

    def test_pack_preview_contains_all_items_and_activation_opens_one_object(self):
        pack = next(pack for pack in self.catalog.packs if 2 <= len(pack.hashes) <= 8)
        self.window.catalog_tabs.setCurrentIndex(1)
        self.window.pack_table.selectRow(self.window.display_packs.index(pack))
        self.assertEqual(self.window.detail_pack, pack)
        target = self.catalog.by_hash[pack.hashes[-1]]
        inspected = []
        def inspect(dialog):
            preview = dialog.findChild(GalleryView)
            self.assertIsNotNone(preview)
            self.assertEqual(preview.count(), len(pack.hashes))
            self.assertEqual({item.hash_id for item in preview.model.items}, set(pack.hashes))
            inspected.append(True)
            preview.activated.emit(target)
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, "exec", new=inspect):
            self.window.show_pack_contents()
        self.assertEqual(inspected, [True])
        self.assertEqual(self.window.catalog_tabs.currentIndex(), 0)
        self.assertEqual(self.window.display_items, [target])
        self.assertEqual(self.window.detail_item, target)
        self.assertEqual(self.window.request_queue.count, 0)
        self.mocks[1].assert_not_called()

    def test_offline_queue_deduplicates_pack_objects_and_replaces_quantity(self):
        pack = next(pack for pack in self.catalog.packs if 2 <= len(pack.hashes) <= 8
                    and not any(self.window.quantity_policy.is_unique(hash_id) for hash_id in pack.hashes))
        self.window.catalog_tabs.setCurrentIndex(1)
        self.window.pack_table.selectRow(self.window.display_packs.index(pack))
        self.window.grant_quantity.setValue(3)
        self.window.queue_selection()
        self.assertEqual(self.window.request_queue.count, len(pack.hashes))
        self.assertTrue(all(entry.quantity == 3 for entry in self.window.request_queue.entries))
        self.window.catalog_tabs.setCurrentIndex(0)
        target = self.catalog.by_hash[pack.hashes[0]]
        self.select(target)
        self.window.grant_quantity.setValue(9)
        self.window.queue_selection()
        self.assertEqual(self.window.request_queue.count, len(pack.hashes))
        self.assertEqual(self.window.request_queue.entries[0].quantity, 9)
        self.assertTrue(all(entry.quantity == 3 for entry in self.window.request_queue.entries[1:]))
        self.window.catalog_tabs.setCurrentIndex(2)
        self.assertEqual(self.window.queue_table.rowCount(), len(pack.hashes))
        self.assertFalse(self.window.grant_button.isEnabled())
        self.assertFalse(self.window.grant_quantity.isEnabled())
        self.window.queue_table.selectRow(0)
        self.window.remove_queue_selection()
        self.assertEqual(self.window.request_queue.count, len(pack.hashes) - 1)
        self.window.clear_queue()
        self.assertEqual(self.window.queue_table.rowCount(), 0)
        self.mocks[1].assert_not_called()

    def test_queue_execution_requires_review_and_preserves_each_quantity(self):
        session = self.connect_inert()
        targets = self.catalog.items[:2]
        for target, quantity in zip(targets, (3, 7)):
            self.select(target)
            self.window.grant_quantity.setValue(quantity)
            self.window.queue_selection()
        self.window.catalog_tabs.setCurrentIndex(2)
        self.mocks[3].return_value = QMessageBox.StandardButton.No
        self.window.grant_selection()
        self.assertEqual(session.calls, [])
        self.assertEqual(self.window.request_queue.count, 2)
        self.mocks[3].return_value = QMessageBox.StandardButton.Yes
        self.window.grant_selection()
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(session.calls, [((targets[0].hash_id,), 3), ((targets[1].hash_id,), 7)])
        self.assertEqual(self.window.request_queue.count, 0)
        self.assertEqual(self.window.queue_table.rowCount(), 0)
        self.assertFalse(self.window.grant_button.isEnabled())
        self.assertEqual(self.window.last_result.confirmed, 2)

    def test_failed_request_is_removed_once_but_unsent_entries_remain(self):
        session = self.connect_inert()
        targets = self.catalog.items[:3]
        for target in targets:
            self.window.request_queue.add([target.hash_id], 2, target.name)
        self.window._fill_queue()
        session.fail_on = targets[1].hash_id
        self.window.catalog_tabs.setCurrentIndex(2)
        self.window.grant_selection()
        self.wait_until(lambda: not self.window.busy)
        self.assertEqual(session.calls, [((targets[0].hash_id,), 2), ((targets[1].hash_id,), 2)])
        self.assertEqual([entry.hash_id for entry in self.window.request_queue.entries], [targets[2].hash_id])
        self.assertEqual(self.window.queue_table.item(0, 2).text(), targets[2].hash_id)
        self.mocks[2].assert_called_once()
        self.assertIn("Resultado incierto", self.window.activity.toPlainText())


if __name__ == "__main__":
    unittest.main()
