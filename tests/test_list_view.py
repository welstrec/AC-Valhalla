"""Compact list thumbnails stay lazy and use the user's raster overrides."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from valhalla.library import UserLibrary
from valhalla.list_view import RowArtworkDelegate


class Provider:
    def __init__(self):
        self.calls = []

    def pixmap(self, item, size):
        self.calls.append((item.hash_id, size))
        picture = QPixmap(size, size)
        picture.fill(QColor("#109060"))
        return picture


class ListArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.library = UserLibrary(Path(self.temporary.name) / "library")
        self.provider = Provider()
        self.items = {f"{number:016X}": SimpleNamespace(hash_id=f"{number:016X}", name=f"Object {number}")
                      for number in range(1, 1001)}
        self.table = QTableWidget(1000, 1)
        self.table.resize(400, 280)
        self.table.setColumnWidth(0, 340)
        self.delegate = RowArtworkDelegate(self.provider, self.library, self.items, self.table)
        self.table.setItemDelegateForColumn(0, self.delegate)
        for row, item in enumerate(self.items.values()):
            cell = QTableWidgetItem(item.name)
            cell.setData(Qt.ItemDataRole.UserRole, item.hash_id)
            self.table.setItem(row, 0, cell)

    def tearDown(self):
        self.table.close()
        self.table.deleteLater()
        QTest.qWait(1)
        self.temporary.cleanup()

    def option(self, row):
        option = QStyleOptionViewItem()
        self.delegate.initStyleOption(option, self.table.model().index(row, 0))
        return option

    def test_full_list_creates_no_art_until_visible_and_scroll_resolves_last_rows(self):
        self.assertEqual(self.provider.calls, [])
        self.table.show()
        QTest.qWait(10)
        first_hashes = {hash_id for hash_id, _ in self.provider.calls}
        self.assertTrue(first_hashes)
        self.assertLess(len(first_hashes), 40)
        self.table.scrollToBottom()
        QTest.qWait(10)
        seen = {hash_id for hash_id, _ in self.provider.calls}
        self.assertIn(f"{1000:016X}", seen)
        self.assertLess(len(seen), 80)
        self.assertTrue(all(size == 24 for _, size in self.provider.calls))

    def test_user_image_is_visible_and_corrupt_override_returns_to_semantic_art(self):
        item = next(iter(self.items.values()))
        source = Path(self.temporary.name) / "red.png"
        Image.new("RGB", (70, 70), "#e91b31").save(source)
        destination = self.library.set_image(item.hash_id, source)
        option = self.option(0)
        color = option.icon.pixmap(24, 24).toImage().pixelColor(12, 12)
        self.assertGreater(color.red(), 220)
        self.assertLess(color.green(), 40)
        self.assertEqual(self.provider.calls, [])
        destination.write_bytes(b"broken raster file")
        self.delegate.clear_cache()
        fallback = self.option(0)
        color = fallback.icon.pixmap(24, 24).toImage().pixelColor(12, 12)
        self.assertGreater(color.green(), 100)
        self.assertEqual(self.provider.calls, [(item.hash_id, 24)])

    def test_local_image_cache_is_bounded_and_can_be_invalidated(self):
        source = Path(self.temporary.name) / "image.png"
        Image.new("RGB", (30, 30), "#e91b31").save(source)
        self.delegate.cache_limit = 3
        for row, item in enumerate(list(self.items.values())[:7]):
            self.library.set_image(item.hash_id, source)
            self.option(row)
        self.assertEqual(len(self.delegate._image_cache), 3)
        self.assertEqual(self.provider.calls, [])
        self.delegate.clear_cache()
        self.assertEqual(len(self.delegate._image_cache), 0)

    def test_unknown_identifier_keeps_plain_list_text_without_art_lookup(self):
        self.table.item(0, 0).setData(Qt.ItemDataRole.UserRole, "missing")
        option = self.option(0)
        self.assertEqual(option.text, "Object 1")
        self.assertTrue(option.icon.isNull())
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
