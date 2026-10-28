"""Virtualized catalogue cards built on Qt's model/view widgets.

Rows hold catalogue data only. Thumbnails are requested while painting visible
cards; there is no widget, texture or background task per catalogue entry.
"""
from __future__ import annotations

from collections import OrderedDict
import math
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImageReader, QPainter, QPen, QPixmap, QPolygonF, QTextLayout
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget


ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1
CARD_SIZE = QSize(176, 216)


def _category(item) -> str:
    categories = getattr(item, "categories", ())
    return " · ".join(categories) if categories else "Sin categoría"


class GalleryModel(QAbstractListModel):
    """A light model which exposes meaningful text to assistive technologies."""
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.items = []
        self.quantities = None
        self._rows = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == ITEM_ROLE:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            favorite = ", favorito" if self.library.is_favorite(item.hash_id) else ""
            quantity = self.quantity_text(item)
            return f"{item.name}, {_category(item)}{favorite}. {quantity} Identificador {item.hash_id}. Abre la ficha para revisar su imagen y sus acciones."
        if role == Qt.ItemDataRole.AccessibleDescriptionRole:
            return "Selecciona con las flechas y pulsa Intro para abrir la ficha. Las ilustraciones semánticas no muestran necesariamente el aspecto real del objeto."
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self._rows = {str(item.hash_id).upper(): row for row, item in enumerate(self.items)}
        self.endResetModel()

    def row_for_hash(self, hash_id):
        return self._rows.get(str(hash_id).removeprefix("0x").upper())

    def refresh(self):
        if self.items:
            self.dataChanged.emit(self.index(0), self.index(len(self.items) - 1), [])

    def quantity_text(self, item):
        if self.quantities is None:
            return ""
        value = self.quantities.get(int(item.hash_id, 16))
        return ("En partida: " + f"{value:,}".replace(",", ".")) if value is not None else "Sin registro en la lectura"


class _CardDelegate(QStyledItemDelegate):
    def __init__(self, icon_provider, library, parent=None):
        super().__init__(parent)
        self.icon_provider = icon_provider
        self.library = library
        self._image_cache = OrderedDict()
        self.cache_limit = 128

    def sizeHint(self, option, index):
        parent = self.parent()
        return parent.gridSize() if isinstance(parent, QListView) else CARD_SIZE

    def clear_cache(self):
        self._image_cache.clear()

    def _thumbnail(self, item, size):
        """Decode only bounded, scaled raster images supplied by UserLibrary."""
        path = self.library.image_path(item.hash_id)
        if path:
            try:
                path = Path(path)
                stat = path.stat()
                key = (str(path), stat.st_mtime_ns, stat.st_size, size)
                cached = self._image_cache.get(key)
                if cached is not None:
                    self._image_cache.move_to_end(key)
                    return cached, True
                if stat.st_size <= 20 * 1024 * 1024:
                    reader = QImageReader(str(path))
                    dimensions = reader.size()
                    image_format = bytes(reader.format()).lower()
                    if (image_format in (b"png", b"jpeg", b"jpg", b"webp") and
                            dimensions.width() > 0 and dimensions.height() > 0 and
                            dimensions.width() * dimensions.height() <= 20_000_000):
                        reader.setAutoTransform(True)
                        reader.setScaledSize(dimensions.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
                        image = reader.read()
                        if not image.isNull():
                            pixmap = QPixmap.fromImage(image)
                            self._image_cache[key] = pixmap
                            while len(self._image_cache) > self.cache_limit:
                                self._image_cache.popitem(last=False)
                            return pixmap, True
            except (OSError, ValueError):
                pass
        return self.icon_provider.pixmap(item, size), False

    @staticmethod
    def _star(painter, center, filled):
        points = []
        for point in range(10):
            angle = -math.pi / 2 + point * math.pi / 5
            radius = 7.5 if point % 2 == 0 else 3.4
            points.append(QPointF(center.x() + math.cos(angle) * radius,
                                 center.y() + math.sin(angle) * radius))
        painter.setPen(QPen(QColor("#e4c38a" if filled else "#8ca4b8"), 1.2))
        painter.setBrush(QColor("#e4c38a") if filled else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF(points))

    @staticmethod
    def _name(painter, text, rectangle, font):
        """Two lines with a real ellipsis on the last line, not clipped text."""
        painter.setFont(font)
        metrics = QFontMetrics(font)
        layout = QTextLayout(text, font)
        layout.beginLayout()
        first = layout.createLine()
        first.setLineWidth(rectangle.width())
        first.setPosition(QPointF(0, 0))
        second = layout.createLine()
        if second.isValid():
            second.setLineWidth(rectangle.width())
            second.setPosition(QPointF(0, metrics.height()))
        layout.endLayout()
        if first.isValid():
            first.draw(painter, rectangle.topLeft())
        if second.isValid():
            remainder = text[second.textStart():].strip()
            painter.drawText(QRectF(rectangle.left(), rectangle.top() + metrics.height(),
                                    rectangle.width(), metrics.height() + 2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(remainder, Qt.TextElideMode.ElideRight,
                                                int(rectangle.width())))

    def paint(self, painter: QPainter, option, index):
        item = index.data(ITEM_ROLE)
        if item is None:
            return
        painter.save()
        scale = option.rect.width() / CARD_SIZE.width()
        painter.translate(option.rect.topLeft())
        painter.scale(scale, scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        card = QRectF(0, 0, CARD_SIZE.width(), CARD_SIZE.height()).adjusted(4, 4, -5, -6)
        painter.setBrush(QColor("#213d49" if selected else "#1d3041" if hovered else "#182737"))
        painter.setPen(QPen(QColor("#74ddc5" if selected else "#7894ab" if hovered else "#344b5d"), 2 if selected else 1))
        painter.drawRoundedRect(card, 9, 9)
        if focused:
            painter.setPen(QPen(QColor("#d2fff3"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(card.adjusted(3, 3, -3, -3), 6, 6)

        art = QRectF(card.left() + 10, card.top() + 9, card.width() - 20, 105)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#101d2a"))
        painter.drawRoundedRect(art, 6, 6)
        pixmap, custom = self._thumbnail(item, max(112, round(112 * scale)))
        if not pixmap.isNull():
            size = pixmap.size().scaled(int(art.width() - 8), int(art.height() - 6), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(art.center().x() - size.width() / 2,
                            art.center().y() - size.height() / 2, size.width(), size.height())
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        favorite = self.library.is_favorite(item.hash_id)
        self._star(painter, QPointF(card.right() - 17, card.top() + 19), favorite)

        base = option.font.pixelSize()
        if base <= 0:
            base = max(12, round(option.font.pointSizeF() * 96 / 72))
        base = round(base / scale)
        title_font = QFont(option.font)
        title_font.setPixelSize(max(12, min(16, base)))
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setPen(QColor("#eff5f8"))
        name_box = QRectF(card.left() + 11, card.top() + 121, card.width() - 22, 40)
        self._name(painter, item.name, name_box, title_font)

        category_font = QFont(option.font)
        category_font.setPixelSize(max(10, min(12, base - 2)))
        painter.setFont(category_font)
        painter.setPen(QColor("#b4c5d3"))
        metrics = QFontMetrics(category_font)
        painter.drawText(QRectF(card.left() + 11, card.top() + 164, card.width() - 22, 17),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(index.model().quantity_text(item) or _category(item), Qt.TextElideMode.ElideRight, int(card.width() - 22)))

        badge_font = QFont(option.font)
        badge_font.setPixelSize(10)
        painter.setFont(badge_font)
        painter.setPen(QColor("#99d9ca" if custom else "#c5b28d"))
        painter.drawText(QRectF(card.left() + 11, card.bottom() - 19, card.width() - 22, 14),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "Tu imagen" if custom else "Ilustración semántica")
        painter.restore()


class GalleryView(QWidget):
    selection_changed = Signal(object)
    activated = Signal(object)

    def __init__(self, icon_provider, library, parent=None):
        super().__init__(parent)
        self.icon_provider, self.library = icon_provider, library
        self.setAccessibleName("Galería de objetos")
        self._resetting = False
        self.model = GalleryModel(library, self)
        self.view = QListView(self)
        self.view.setAccessibleName("Tarjetas de objetos del catálogo")
        self.view.setAccessibleDescription("Usa las flechas para seleccionar un objeto y pulsa Intro para abrir su ficha. Las miniaturas marcadas como ilustración son interpretaciones del objeto.")
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)
        self.view.setBatchSize(100)
        self.view.setUniformItemSizes(True)
        self.view.setGridSize(CARD_SIZE)
        self.view.setWrapping(True)
        self.view.setWordWrap(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.view.setMouseTracking(True)
        self.view.setStyleSheet("QListView { background: #111c29; border: 1px solid #344b5e; border-radius: 6px; outline: none; } QListView:focus { border: 2px solid #6de0c6; }")
        self.view.setModel(self.model)
        self.delegate = _CardDelegate(icon_provider, library, self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        self.view.activated.connect(self._activated)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def set_items(self, items):
        self._resetting = True
        self.model.set_items(items)
        self.view.clearSelection()
        self.view.setCurrentIndex(QModelIndex())
        self._resetting = False
        self.selection_changed.emit(None)

    def selected_item(self):
        index = self.view.currentIndex()
        return index.data(ITEM_ROLE) if index.isValid() else None

    def select_hash(self, hash_id):
        row = self.model.row_for_hash(hash_id)
        if row is None:
            self.view.clearSelection()
            self.view.setCurrentIndex(QModelIndex())
            return False
        index = self.model.index(row)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        return True

    def refresh_visuals(self):
        self.delegate.clear_cache()
        self.model.refresh()
        self.view.viewport().update()

    def set_quantities(self, quantities):
        self.model.quantities = dict(quantities) if quantities is not None else None
        self.model.refresh()
        self.view.viewport().update()

    def set_scale(self, scale):
        """Keep cards and their text readable alongside the app's text scaling."""
        scale = max(0.85, min(1.75, float(scale)))
        self.view.setGridSize(QSize(round(CARD_SIZE.width() * scale), round(CARD_SIZE.height() * scale)))
        self.view.scheduleDelayedItemsLayout()
        self.refresh_visuals()

    def count(self):
        return self.model.rowCount()

    def _current_changed(self, current, previous):
        if not self._resetting:
            self.selection_changed.emit(current.data(ITEM_ROLE) if current.isValid() else None)

    def _activated(self, index):
        item = index.data(ITEM_ROLE)
        if item is not None:
            self.activated.emit(item)
