"""Small, lazily painted illustrations for the compact catalogue list."""
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem


class RowArtworkDelegate(QStyledItemDelegate):
    """Resolve artwork only for rows Qt actually requests, with bounded caches."""

    def __init__(self, provider, library, by_hash, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.library = library
        self.by_hash = by_hash
        self.cache_limit = 128
        self._image_cache = OrderedDict()

    def clear_cache(self):
        self._image_cache.clear()

    def _pixmap(self, item, size):
        path = self.library.image_path(item.hash_id)
        if path:
            try:
                path = Path(path)
                stat = path.stat()
                key = (str(path), stat.st_mtime_ns, stat.st_size, size)
                cached = self._image_cache.get(key)
                if cached is not None:
                    self._image_cache.move_to_end(key)
                    return cached
                if stat.st_size <= 20 * 1024 * 1024:
                    reader = QImageReader(str(path))
                    dimensions = reader.size()
                    if (bytes(reader.format()).lower() in (b"png", b"jpeg", b"jpg", b"webp")
                            and dimensions.width() > 0 and dimensions.height() > 0
                            and dimensions.width() * dimensions.height() <= 20_000_000):
                        reader.setAutoTransform(True)
                        reader.setScaledSize(dimensions.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
                        picture = reader.read()
                        if not picture.isNull():
                            pixmap = QPixmap.fromImage(picture)
                            self._image_cache[key] = pixmap
                            while len(self._image_cache) > self.cache_limit:
                                self._image_cache.popitem(last=False)
                            return pixmap
            except (OSError, ValueError):
                pass
        return self.provider.pixmap(item, size)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        hash_id = index.data(Qt.ItemDataRole.UserRole)
        item = self.by_hash.get(str(hash_id).removeprefix("0x").upper())
        if item is None:
            return
        option.icon = QIcon(self._pixmap(item, 24))
        option.decorationSize = QSize(24, 24)
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
