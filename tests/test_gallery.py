"""The visual catalogue stays virtual, keyboard-accessible and inert."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from valhalla.catalog import Catalog, Item
from valhalla.gallery import GalleryView, ITEM_ROLE
from valhalla.theme import apply_theme
from valhalla.i18n import Translator


class FakeProvider:
    def __init__(self):
        self.calls = []

    def pixmap(self, item, size=128):
        self.calls.append(item.hash_id)
        result = QPixmap(size, size)
        result.fill(QColor("#d9bb7b"))
        return result


class FakeLibrary:
    def __init__(self):
        self.favorites = set()
        self.images = {}

    def is_favorite(self, hash_id):
        return hash_id in self.favorites

    def image_path(self, hash_id):
        return self.images.get(hash_id)


class GalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_theme(cls.app)
        cls.catalog = Catalog.load()

    def setUp(self):
        self.provider = FakeProvider()
        self.library = FakeLibrary()
        self.gallery = GalleryView(self.provider, self.library)
        self.gallery.resize(720, 470)
        self.gallery.show()
        QTest.qWait(10)

    def tearDown(self):
        self.gallery.close()
        self.gallery.deleteLater()
        QTest.qWait(5)

    def test_full_catalogue_uses_model_rows_not_per_item_widgets(self):
        before = len(self.gallery.findChildren(QWidget))
        self.gallery.set_items(self.catalog.items)
        QTest.qWait(40)
        self.assertEqual(self.gallery.count(), 2327)
        self.assertEqual(len(self.gallery.findChildren(QWidget)), before)
        self.assertLess(len(set(self.provider.calls)), 100)
        self.assertIsNone(self.gallery.selected_item())

    def test_select_hash_and_reset_emit_item_then_none(self):
        self.gallery.set_items(self.catalog.items)
        spy = QSignalSpy(self.gallery.selection_changed)
        wanted = self.catalog.items[120]
        self.assertTrue(self.gallery.select_hash(wanted.hash_id))
        self.assertEqual(self.gallery.selected_item(), wanted)
        self.assertEqual(spy.at(spy.count() - 1)[0], wanted)
        self.gallery.set_items([])
        self.assertIsNone(self.gallery.selected_item())
        self.assertEqual(spy.at(spy.count() - 1)[0], None)
        self.assertEqual(self.gallery.count(), 0)

    def test_keyboard_navigation_and_enter_open_item_without_game_action(self):
        self.gallery.set_items(self.catalog.items[:20])
        self.gallery.select_hash(self.catalog.items[0].hash_id)
        self.gallery.view.setFocus()
        spy = QSignalSpy(self.gallery.activated)
        QTest.keyClick(self.gallery.view, Qt.Key.Key_Right)
        self.assertEqual(self.gallery.selected_item(), self.catalog.items[1])
        QTest.keyClick(self.gallery.view, Qt.Key.Key_Return)
        self.assertEqual(spy.count(), 1)
        self.assertEqual(spy.at(0)[0], self.catalog.items[1])

    def test_accessible_text_includes_identity_category_and_favorite(self):
        wanted = self.catalog.items[0]
        self.library.favorites.add(wanted.hash_id)
        self.gallery.set_items([wanted])
        index = self.gallery.model.index(0)
        text = index.data(Qt.ItemDataRole.AccessibleTextRole)
        self.assertIn(wanted.name, text)
        self.assertIn(wanted.hash_id, text)
        self.assertIn("favorito", text)
        self.assertEqual(index.data(ITEM_ROLE), wanted)

    def test_scroll_to_last_item_requests_only_visible_art(self):
        self.gallery.set_items(self.catalog.items)
        QTest.qWait(30)
        self.provider.calls.clear()
        wanted = self.catalog.items[-1]
        self.gallery.select_hash(wanted.hash_id)
        QTest.qWait(60)
        self.assertEqual(self.gallery.selected_item(), wanted)
        self.assertIn(wanted.hash_id, self.provider.calls)
        self.assertLess(len(set(self.provider.calls)), 100)

    def test_custom_raster_image_is_used_and_corrupt_image_falls_back(self):
        item = self.catalog.items[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.png"
            image = QPixmap(64, 48)
            image.fill(QColor("red"))
            self.assertTrue(image.save(str(path)))
            self.library.images[item.hash_id] = path
            pixmap, custom = self.gallery.delegate._thumbnail(item, 112)
            self.assertTrue(custom)
            self.assertFalse(pixmap.isNull())
            self.assertEqual(self.provider.calls, [])
            path.write_bytes(b"invalid raster")
            self.gallery.refresh_visuals()
            pixmap, custom = self.gallery.delegate._thumbnail(item, 112)
            self.assertFalse(custom)
            self.assertIn(item.hash_id, self.provider.calls)

    def test_local_thumbnail_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            image = QPixmap(16, 16)
            image.fill(QColor("blue"))
            for index in range(140):
                path = Path(folder) / f"{index}.png"
                image.save(str(path))
                item = Item(f"{index + 1:016X}", f"Test {index}", ("Tests",))
                self.library.images[item.hash_id] = path
                self.gallery.delegate._thumbnail(item, 32)
            self.assertEqual(len(self.gallery.delegate._image_cache), 128)

    def test_text_scale_changes_card_geometry(self):
        self.gallery.set_items(self.catalog.items[:3])
        self.gallery.set_scale(1.5)
        self.assertEqual(self.gallery.view.gridSize().width(), 264)
        self.assertEqual(self.gallery.view.gridSize().height(), 324)
        QTest.qWait(10)
        self.assertFalse(self.gallery.grab().isNull())

    def test_language_switch_translates_accessibility_and_counts_without_resetting_selection(self):
        self.gallery.close()
        translator = Translator("en")
        self.gallery = GalleryView(self.provider, self.library, translator=translator)
        item, absent = self.catalog.items[:2]
        self.library.favorites.add(item.hash_id)
        self.gallery.set_items([item, absent])
        self.gallery.set_quantities({int(item.hash_id, 16): 100000})
        self.gallery.select_hash(item.hash_id)
        selection_changes = QSignalSpy(self.gallery.selection_changed)
        index = self.gallery.model.index(0)
        text = index.data(Qt.ItemDataRole.AccessibleTextRole)
        self.assertEqual(self.gallery.accessibleName(), "Item gallery")
        self.assertIn(", favorite", text)
        self.assertIn("In game: 100,000", text)
        self.assertIn(item.name, text)
        self.assertIn(item.hash_id, text)
        self.assertEqual(self.gallery.model.quantity_text(absent), "No record in this reading")
        translator.set_language("es")
        self.gallery.retranslate()
        self.assertEqual(self.gallery.accessibleName(), "Galería de objetos")
        self.assertIn("En partida: 100.000", index.data(Qt.ItemDataRole.AccessibleTextRole))
        self.assertEqual(self.gallery.selected_item(), item)
        self.assertEqual(self.gallery.model.quantities[int(item.hash_id, 16)], 100000)
        self.assertEqual(selection_changes.count(), 0)


if __name__ == "__main__":
    unittest.main()
