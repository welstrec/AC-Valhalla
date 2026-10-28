"""Accessible Qt Widgets UI. All game operations run on a single worker."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, QSize
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QDialog, QSplitter,
    QScrollArea, QSpinBox, QStackedWidget, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__
from .catalog import Catalog
from .compatibility import executable_identity
from .engine import GameSession
from .memory import list_games, reap_quarantine
from .paths import user_dir
from .resources import ResourceAssistant, RATIONS_HASH
from .icons import IconProvider
from .library import UserLibrary, RequestQueue
from .gallery import GalleryView
from .requests import execute_requests
from .navigation import CatalogNavigator
from .list_view import RowArtworkDelegate
from .theme import apply_theme
from .quantities import QuantityPolicy, MAX_STACK_QUANTITY
from .branding import install_window_icon

LOG = logging.getLogger(__name__)


def control_icon(name, active=False):
    import qtawesome as qta
    return qta.icon("fa5s." + name, color="#e4bd7b" if active else "#bcd5e4", color_disabled="#6f8598")


class Worker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(int, int, str)

    def __init__(self):
        super().__init__()
        self.cancel = threading.Event()
        self.running = False

    def start(self, name, function):
        if self.running:
            raise RuntimeError("Espera a que termine la operación actual.")
        self.cancel.clear()
        self.running = True

        def execute():
            try:
                result = function()
            except Exception as exc:
                LOG.exception("Operation %s failed", name)
                self.failed.emit(name, str(exc) or type(exc).__name__)
            else:
                self.finished.emit(name, result)

        threading.Thread(target=execute, name="ValhallaOperation", daemon=True).start()


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text, callback, primary=False, accessible=None):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    widget.setAccessibleName(accessible or text.replace("&", ""))
    return widget


def table(columns, accessible):
    widget = QTableWidget(0, len(columns))
    widget.setHorizontalHeaderLabels(columns)
    widget.setAccessibleName(accessible)
    widget.setAlternatingRowColors(True)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.verticalHeader().setVisible(False)
    widget.verticalHeader().setDefaultSectionSize(34)
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setShowGrid(False)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, catalog=None, library=None):
        super().__init__()
        install_window_icon(self)
        self.catalog = catalog or Catalog.load()
        self.library = library if library is not None else UserLibrary()
        self.icon_provider = IconProvider(self.catalog)
        self.quantity_policy = QuantityPolicy(self.catalog)
        self.request_queue = RequestQueue(quantity_policy=self.quantity_policy)
        self.detail_item = None
        self.detail_pack = None
        self._sync_selection = False
        self.catalog_scope = None
        self.resource_assistant = ResourceAssistant(self.catalog)
        self.resource_statuses = []
        self.session = None
        self.process_fingerprint = None
        self.inventory = []
        self.inventory_loaded_at = None
        self.inventory_totals = {}
        self.inventory_identity = None
        self.display_items = []
        self.display_packs = []
        self.busy = False
        self.close_pending = False
        self.disconnect_pending = False
        self.last_result = None
        self.worker = Worker()
        self.worker.finished.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self._progress)
        self._callback = None
        self._size = 1.0
        self.setWindowTitle(f"Valhalla Companion · {__version__}")
        self.setAccessibleName("Valhalla Companion, editor de inventario de Assassin's Creed Valhalla")
        self.resize(1280, 860)
        self.setMinimumSize(960, 680)
        self._build()
        self._menus()
        self.refresh_processes()
        self._fill_items()
        self._fill_packs()
        self._refresh_controls()
        self._activity("Aplicación preparada. Elige el proceso del juego para comenzar.")
        for warning in self.library.warnings:
            self._activity(warning)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(3000)

    def _build(self):
        body = QWidget()
        body.setObjectName("body")
        self.setCentralWidget(body)
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20, 30, 20, 20)
        nav.addWidget(label("VALHALLA", "brand"))
        nav.addWidget(label("C O M P A N I O N", "eyebrow"))
        nav.addSpacing(30)
        self.nav_buttons = []
        self.pages = QStackedWidget()
        for index, text in enumerate(["Inicio", "Objetos y packs", "Mi inventario", "Ayudas del juego", "Compatibilidad", "Actividad"]):
            item = button(text, lambda checked=False, i=index: self.navigate(i))
            item.setObjectName("nav")
            item.setCheckable(True)
            item.setIcon(control_icon(["home", "th-large", "box-open", "magic", "check-circle", "history"][index]))
            nav.addWidget(item)
            self.nav_buttons.append(item)
        self.nav_buttons[0].setChecked(True)
        nav.addStretch()
        self.side_status = label("SIN CONEXIÓN", "eyebrow")
        nav.addWidget(self.side_status)
        nav.addWidget(label(f"Windows · 64 bits\nVersión {__version__} · validación inicial", "muted", True))
        layout.addWidget(sidebar)
        main = QWidget()
        content = QVBoxLayout(main)
        content.setContentsMargins(28, 24, 28, 18)
        content.setSpacing(16)
        self.heading = label("Tu partida, bajo control.", "title")
        content.addWidget(self.heading)
        self.subtitle = label("Conecta el juego, comprueba las funciones y elige qué cambiar.", "muted", True)
        content.addWidget(self.subtitle)
        connect = QHBoxLayout()
        process_label = label("&Proceso:")
        self.processes = QComboBox()
        self.processes.setAccessibleName("Proceso de Assassin's Creed Valhalla")
        self.processes.setMinimumWidth(100)
        self.processes.setMinimumContentsLength(10)
        self.processes.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        process_label.setBuddy(self.processes)
        connect.addWidget(process_label)
        connect.addWidget(self.processes, 1)
        self.refresh_button = button("&Buscar juego", self.refresh_processes)
        self.attach_button = button("&Adjuntar", self.attach_game, True)
        self.attach_button.setIcon(control_icon("link", True))
        self.detach_button = button("Desconectar", self.disconnect_game)
        connect.addWidget(self.refresh_button)
        connect.addWidget(self.attach_button)
        connect.addWidget(self.detach_button)
        content.addLayout(connect)
        self.connection_status = label("Abre el juego y carga una partida. Después pulsa Buscar juego.", "status", True)
        content.addWidget(self.connection_status)
        content.addWidget(self.pages, 1)
        self.pages.addWidget(self._home())
        self.pages.addWidget(self._catalog_page())
        self.pages.addWidget(self._inventory_page())
        self.pages.addWidget(self._helpers_page())
        self.pages.addWidget(self._compatibility_page())
        self.pages.addWidget(self._activity_page())
        progress = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Progreso de la operación")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        progress.addWidget(self.progress, 1)
        self.cancel_button = button("Cancelar operación", self.cancel_operation)
        progress.addWidget(self.cancel_button)
        content.addLayout(progress)
        layout.addWidget(main, 1)
        self.statusBar().showMessage("Preparado · Ninguna operación activa")

    def _home(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setMinimumHeight(480)
        self.home_content = page
        self.home_cards = []
        out = QVBoxLayout(page)
        out.setContentsMargins(0, 0, 0, 0)
        out.addWidget(label("Un flujo más sencillo", "sectionTitle"))
        out.addWidget(label("Cada función se comprueba por separado. Tú decides cuándo aplicarla.", "muted", True))
        cards = QHBoxLayout()
        for number, title, text in [
            ("01", "Conecta tu partida", "Abre Valhalla y carga una partida. Selecciona el proceso y pulsa Adjuntar."),
            ("02", "Elige lo que necesitas", "Busca un objeto, un pack o una ayuda. La aplicación indica qué funciones reconoce."),
            ("03", "Aplica y revisa", "Las tareas se ejecutan una por una. Consulta el resultado antes de guardar tu partida."),
        ]:
            card = QFrame()
            card.setObjectName("card")
            card.setMinimumHeight(210)
            card.setMaximumHeight(260)
            self.home_cards.append(card)
            box = QVBoxLayout(card)
            box.setContentsMargins(20, 20, 20, 22)
            box.addWidget(label(number, "stat"))
            box.addWidget(label(title, "sectionTitle", True))
            box.addWidget(label(text, "muted", True))
            box.addStretch()
            cards.addWidget(card, 1)
        out.addLayout(cards, 1)
        stats = QHBoxLayout()
        stats.addWidget(label(f"{len(self.catalog.items):,} objetos".replace(",", "."), "stat"))
        stats.addWidget(label(f"{len(self.catalog.packs)} packs", "stat"))
        stats.addWidget(label("Sin Cheat Engine", "muted"))
        out.addLayout(stats)
        out.addWidget(label("Primera edición: las firmas detectadas indican coincidencia de código; el comportamiento en tu compilación del juego necesita validación.", "notice", True))
        shortcuts = QHBoxLayout()
        shortcuts.addWidget(button("Explorar catálogo", lambda: self.navigate(1), True))
        shortcuts.addWidget(button("Ver compatibilidad", lambda: self.navigate(4)))
        shortcuts.addStretch()
        out.addLayout(shortcuts)
        scroll.setWidget(page)
        return scroll

    def _catalog_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        search_label = label("&Buscar:")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Nombre, categoría o identificador del objeto…")
        self.search.setAccessibleName("Buscar objetos y packs")
        search_label.setBuddy(self.search)
        filters.addWidget(search_label)
        filters.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.addItems(["Todos"] + list(self.catalog.categories))
        self.category.setAccessibleName("Filtrar categoría")
        self.category.setMaximumWidth(280)
        filters.addWidget(self.category)
        layout.addLayout(filters)
        options = QHBoxLayout()
        self.kind_filter = QComboBox()
        self.kind_filter.setAccessibleName("Tipo de objeto representado por su ilustración")
        self.kind_filter.addItem("Todos los tipos", None)
        kinds = {}
        for item in self.catalog.items:
            description = self.icon_provider.describe(item)
            kinds[description.kind] = description.label
        for kind, name in sorted(kinds.items(), key=lambda pair: pair[1]):
            self.kind_filter.addItem(name, kind)
        self.kind_filter.currentIndexChanged.connect(self._filter_catalog)
        options.addWidget(self.kind_filter)
        self.favorite_filter = button("Solo favoritos", self._filter_catalog)
        self.favorite_filter.setCheckable(True)
        self.favorite_filter.setIcon(control_icon("star"))
        self.favorite_filter.setToolTip("En Packs, muestra los que contienen algún objeto favorito.")
        options.addWidget(self.favorite_filter)
        options.addStretch()
        self.view_mode = QComboBox()
        self.view_mode.addItems(["Lista", "Tarjetas"])
        self.view_mode.setAccessibleName("Presentación del catálogo")
        options.addWidget(self.view_mode)
        self.details_toggle = button("Ver ficha", self._toggle_details)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setIcon(control_icon("info-circle"))
        options.addWidget(self.details_toggle)
        options.addWidget(button("Restablecer", self.reset_catalog))
        layout.addLayout(options)
        self.catalog_path = label("Todo el catálogo", "muted")
        layout.addWidget(self.catalog_path)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._filter_catalog)
        self.search.textChanged.connect(lambda: self.search_timer.start(180))
        self.category.currentIndexChanged.connect(self._filter_catalog)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.catalog_split = split
        self.navigator = CatalogNavigator(self.catalog, self.icon_provider)
        self.navigator.setMinimumWidth(200)
        self.navigator.setMaximumWidth(360)
        self.catalog_scope = self.navigator.current_scope()
        self.navigator.scope_changed.connect(self._scope_changed)
        split.addWidget(self.navigator)
        tabs = QTabWidget()
        self.gallery = GalleryView(self.icon_provider, self.library)
        self.item_views = QStackedWidget()
        self.item_table = table(["Objeto", "Tipo orientativo", "Identificador", "En partida", "Regla"], "Objetos disponibles en el catálogo")
        self.item_table.verticalHeader().setDefaultSectionSize(30)
        self.item_table.setIconSize(QSize(24, 24))
        self.item_artwork = RowArtworkDelegate(self.icon_provider, self.library, self.catalog.by_hash, self.item_table)
        self.item_table.setItemDelegateForColumn(0, self.item_artwork)
        self.item_table.horizontalHeader().setStretchLastSection(False)
        self.item_table.setColumnWidth(1, 115)
        self.item_table.setColumnWidth(2, 150)
        self.item_table.setColumnWidth(3, 115)
        self.item_table.setColumnWidth(4, 100)
        self.item_table.setColumnHidden(2, True)
        self.item_views.addWidget(self.item_table)
        self.item_views.addWidget(self.gallery)
        self.view_mode.currentIndexChanged.connect(self.item_views.setCurrentIndex)
        tabs.addTab(self.item_views, "Objetos")
        self.pack_table = table(["Pack", "Categoría", "Objetos", "Con registro"], "Packs de objetos del catálogo")
        self.pack_table.verticalHeader().setDefaultSectionSize(30)
        self.pack_table.horizontalHeader().setStretchLastSection(False)
        self.pack_table.setColumnWidth(1, 180)
        self.pack_table.setColumnWidth(2, 70)
        self.pack_table.setColumnWidth(3, 100)
        self.pack_table.itemDoubleClicked.connect(lambda *_: self.browse_selected_pack())
        tabs.addTab(self.pack_table, "Packs")
        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.addWidget(label("Prepara varios objetos y revisa las cantidades antes de enviarlos. Añadir el mismo objeto otra vez sustituye su cantidad; no la suma.", "muted", True))
        self.queue_table = table(["Objeto", "Cantidad a añadir", "Identificador", "En partida"], "Lista de solicitudes pendientes")
        queue_layout.addWidget(self.queue_table, 1)
        queue_actions = QHBoxLayout()
        self.queue_remove = button("Retirar selección", self.remove_queue_selection)
        self.queue_clear = button("Vaciar lista", self.clear_queue)
        queue_actions.addWidget(self.queue_remove)
        queue_actions.addWidget(self.queue_clear)
        queue_layout.addLayout(queue_actions)
        tabs.addTab(queue_page, "Solicitudes (0)")
        self.catalog_tabs = tabs
        self.item_table.itemDoubleClicked.connect(lambda *_: self.reveal_details())
        self.item_table.itemSelectionChanged.connect(self._item_selected)
        self.gallery.selection_changed.connect(self._gallery_selected)
        self.gallery.activated.connect(lambda item: self.show_large_image())
        self.pack_table.itemSelectionChanged.connect(self._pack_selected)
        tabs.currentChanged.connect(self._catalog_tab_changed)
        split.addWidget(tabs)
        self.detail_panel = self._detail_panel()
        split.addWidget(self.detail_panel)
        self.detail_panel.hide()
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([250, 730, 0])
        layout.addWidget(split, 1)
        counts = QHBoxLayout()
        self.catalog_count = label("", "muted", True)
        counts.addWidget(self.catalog_count, 1)
        self.show_quantities = QCheckBox("Mostrar cantidades")
        self.show_quantities.setAccessibleName("Mostrar cantidades leídas de la partida en el catálogo")
        self.show_quantities.setChecked(True)
        self.show_quantities.toggled.connect(self._toggle_quantity_display)
        counts.addWidget(self.show_quantities)
        self.catalog_refresh = button("Leer cantidades", self.load_inventory)
        self.catalog_refresh.setIcon(control_icon("sync-alt"))
        counts.addWidget(self.catalog_refresh)
        layout.addLayout(counts)
        controls = QHBoxLayout()
        controls.addWidget(label("Cantidad por objeto:"))
        self.grant_quantity = QSpinBox()
        self.grant_quantity.setRange(1, MAX_STACK_QUANTITY)
        self.grant_quantity.setGroupSeparatorShown(True)
        self.grant_quantity.setValue(1)
        self.grant_quantity.setAccessibleName("Cantidad que se solicitará de cada objeto")
        controls.addWidget(self.grant_quantity)
        controls.addWidget(self.favorite_button)
        controls.addWidget(self.queue_add_button)
        self.contents_button = button("Abrir pack", self.browse_selected_pack)
        controls.addWidget(self.contents_button)
        controls.addStretch()
        self.grant_button = button("Añadir a la partida", self.grant_selection, True)
        self.grant_button.setIcon(control_icon("plus", True))
        controls.addWidget(self.grant_button)
        layout.addLayout(controls)
        self.grant_hint = label("Conecta el juego para comprobar si puedes añadir objetos.", "muted", True)
        layout.addWidget(self.grant_hint)
        return page

    def _detail_panel(self):
        wrapper = QWidget()
        wrapper.setMinimumWidth(245)
        wrapper.setMaximumWidth(330)
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel = QFrame()
        panel.setObjectName("card")
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 16, 16, 16)
        self.detail_image = QLabel()
        self.detail_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_image.setMinimumHeight(128)
        self.detail_image.setAccessibleName("Vista previa del objeto seleccionado")
        box.addWidget(self.detail_image)
        self.detail_title = label("Selecciona un objeto", "sectionTitle", True)
        box.addWidget(self.detail_title)
        self.detail_category = label("Explora la galería o busca por nombre.", "muted", True)
        box.addWidget(self.detail_category)
        self.detail_source = label("Las ilustraciones se crean a partir del significado del nombre; no representan necesariamente su aspecto en el juego.", "muted", True)
        box.addWidget(self.detail_source)
        self.detail_state = label("", "muted", True)
        box.addWidget(self.detail_state)
        self.favorite_button = button("Favorito", self.toggle_favorite)
        self.favorite_button.setIcon(control_icon("star"))
        self.favorite_button.setAccessibleName("Guardar o quitar favorito")
        self.preview_button = button("Ampliar imagen", self.show_large_image)
        self.preview_button.setIcon(control_icon("search-plus"))
        box.addWidget(self.preview_button)
        self.pack_preview_button = button("Ver contenido del pack", self.show_pack_contents)
        box.addWidget(self.pack_preview_button)
        self.queue_add_button = button("Preparar", self.queue_selection)
        self.queue_add_button.setIcon(control_icon("list-ul"))
        self.queue_add_button.setAccessibleName("Añadir el objeto o pack a la lista de solicitudes")
        self.queue_add_button.setToolTip("Añadir a la lista con la cantidad indicada abajo")
        self.import_image_button = button("Elegir mi imagen…", self.import_item_image)
        self.import_image_button.setIcon(control_icon("image"))
        box.addWidget(self.import_image_button)
        self.reset_image_button = button("Usar ilustración", self.reset_item_image)
        box.addWidget(self.reset_image_button)
        box.addStretch()
        scroll.setWidget(panel)
        outer.addWidget(scroll, 1)
        return wrapper

    def _toggle_details(self, *args):
        visible = self.details_toggle.isChecked()
        self.detail_panel.setVisible(visible)
        self.details_toggle.setText("Ocultar ficha" if visible else "Ver ficha")
        if visible:
            self.catalog_split.setSizes([230, 460, 290])

    def reveal_details(self):
        self.details_toggle.setChecked(True)
        self._toggle_details()

    def _scope_changed(self, scope):
        self.catalog_scope = scope
        for widget in (self.search, self.category, self.kind_filter, self.favorite_filter):
            widget.blockSignals(True)
        self.search.clear()
        self.search_timer.stop()
        self.category.setCurrentIndex(0)
        self.kind_filter.setCurrentIndex(0)
        self.favorite_filter.setChecked(False)
        for widget in (self.search, self.category, self.kind_filter, self.favorite_filter):
            widget.blockSignals(False)
        self.catalog_path.setText(self.navigator.breadcrumb())
        self.catalog_path.setToolTip(self.navigator.breadcrumb())
        self.search.setPlaceholderText("Buscar dentro de «" + scope.title + "»…")
        self.catalog_tabs.setCurrentIndex(1 if scope.mode == "packs" else 0)
        self._filter_catalog()
        if scope.pack_id:
            pack = self.catalog.by_pack[scope.pack_id]
            self.grant_quantity.setValue(min(pack.quantity, self.grant_quantity.maximum()))
            self._show_detail(pack=pack)

    def reset_catalog(self):
        self.navigator.reset_scope()

    def browse_selected_pack(self):
        pack = self.detail_pack
        if pack:
            self.navigator.select_key("pack:" + pack.id)

    def _inventory_page(self):
        page = QWidget()
        out = QVBoxLayout(page)
        out.setContentsMargins(0, 0, 0, 0)
        line = QHBoxLayout()
        line.addWidget(label("Inventario de tu partida", "sectionTitle"), 1)
        self.inventory_refresh = button("Actualizar desde partida", self.load_inventory)
        line.addWidget(self.inventory_refresh)
        out.addLayout(line)
        out.addWidget(label("Se lee al abrir esta sección y después de añadir objetos. Cada cambio vuelve a comprobar el objeto y su ubicación.", "muted", True))
        self.inventory_table = table(["Objeto", "Cantidad", "Identificador"], "Inventario actual de la partida")
        self.inventory_table.itemSelectionChanged.connect(self._inventory_selection)
        self.inventory_tabs = QTabWidget()
        actual = QWidget()
        actual_layout = QVBoxLayout(actual)
        actual_layout.setContentsMargins(0, 0, 0, 0)
        actual_layout.addWidget(self.inventory_table, 1)
        row = QHBoxLayout()
        self.inventory_selected = label("Selecciona un objeto.", "muted", True)
        row.addWidget(self.inventory_selected, 1)
        quantity_label = label("&Nueva cantidad:")
        self.new_quantity = QSpinBox()
        self.new_quantity.setRange(0, MAX_STACK_QUANTITY)
        self.new_quantity.setGroupSeparatorShown(True)
        self.new_quantity.setAccessibleName("Nueva cantidad del objeto seleccionado")
        quantity_label.setBuddy(self.new_quantity)
        row.addWidget(quantity_label)
        row.addWidget(self.new_quantity)
        self.quantity_button = button("Aplicar cantidad", self.apply_quantity, True)
        row.addWidget(self.quantity_button)
        actual_layout.addLayout(row)
        actual_layout.addWidget(label("Cambiar a cero puede retirar el objeto de la partida. Comprueba el resultado antes de guardar.", "muted", True))
        self.inventory_tabs.addTab(actual, "Inventario actual")
        recovery = QWidget()
        recovery_layout = QVBoxLayout(recovery)
        recovery_layout.setContentsMargins(0, 0, 0, 0)
        recovery_layout.addWidget(label("Resolver recursos ausentes («??»)", "sectionTitle"))
        recovery_layout.addWidget(label("La reparación solicita una unidad de cada recurso básico que no tenga registro y vuelve a comprobarlo. No añade unidades a registros que ya existen ni desbloquea contenido o progreso pendiente.", "muted", True))
        self.resource_table = table(["Recurso", "Grupo", "Cantidad", "Estado", "Detalle"], "Estado de los recursos y explicación de valores ausentes")
        self.resource_table.setColumnWidth(1, 155)
        self.resource_table.setColumnWidth(2, 90)
        self.resource_table.setColumnWidth(3, 165)
        recovery_layout.addWidget(self.resource_table, 1)
        self.resource_hint = label("Actualiza el inventario para comprobar los recursos. Ausente no significa cantidad cero.", "notice", True)
        recovery_layout.addWidget(self.resource_hint)
        self.repair_button = button("Añadir 1 a cada recurso básico ausente y comprobar", self.repair_resources, True)
        recovery_layout.addWidget(self.repair_button)
        self.inventory_tabs.addTab(recovery, "Recursos ausentes")
        out.addWidget(self.inventory_tabs, 1)
        return page

    def _helpers_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        out = QVBoxLayout(page)
        out.setContentsMargins(0, 0, 6, 0)
        out.addWidget(label("Ayudas para tu partida", "sectionTitle"))
        out.addWidget(label("Las opciones temporales se restauran al desconectar. Los objetos y recompensas pueden quedar guardados en la partida.", "muted", True))
        grid = QGridLayout()
        self.helper_buttons = {}
        titles = {
            "persistence": ("Conservar objetos añadidos", "Aplica el parche temporal de persistencia al proceso del juego."),
            "horse": ("Velocidad del caballo", "Retira el límite indicado por la tabla original."),
            "drink": ("Desafío de bebida", "Activa la ayuda para completar el desafío."),
            "orlog": ("Partida de Orlog", "Activa la ayuda de victoria rápida."),
            "stealth": ("Sigilo", "Activa la ayuda de no detección."),
        }
        self.helper_reasons = {}
        for index, (key, (title, description)) in enumerate(titles.items()):
            frame = QFrame()
            frame.setObjectName("card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(17, 16, 17, 16)
            box.addWidget(label(title, "sectionTitle", True))
            box.addWidget(label(description, "muted", True))
            reason = label("Pendiente de comprobar", "muted", True)
            box.addWidget(reason)
            action = button("Activar", lambda checked=False, k=key: self.toggle_helper(k))
            action.setAccessibleName(f"Activar o desactivar {title}")
            box.addWidget(action)
            self.helper_buttons[key] = action
            self.helper_reasons[key] = reason
            grid.addWidget(frame, index // 2, index % 2)
        out.addLayout(grid)
        permanent = QHBoxLayout()
        permanent.addWidget(label("Multiplicador de plata y experiencia:"))
        self.multiplier = QDoubleSpinBox()
        self.multiplier.setRange(1, 6)
        self.multiplier.setSingleStep(0.5)
        self.multiplier.setValue(1.5)
        self.multiplier.setSuffix(" ×")
        self.multiplier.setAccessibleName("Multiplicador de plata y experiencia")
        permanent.addWidget(self.multiplier)
        self.boost_button = button("Aplicar multiplicador", self.apply_boost)
        permanent.addWidget(self.boost_button)
        out.addLayout(permanent)
        self.map_button = button("Solicitar revelado del mapa", self.reveal_map)
        out.addWidget(self.map_button)
        out.addWidget(label("Tiendas remotas y edición del objeto equipado: pendientes de una adaptación verificable. Puedes gestionar los objetos y sus cantidades desde las otras secciones.", "notice", True))
        out.addStretch()
        scroll.setWidget(page)
        return scroll

    def _compatibility_page(self):
        page = QWidget()
        out = QVBoxLayout(page)
        out.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Compatibilidad por función", "sectionTitle"), 1)
        self.diagnose_button = button("Volver a comprobar", self.diagnose_game)
        row.addWidget(self.diagnose_button)
        out.addLayout(row)
        out.addWidget(label("Una firma ausente o ambigua bloquea la función correspondiente. Reconocer una firma todavía no confirma su comportamiento dentro del juego.", "muted", True))
        self.feature_table = table(["Función", "Estado", "Detalle"], "Resultado de compatibilidad por función")
        self.feature_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.feature_table.setColumnWidth(1, 175)
        out.addWidget(self.feature_table, 1)
        self.process_details = QPlainTextEdit()
        self.process_details.setReadOnly(True)
        self.process_details.setMaximumHeight(150)
        self.process_details.setAccessibleName("Información del proceso y procedencia del catálogo")
        self.process_details.setPlainText("Adjunta un proceso para ver sus datos.\nFuente del catálogo: tabla comunitaria AC Valhalla Inventory Editor.\nEl nombre del archivo indica 1.6.2; sus comentarios declaran 1.5.3.\nLa comprobación se realiza sobre el código del proceso, función por función.")
        out.addWidget(self.process_details)
        return page

    def _activity_page(self):
        page = QWidget()
        out = QVBoxLayout(page)
        out.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("Actividad y resultados", "sectionTitle"), 1)
        row.addWidget(button("Guardar diagnóstico…", self.export_diagnostics))
        out.addLayout(row)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(1500)
        self.activity.setAccessibleName("Historial de operaciones y mensajes de error")
        out.addWidget(self.activity, 1)
        return page

    def _menus(self):
        menu = self.menuBar().addMenu("&Ver")
        for title, shortcut, callback in [
            ("Texto más grande", "Ctrl++", lambda: self._resize_text(0.1)),
            ("Texto más pequeño", "Ctrl+-", lambda: self._resize_text(-0.1)),
            ("Tamaño original", "Ctrl+0", lambda: self._resize_text(reset=True)),
            ("Buscar en catálogo", "Ctrl+F", self.focus_search),
        ]:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            menu.addAction(action)
        help_menu = self.menuBar().addMenu("A&yuda")
        action = QAction("Acerca de y créditos", self)
        action.triggered.connect(self.about)
        help_menu.addAction(action)

    def _resize_text(self, delta=0, reset=False):
        self._size = 1 if reset else max(0.9, min(1.5, self._size + delta))
        apply_theme(QApplication.instance(), self._size)
        self.home_content.setMinimumHeight(round(480 * self._size))
        for card in self.home_cards:
            card.setMinimumHeight(round(210 * self._size))
            card.setMaximumHeight(round(260 * self._size))
        self.gallery.set_scale(self._size)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        headings = [
            ("Tu partida, bajo control.", "Conecta el juego, comprueba las funciones y elige qué cambiar."),
            ("Explora tu colección.", "Encuentra objetos, revisa sus imágenes y prepara tus solicitudes."),
            ("El inventario de tu partida.", "Consulta cantidades y comprueba los recursos ausentes."),
            ("Prepara tu próxima aventura.", "Elige las ayudas disponibles para la partida conectada."),
            ("Comprueba la conexión.", "Revisa qué funciones están disponibles para tu versión del juego."),
            ("Cada acción, a la vista.", "Consulta los resultados de las operaciones y guarda un diagnóstico."),
        ]
        self.heading.setText(headings[index][0])
        self.subtitle.setText(headings[index][1])
        self.heading.setVisible(index != 1)
        self.subtitle.setVisible(index != 1)
        for i, item in enumerate(self.nav_buttons):
            item.setChecked(i == index)
        if (index == 2 or (index == 1 and self.show_quantities.isChecked())) and not self._snapshot_available() and self._available("inventory"):
            self.load_inventory()

    def focus_search(self):
        self.navigate(1)
        self.search.setFocus()

    def _fill_items(self):
        self.display_items = self.catalog.search(self.search.text(), self.category.currentText())
        if self.catalog_scope and self.catalog_scope.hashes is not None:
            self.display_items = [item for item in self.display_items if item.hash_id in self.catalog_scope.hashes]
        kind = self.kind_filter.currentData()
        if kind:
            self.display_items = [item for item in self.display_items if self.icon_provider.describe(item).kind == kind]
        if self.favorite_filter.isChecked():
            self.display_items = [item for item in self.display_items if self.library.is_favorite(item.hash_id)]
        self._display_by_hash = {item.hash_id: i for i, item in enumerate(self.display_items)}
        self._sync_selection = True
        self.item_table.setUpdatesEnabled(False)
        self.item_table.clearSelection()
        self.item_table.setCurrentCell(-1, -1)
        self.item_table.setRowCount(len(self.display_items))
        for row, item in enumerate(self.display_items):
            name = ("★ " if self.library.is_favorite(item.hash_id) else "") + item.name
            for column, text in enumerate([name, self.icon_provider.describe(item).label, item.hash_id, self._quantity_text(item.hash_id), self._quantity_rule_text(item.hash_id)]):
                cell = QTableWidgetItem(str(text))
                cell.setToolTip(f"{item.name}\n{' · '.join(item.categories)}\n{item.hash_id}\nIlustración semántica; abre la ficha para consultar o cambiar la imagen.")
                if column == 4:
                    cell.setToolTip(self.quantity_policy.rule(item.hash_id).reason)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.hash_id)
                self.item_table.setItem(row, column, cell)
        self.item_table.setUpdatesEnabled(True)
        self.gallery.set_items(self.display_items)
        self._update_gallery_quantities()
        self._sync_selection = False
        if self.catalog_tabs.currentIndex() == 0:
            self._show_detail()
        self._catalog_counts()

    def _fill_packs(self):
        import unicodedata
        def normalized(text):
            return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
        query = normalized(self.search.text().strip())
        category = self.category.currentText()
        terms = query.split()
        self.display_packs = [p for p in self.catalog.packs if (category == "Todos" or p.category == category) and all(term in normalized(p.name + " " + p.category + " " + " ".join(self.catalog.pack_paths[p.id])) for term in terms)]
        if self.catalog_scope and self.catalog_scope.pack_ids is not None:
            self.display_packs = [pack for pack in self.display_packs if pack.id in self.catalog_scope.pack_ids]
        if self.favorite_filter.isChecked():
            self.display_packs = [p for p in self.display_packs if any(self.library.is_favorite(h) for h in p.hashes)]
        kind = self.kind_filter.currentData()
        if kind:
            self.display_packs = [p for p in self.display_packs if any(self.icon_provider.describe(self.catalog.by_hash[h]).kind == kind for h in p.hashes)]
        self.pack_table.setUpdatesEnabled(False)
        self.pack_table.clearSelection()
        self.pack_table.setCurrentCell(-1, -1)
        self.pack_table.setRowCount(len(self.display_packs))
        for row, pack in enumerate(self.display_packs):
            for column, text in enumerate([pack.name, pack.category, len(pack.hashes), self._pack_quantity_text(pack)]):
                cell = QTableWidgetItem(str(text))
                cell.setToolTip(f"{pack.name}\n{' / '.join(self.catalog.pack_paths[pack.id])}\nDoble clic para abrir sus {len(pack.hashes)} objetos.")
                if column == 0:
                    cell.setIcon(control_icon("box-open"))
                self.pack_table.setItem(row, column, cell)
        self.pack_table.setUpdatesEnabled(True)
        self._catalog_counts()

    def _item_selected(self):
        if self._sync_selection:
            return
        row = self.item_table.currentRow()
        item = self.display_items[row] if 0 <= row < len(self.display_items) else None
        self._sync_selection = True
        if item:
            self.gallery.select_hash(item.hash_id)
        self._sync_selection = False
        if self.catalog_tabs.currentIndex() == 0:
            self._show_detail(item=item)
        self._refresh_controls()

    def _gallery_selected(self, item):
        if self._sync_selection:
            return
        self._sync_selection = True
        if item and item.hash_id in self._display_by_hash:
            self.item_table.selectRow(self._display_by_hash[item.hash_id])
        else:
            self.item_table.clearSelection()
            self.item_table.setCurrentCell(-1, -1)
        self._sync_selection = False
        if self.catalog_tabs.currentIndex() == 0:
            self._show_detail(item=item)
        self._refresh_controls()

    def _pack_selected(self):
        self._pack_selection()
        row = self.pack_table.currentRow()
        pack = self.display_packs[row] if 0 <= row < len(self.display_packs) else None
        if self.catalog_tabs.currentIndex() == 1:
            self._show_detail(pack=pack)

    def _catalog_tab_changed(self, index):
        if index == 0:
            self._item_selected()
        elif index == 1:
            self._pack_selected()
        else:
            self._show_detail()
            self.detail_title.setText("Tu lista de solicitudes")
            self.detail_category.setText("Las cantidades se aplican por objeto. Revisa la lista antes de ejecutarla.")
        self._refresh_controls()

    def _item_pixmap(self, item, size=160):
        path = self.library.image_path(item.hash_id)
        if path:
            picture = QPixmap(str(path))
            if not picture.isNull():
                return picture.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return self.icon_provider.pixmap(item, size)

    def _show_detail(self, item=None, pack=None):
        self.detail_item, self.detail_pack = item, pack
        hashes = [item.hash_id] if item else (list(pack.hashes) if pack else [])
        if hashes:
            self.grant_quantity.setMaximum(self.quantity_policy.limit(item.hash_id) if item else self.quantity_policy.limit_for(hashes))
            self.grant_quantity.setToolTip(self.quantity_policy.rule(hashes[0]).reason if item else "Cada pieza de equipo se limita a una unidad; los recursos mantienen la cantidad indicada.")
        else:
            self.grant_quantity.setMaximum(MAX_STACK_QUANTITY)
        if item:
            self.detail_image.setPixmap(self._item_pixmap(item))
            self.detail_title.setText(item.name)
            description = self.icon_provider.describe(item)
            self.detail_category.setText(" · ".join(item.categories) + "\n" + description.label)
            self.detail_source.setText("Imagen importada por ti." if self.library.image_path(item.hash_id) else "Ilustración semántica original. Su aspecto en el juego puede ser diferente.")
            current = self._known_quantity(item.hash_id)
            state = f"Última lectura: {self._format_quantity(current)} unidades." if current is not None else ("Sin registro en la última lectura." if self._snapshot_available() else "Conecta y lee el inventario para consultar su cantidad.")
            rule = self.quantity_policy.rule(item.hash_id)
            if rule.unique:
                state += " Equipo protegido: una unidad; si ya está presente se omite."
                if current is not None and current > 1:
                    state += " Hay varias unidades en la lectura; no se añadirán más."
            self.detail_state.setText(state)
            self.favorite_button.setText("Guardado" if self.library.is_favorite(item.hash_id) else "Favorito")
            self.favorite_button.setIcon(control_icon("star", self.library.is_favorite(item.hash_id)))
        elif pack:
            self.detail_image.setPixmap(self.icon_provider.pack_pixmap(pack, self.catalog, 160))
            self.detail_title.setText(pack.name)
            self.detail_category.setText(f"{pack.category}\n{len(pack.hashes)} objetos · cantidad predeterminada: {pack.quantity}")
            self.detail_source.setText("Ilustración del pack. Abre su contenido para revisar cada objeto.")
            self.detail_state.setText("Puedes prepararlo en la lista de solicitudes antes de conectar el juego.")
        else:
            self.detail_image.clear()
            self.detail_title.setText("Selecciona un objeto")
            self.detail_category.setText("Explora las tarjetas, los packs o utiliza el buscador.")
            self.detail_source.setText("Los iconos inventados se identifican como ilustraciones; puedes sustituirlos por tus imágenes.")
            self.detail_state.setText("")
        self._refresh_controls()

    def toggle_favorite(self):
        item = self.detail_item
        if not item:
            return
        try:
            self.library.toggle_favorite(item.hash_id)
            if self.favorite_filter.isChecked():
                self._filter_catalog()
            else:
                self.gallery.refresh_visuals()
                row = self._display_by_hash.get(item.hash_id)
                if row is not None:
                    self.item_table.item(row, 0).setText(("★ " if self.library.is_favorite(item.hash_id) else "") + item.name)
                self._show_detail(item=item)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "No se pudo guardar el favorito", str(exc))

    def import_item_image(self):
        item = self.detail_item
        if not item:
            return
        source, _ = QFileDialog.getOpenFileName(self, "Elegir imagen del objeto", "", "Imágenes (*.png *.jpg *.jpeg *.webp)")
        if not source:
            return
        try:
            self.library.set_image(item.hash_id, Path(source))
            self.gallery.refresh_visuals()
            self.item_artwork.clear_cache()
            self.item_table.viewport().update()
            self._show_detail(item=item)
            self._activity("Imagen local guardada para " + item.name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "No se pudo importar la imagen", str(exc))

    def reset_item_image(self):
        if self.detail_item:
            item = self.detail_item
            try:
                self.library.clear_image(item.hash_id)
                self.gallery.refresh_visuals()
                self.item_artwork.clear_cache()
                self.item_table.viewport().update()
                self._show_detail(item=item)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "No se pudo restablecer la ilustración", str(exc))

    def show_large_image(self):
        item, pack = self.detail_item, self.detail_pack
        if not item and not pack:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(item.name if item else pack.name)
        dialog.resize(580, 650)
        layout = QVBoxLayout(dialog)
        title = label(item.name if item else pack.name, "sectionTitle", True)
        layout.addWidget(title)
        picture = QLabel()
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picture.setAccessibleName("Imagen ampliada del objeto o pack")
        picture.setPixmap(self._item_pixmap(item, 480) if item else self.icon_provider.pack_pixmap(pack, self.catalog, 480))
        layout.addWidget(picture, 1)
        layout.addWidget(label(self.detail_source.text(), "muted", True))
        layout.addWidget(button("Cerrar", dialog.accept))
        dialog.exec()

    def show_pack_contents(self):
        pack = self.detail_pack
        if not pack:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Contenido · " + pack.name)
        dialog.resize(850, 660)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(pack.name, "sectionTitle", True))
        layout.addWidget(label(f"{len(pack.hashes)} objetos. Abre una tarjeta para ver ese objeto en el catálogo.", "muted", True))
        preview = GalleryView(self.icon_provider, self.library)
        preview.set_items([self.catalog.by_hash[h] for h in pack.hashes])
        layout.addWidget(preview, 1)
        def open_item(item):
            dialog.accept()
            self.navigator.reset_scope()
            self.favorite_filter.setChecked(False)
            self.category.setCurrentIndex(0)
            self.kind_filter.setCurrentIndex(0)
            self.search.setText(item.hash_id)
            self.search_timer.stop()
            self.catalog_tabs.setCurrentIndex(0)
            self._filter_catalog()
            self.gallery.select_hash(item.hash_id)
        preview.activated.connect(open_item)
        layout.addWidget(button("Cerrar vista previa", dialog.accept))
        dialog.exec()

    def queue_selection(self):
        if self.busy:
            return
        if self.detail_item:
            if self.quantity_policy.is_unique(self.detail_item.hash_id) and (self._known_quantity(self.detail_item.hash_id) or 0) > 0:
                self._activity("Este equipo ya figura en la última lectura; no se preparó una copia.")
                return
            hashes, name = [self.detail_item.hash_id], self.detail_item.name
        elif self.detail_pack:
            hashes, name = list(self.detail_pack.hashes), self.detail_pack.name
        else:
            return
        try:
            self.request_queue.add(hashes, self.grant_quantity.value(), name)
            self._fill_queue()
            self._activity(f"Lista preparada: {self.request_queue.count} objetos distintos.")
        except ValueError as exc:
            QMessageBox.warning(self, "No se pudo añadir a la lista", str(exc))

    def _fill_queue(self):
        entries = self.request_queue.entries
        self.queue_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            item = self.catalog.by_hash.get(entry.hash_id)
            for column, text in enumerate([item.name if item else entry.label, entry.quantity, entry.hash_id, self._quantity_text(entry.hash_id)]):
                self.queue_table.setItem(row, column, QTableWidgetItem(str(text)))
        self.catalog_tabs.setTabText(2, f"Solicitudes ({len(entries)})")
        self._refresh_controls()

    def remove_queue_selection(self):
        row = self.queue_table.currentRow()
        if not self.busy and 0 <= row < self.request_queue.count:
            self.request_queue.remove(self.request_queue.entries[row].hash_id)
            self._fill_queue()

    def clear_queue(self):
        if not self.busy:
            self.request_queue.clear()
            self._fill_queue()

    def execute_queue(self):
        if not self._available("grant") or not self.request_queue.count:
            return
        entries = self.request_queue.entries
        summary = "\n".join(f"{self.catalog.by_hash[e.hash_id].name}: {e.quantity}" for e in entries[:8])
        if len(entries) > 8:
            summary += f"\n… y {len(entries) - 8} objetos más."
        if QMessageBox.question(self, "Revisar lista de solicitudes", f"Se solicitarán {len(entries)} objetos distintos:\n\n{summary}\n\n¿Enviar esta lista a la partida conectada?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        def done(result):
            for hash_id in result.attempted:
                self.request_queue.remove(hash_id)
            self._fill_queue()
            if result.inventory is not None:
                self._inventory_loaded(result.inventory)
            else:
                self._clear_inventory()
            self._activity(f"Lista: {result.completed}/{result.requested} solicitudes completadas; {result.confirmed} aumentos verificados." + (" Cancelada entre objetos." if result.cancelled else ""))
            if getattr(result, "skipped", 0):
                self._activity(f"Equipo ya presente: {result.skipped} objetos omitidos.")
            for warning in result.warnings:
                self._activity(warning)
            if result.error:
                self._activity(result.error)
                QMessageBox.warning(self, "Se detuvo la lista", result.error)
        self._run("Ejecutar lista de solicitudes", lambda: execute_requests(self.session, entries, self.worker.cancel, self.worker.progress.emit), done)

    def _catalog_counts(self):
        if hasattr(self, "catalog_count"):
            status = ("Lectura " + self.inventory_loaded_at.strftime("%H:%M:%S")) if self._snapshot_available() else "Sin lectura de la partida"
            self.catalog_count.setText(f"{len(self.display_items)} objetos · {len(self.display_packs)} packs · {status}")

    @staticmethod
    def _format_quantity(value):
        return f"{value:,}".replace(",", ".")

    def _snapshot_available(self):
        return bool(self.inventory_loaded_at is not None and self.session is not None and
                    not getattr(self.session, "tainted", False) and
                    self.inventory_identity == self.session.identity)

    def _known_quantity(self, hash_id):
        return self.inventory_totals.get(int(str(hash_id), 16) if isinstance(hash_id, str) else hash_id) if self._snapshot_available() else None

    def _quantity_text(self, hash_id):
        if not self._snapshot_available():
            return "Sin lectura"
        value = self._known_quantity(hash_id)
        return self._format_quantity(value) if value is not None else "Sin registro"

    def _quantity_rule_text(self, hash_id):
        rule = self.quantity_policy.rule(hash_id)
        if rule.limit_verified:
            return "Máx. " + self._format_quantity(rule.maximum)
        return "1 unidad" if rule.unique else ("Acumulable" if rule.label == "Recurso o consumible" else "Sin comprobar")

    def _pack_quantity_text(self, pack):
        if not self._snapshot_available():
            return "Sin lectura"
        present = sum(int(hash_id, 16) in self.inventory_totals for hash_id in pack.hashes)
        return f"{present}/{len(pack.hashes)}"

    def _update_gallery_quantities(self):
        if hasattr(self, "gallery"):
            totals = self.inventory_totals if self._snapshot_available() and self.show_quantities.isChecked() else None
            self.gallery.set_quantities(totals)

    def _update_catalog_quantities(self):
        if not hasattr(self, "show_quantities"):
            return
        visible = self.show_quantities.isChecked()
        self.item_table.setColumnHidden(3, not visible)
        self.pack_table.setColumnHidden(3, not visible)
        self.queue_table.setColumnHidden(3, not visible)
        for row, item in enumerate(self.display_items):
            self.item_table.setItem(row, 3, QTableWidgetItem(self._quantity_text(item.hash_id)))
        for row, pack in enumerate(self.display_packs):
            self.pack_table.setItem(row, 3, QTableWidgetItem(self._pack_quantity_text(pack)))
        for row, entry in enumerate(self.request_queue.entries):
            self.queue_table.setItem(row, 3, QTableWidgetItem(self._quantity_text(entry.hash_id)))
        self._update_gallery_quantities()
        self._catalog_counts()
        self._refresh_controls()

    def _toggle_quantity_display(self, enabled):
        self._update_catalog_quantities()
        if enabled and self._available("inventory"):
            self.load_inventory()

    def _filter_catalog(self, *args):
        self._fill_items()
        self._fill_packs()
        self._refresh_controls()

    def _activity(self, text):
        if hasattr(self, "activity"):
            self.activity.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")
        LOG.info(text)
        self.statusBar().showMessage(text)

    def refresh_processes(self):
        if self.busy:
            return
        previous = self.processes.currentData()
        self.processes.clear()
        try:
            found = list_games()
            for item in found:
                self.processes.addItem(f"{item.name}  ·  PID {item.pid}", item.pid)
            if not found:
                self.processes.addItem("No se encontró un proceso de Valhalla", None)
            elif previous:
                index = self.processes.findData(previous)
                if index >= 0:
                    self.processes.setCurrentIndex(index)
        except Exception as exc:
            self.processes.addItem("No se pudo consultar el juego", None)
            self._activity(str(exc))
        self._refresh_controls()

    def _available(self, key):
        if not self.session or self.busy or getattr(self.session, "tainted", False):
            return False
        if key == "grant" and getattr(self.session, "require_quantity_snapshot", False):
            inventory = self.session.features.get("inventory")
            if not inventory or not inventory.available:
                return False
        feature = self.session.features.get(key)
        return bool(feature and feature.available)

    def _refresh_controls(self, *args):
        if not hasattr(self, "cancel_button"):
            return
        self.attach_button.setEnabled(not self.busy and self.session is None and self.processes.currentData() is not None)
        self.refresh_button.setEnabled(not self.busy and self.session is None)
        self.processes.setEnabled(not self.busy and self.session is None)
        self.detach_button.setEnabled(self.session is not None and not self.busy)
        self.cancel_button.setEnabled(self.busy)
        self.progress.setVisible(self.busy)
        self.cancel_button.setVisible(self.busy)
        tab = self.catalog_tabs.currentIndex()
        selected = (self.detail_item is not None or self.detail_pack is not None) if tab == 0 else (self.pack_table.currentRow() >= 0 if tab == 1 else self.request_queue.count > 0)
        self.grant_button.setEnabled(self._available("grant") and selected)
        self.grant_button.setText("Ejecutar lista" if tab == 2 else "Añadir a la partida")
        self.grant_quantity.setEnabled(not self.busy and tab != 2)
        self.favorite_button.setEnabled(self.detail_item is not None and not self.busy)
        self.import_image_button.setEnabled(self.detail_item is not None and not self.busy)
        self.reset_image_button.setEnabled(self.detail_item is not None and not self.busy and self.library.image_path(self.detail_item.hash_id) is not None)
        self.preview_button.setEnabled(self.detail_item is not None or self.detail_pack is not None)
        self.pack_preview_button.setVisible(self.detail_pack is not None)
        self.queue_add_button.setEnabled(not self.busy and (self.detail_item is not None or self.detail_pack is not None))
        owned_equipment = bool(self.detail_item and self.quantity_policy.is_unique(self.detail_item.hash_id) and
                               (self._known_quantity(self.detail_item.hash_id) or 0) > 0)
        if owned_equipment:
            self.grant_button.setEnabled(False)
            self.queue_add_button.setEnabled(False)
        self.queue_add_button.setText("Preparar pack" if self.detail_pack else "Preparar")
        self.contents_button.setVisible(tab == 1)
        self.contents_button.setEnabled(self.detail_pack is not None)
        self.queue_remove.setEnabled(not self.busy and self.request_queue.count > 0)
        self.queue_clear.setEnabled(not self.busy and self.request_queue.count > 0)
        self.inventory_refresh.setEnabled(self._available("inventory"))
        self.catalog_refresh.setEnabled(self._available("inventory") and self.show_quantities.isChecked())
        current_row = self.inventory_table.currentRow()
        editable = 0 <= current_row < len(self.inventory) and self.inventory[current_row].hash_id != RATIONS_HASH
        self.quantity_button.setEnabled(self._available("inventory") and editable)
        self.repair_button.setEnabled(self._available("inventory") and self._available("grant"))
        self.diagnose_button.setEnabled(self.session is not None and not self.busy and not getattr(self.session, "tainted", False))
        self.boost_button.setEnabled(self._available("boost"))
        self.map_button.setEnabled(self._available("map"))
        for key, widget in self.helper_buttons.items():
            widget.setEnabled(self._available(key))
            state = self.session.features.get(key) if self.session else None
            widget.setText("Desactivar" if state and state.enabled else "Activar")
            self.helper_reasons[key].setText((state.reason or "Firma detectada · validación en partida pendiente") if state else "Conecta el juego para comprobar esta función.")
        if self.session:
            feature = self.session.features.get("grant")
            inventory = self.session.features.get("inventory")
            if getattr(self.session, "require_quantity_snapshot", False) and not (inventory and inventory.available):
                self.grant_hint.setText("Se necesita una lectura compatible del inventario para comprobar cantidades antes de añadir objetos.")
            elif owned_equipment:
                self.grant_hint.setText("Este equipo ya figura en tu inventario. No se añadirán copias; pulsa Leer cantidades para actualizar.")
            elif self.detail_item and self.quantity_policy.is_unique(self.detail_item.hash_id):
                self.grant_hint.setText("Equipo protegido: una unidad. Antes de enviarlo se vuelve a comprobar si ya está presente.")
            else:
                self.grant_hint.setText("En packs, el equipo recibe una unidad y se omite si ya está presente; los recursos mantienen la cantidad indicada." if feature and feature.available else (feature.reason if feature else "Función pendiente de comprobación."))

    def _run(self, name, operation, callback=None):
        if self.busy:
            return
        self.busy = True
        self._callback = callback
        self.progress.setRange(0, 0)
        self._activity(name + "…")
        self._refresh_controls()
        self.worker.start(name, operation)

    def _progress(self, done, total, text):
        item = self.catalog.by_hash.get(text)
        if item:
            text = "Solicitando " + item.name
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.progress.setFormat(text + (" · %p%" if total else ""))
        self.statusBar().showMessage(text)

    def _settled(self, success=True):
        self.worker.running = False
        self.busy = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if success else 0)
        self.progress.setFormat("Preparado" if success else "Operación no completada")
        self._refresh_controls()
        self._fill_features()
        if self.disconnect_pending:
            self.disconnect_pending = False
            QTimer.singleShot(0, self.disconnect_game)
        elif self.close_pending:
            QTimer.singleShot(0, self.close)

    def _finished(self, name, result):
        callback = self._callback
        self._callback = None
        self.last_result = result
        self._settled()
        try:
            if callback:
                callback(result)
            else:
                self._activity(str(result) if isinstance(result, str) else name + ": operación completada.")
        except Exception as exc:
            LOG.exception("UI result failed")
            self._activity("No se pudo presentar el resultado: " + str(exc))
        self._refresh_controls()

    def _failed(self, name, text):
        self._callback = None
        if name in {"Leer inventario", "Comprobar objeto y aplicar cantidad", "Añadir objetos y actualizar inventario", "Ejecutar lista de solicitudes"}:
            self._clear_inventory()
        if self.close_pending and name == "Restaurar ayudas y desconectar":
            self.close_pending = False
        self._settled(False)
        self._activity(f"{name}: {text}")
        if self.session and getattr(getattr(self.session, "memory", None), "tainted", False):
            self._clear_inventory()
            self.grant_hint.setText("La operación no terminó a tiempo. La sesión queda bloqueada hasta reiniciar el juego.")
        if not self.close_pending:
            QMessageBox.warning(self, "No se pudo completar la operación", text)

    def attach_game(self):
        pid = self.processes.currentData()
        if pid is None or self.session:
            return
        def connect():
            session = GameSession(pid=pid, names={int(i.hash_id, 16): i.name for i in self.catalog.items})
            session.quantity_policy = self.quantity_policy
            session.require_quantity_snapshot = True
            try:
                session.diagnose(cancel=self.worker.cancel, progress=self.worker.progress.emit)
                session.executable_identity = executable_identity(session.memory.path, self.worker.cancel)
            except Exception:
                session.close()
                raise
            return session
        def done(session):
            self.session = session
            self.side_status.setText("JUEGO CONECTADO")
            memory = session.memory
            self.process_fingerprint = getattr(session, "executable_identity", {})
            self.connection_status.setText(f"Conectado a {memory.name} · PID {memory.pid}. Revisa las funciones detectadas en Compatibilidad.")
            fingerprint = self.process_fingerprint
            self.process_details.setPlainText(f"Proceso: {memory.name}\nPID: {memory.pid}\nRuta: {memory.path}\nVersión declarada: {fingerprint.get('version', 'No declarada')}\nSHA-256: {fingerprint.get('sha256') or 'No disponible'}\nPerfil: firmas de la tabla fuente; validación en partida pendiente.")
            self._fill_features()
            self._activity("Conexión establecida. Ninguna ayuda se ha activado automáticamente.")
            if self.show_quantities.isChecked():
                self.load_inventory()
        self._run("Conectar y comprobar funciones", connect, done)

    def disconnect_game(self):
        if not self.session:
            return
        if self.busy:
            self.worker.cancel.set()
            self.disconnect_pending = True
            return
        current = self.session
        def done(result):
            self.session = None
            self._clear_inventory()
            self.side_status.setText("SIN CONEXIÓN")
            self.connection_status.setText("Desconectado. Puedes volver a adjuntar el juego.")
            self._fill_features()
            if isinstance(result, dict) and (result.get("pending") or result.get("tainted")):
                self._activity("Desconectado. Quedó una operación sin confirmar; reinicia el juego antes de volver a conectarlo.")
            else:
                self._activity("Desconectado. Se restauraron las ayudas temporales activadas.")
        self._run("Restaurar ayudas y desconectar", current.close, done)

    def diagnose_game(self):
        if self.session:
            self._run("Comprobar compatibilidad", lambda: self.session.diagnose(cancel=self.worker.cancel, progress=self.worker.progress.emit), lambda _: self._activity("Comprobación terminada. Consulta el detalle por función."))

    def _fill_features(self):
        if not hasattr(self, "feature_table"):
            return
        features = list(self.session.features.values()) if self.session else []
        self.feature_table.setRowCount(len(features))
        for row, feature in enumerate(features):
            status = "Activa" if feature.enabled else ("Firma detectada" if feature.available else "No disponible")
            for column, text in enumerate([feature.title, status, feature.reason or "Validación en partida pendiente"]):
                self.feature_table.setItem(row, column, QTableWidgetItem(text))

    def grant_selection(self):
        if self.catalog_tabs.currentIndex() == 2:
            self.execute_queue()
            return
        if not self._available("grant"):
            return
        if self.catalog_tabs.currentIndex() == 0:
            row = self.item_table.currentRow()
            if row < 0:
                if self.detail_pack is None:
                    return
                selected = self.detail_pack
                hashes, title = list(selected.hashes), selected.name
            else:
                selected = self.display_items[row]
                hashes, title = [selected.hash_id], selected.name
        else:
            row = self.pack_table.currentRow()
            if row < 0:
                return
            selected = self.display_packs[row]
            hashes, title = list(selected.hashes), selected.name
        quantity = self.grant_quantity.value()
        terms = "El equipo identificado recibe una unidad y se omite si ya está presente. Los recursos u objetos sin esa protección reciben la cantidad indicada."
        answer = QMessageBox.question(self, "Revisar solicitud", f"{title}\n\nSe revisarán {len(hashes)} objetos distintos. Cantidad seleccionada: {self._format_quantity(quantity)}.\n{terms}\n\n¿Aplicar a la partida conectada?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        def done(result):
            grant, items = result
            if items is not None:
                self._inventory_loaded(items)
            else:
                self._clear_inventory()
            result = grant
            self._activity(f"Objetos: {result.completed}/{result.requested} solicitudes completadas; {result.confirmed} aumentos de cantidad verificados." + (" Cancelada entre objetos." if result.cancelled else " Revisa los desbloqueos en la partida."))
            if getattr(result, "skipped", 0):
                self._activity(f"Equipo ya presente: {result.skipped} objetos omitidos.")
            for warning in result.warnings:
                self._activity(warning)
        def add_and_refresh():
            result = self.session.grant_items(hashes, quantity, cancel=self.worker.cancel, progress=self.worker.progress.emit)
            items = None
            if self.session.features.get("inventory") and self.session.features["inventory"].available and not self.session.tainted:
                try:
                    items = self.session.list_inventory()
                except RuntimeError as exc:
                    result.warnings.append("Actualización automática pendiente: " + str(exc))
            return result, items
        self._run("Añadir objetos y actualizar inventario", add_and_refresh, done)

    def _pack_selection(self):
        row = self.pack_table.currentRow()
        if hasattr(self, "grant_quantity") and 0 <= row < len(self.display_packs):
            self.grant_quantity.setValue(min(self.display_packs[row].quantity, self.grant_quantity.maximum()))
        self._refresh_controls()

    def _clear_inventory(self):
        self.inventory = []
        self.inventory_loaded_at = None
        self.inventory_totals = {}
        self.inventory_identity = None
        self.inventory_table.setRowCount(0)
        self.inventory_selected.setText("Actualiza el inventario desde la partida.")
        self.resource_statuses = []
        self.resource_table.setRowCount(0)
        self.resource_hint.setText("Actualiza el inventario para comprobar los recursos. Ausente no significa cantidad cero.")
        self._update_catalog_quantities()

    def load_inventory(self):
        if self._available("inventory"):
            self._run("Leer inventario", self.session.list_inventory, self._inventory_loaded)

    def _inventory_loaded(self, items):
        self.inventory_table.clearSelection()
        self.inventory_table.setCurrentCell(-1, -1)
        self.inventory = items
        self.inventory_loaded_at = datetime.now()
        self.inventory_identity = self.session.identity if self.session else None
        self.inventory_totals = {}
        for item in items:
            self.inventory_totals[item.hash_id] = self.inventory_totals.get(item.hash_id, 0) + item.quantity
        self.inventory_selected.setText("Selecciona un objeto.")
        self.inventory_table.setRowCount(len(items))
        for row, item in enumerate(items):
            for column, text in enumerate([item.name, item.quantity, f"{int(item.hash_id):016X}"]):
                self.inventory_table.setItem(row, column, QTableWidgetItem(str(text)))
        self._activity(f"Inventario actualizado: {len(items)} registros.")
        if self.session:
            self._resources_loaded(self.resource_assistant.inspect(self.session, items))
        self._update_catalog_quantities()
        if self.detail_item:
            self._show_detail(item=self.detail_item)

    def _resources_loaded(self, statuses):
        self.resource_statuses = statuses
        self.resource_table.setRowCount(len(statuses))
        for row, status in enumerate(statuses):
            values = [status.rule.name, status.rule.group, str(status.quantity) if status.quantity is not None else "No localizado", status.state, status.detail]
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setToolTip(status.detail)
                self.resource_table.setItem(row, column, cell)
        missing = sum(status.can_initialize for status in statuses)
        self.resource_hint.setText(f"{missing} recursos básicos sin registro pueden recibir una unidad. Los recursos de contenido, eventos y progreso requieren comprobación aparte.")

    def repair_resources(self):
        if not (self._available("inventory") and self._available("grant")):
            return
        self.inventory_tabs.setCurrentIndex(1)
        def done(result):
            if self.session.tainted:
                self._clear_inventory()
            else:
                self._inventory_loaded(result.inventory)
                self._resources_loaded(result.statuses)
            self._activity(f"Recursos básicos: {result.requested} solicitudes; {result.initialized} recursos localizados con cantidad positiva al verificar." + (" Operación cancelada." if result.cancelled else ""))
            for warning in result.warnings:
                self._activity(warning)
        self._run("Resolver recursos básicos ausentes", lambda: self.resource_assistant.repair_missing_base(self.session, cancel=self.worker.cancel, progress=self.worker.progress.emit), done)

    def _inventory_selection(self):
        row = self.inventory_table.currentRow()
        if 0 <= row < len(self.inventory):
            item = self.inventory[row]
            self.new_quantity.setMaximum(self.quantity_policy.limit(item.hash_id))
            self.new_quantity.setToolTip(self.quantity_policy.rule(item.hash_id).reason)
            self.inventory_selected.setText(item.name)
            if item.hash_id == RATIONS_HASH:
                self.inventory_selected.setText("Raciones: tratamiento especial; usa el pack del catálogo. No se edita directamente.")
            self.new_quantity.setValue(min(item.quantity, self.new_quantity.maximum()))
        self._refresh_controls()

    def apply_quantity(self):
        row = self.inventory_table.currentRow()
        if not (self._available("inventory") and 0 <= row < len(self.inventory)):
            return
        item, value = self.inventory[row], self.new_quantity.value()
        if item.hash_id == RATIONS_HASH:
            return
        if value == 0 and QMessageBox.question(self, "Retirar objeto", f"¿Cambiar a cero la cantidad de {item.name}? Esta modificación puede quedar guardada en tu partida.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        def done(updated):
            self._clear_inventory()
            self._activity(f"Cantidad de {updated.name}: {updated.quantity}. Comprobando el inventario actualizado.")
            self.load_inventory()
        self._run("Comprobar objeto y aplicar cantidad", lambda: self.session.set_quantity(item, value), done)

    def toggle_helper(self, key):
        if not self._available(key):
            return
        enabled = not self.session.features[key].enabled
        self._run(("Activar " if enabled else "Restaurar ") + self.session.features[key].title, lambda: self.session.toggle(key, enabled))

    def apply_boost(self):
        if self._available("boost"):
            value = self.multiplier.value()
            self._run("Aplicar multiplicador", lambda: self.session.boost(value))

    def reveal_map(self):
        if self._available("map"):
            self._run("Solicitar revelado del mapa", lambda: self.session.unlock_map(cancel=self.worker.cancel, progress=self.worker.progress.emit), lambda count: self._activity(f"Mapa: {count}/4 solicitudes completadas. Comprueba los iconos en la partida."))

    def cancel_operation(self):
        self.worker.cancel.set()
        self.cancel_button.setEnabled(False)
        self._activity("Cancelación solicitada. Esperando el final de la operación en curso.")

    def _poll(self):
        if self.busy:
            return
        reap_quarantine()
        if not self.session:
            return
        try:
            alive = self.session.memory.is_alive()
        except Exception:
            alive = False
        if not alive:
            self._activity("El juego se cerró. Se han descartado las direcciones de esta sesión.")
            self.disconnect_game()

    def diagnostic_data(self):
        features = []
        if self.session:
            features = [{"key": x.key, "title": x.title, "available": x.available, "enabled": x.enabled, "reason": x.reason} for x in self.session.features.values()]
        process = None
        if self.session:
            memory = self.session.memory
            process = {"pid": memory.pid, "name": memory.name, "path": memory.path, "identity": memory.identity, "executable": self.process_fingerprint}
        return {"application": "Valhalla Companion", "version": __version__, "catalog_items": len(self.catalog.items), "catalog_packs": len(self.catalog.packs), "source": self.catalog.source, "process": process, "features": features, "activity": self.activity.toPlainText(), "gameplay_validated": False}

    def export_diagnostics(self):
        target, _ = QFileDialog.getSaveFileName(self, "Guardar diagnóstico", str(user_dir() / "diagnostico.json"), "JSON (*.json)")
        if target:
            try:
                Path(target).write_text(json.dumps(self.diagnostic_data(), ensure_ascii=False, indent=2), encoding="utf-8")
                self._activity("Diagnóstico guardado.")
            except OSError as exc:
                QMessageBox.warning(self, "No se pudo guardar", str(exc))

    def about(self):
        QMessageBox.about(self, "Acerca de Valhalla Companion", f"Valhalla Companion {__version__}\nAplicación independiente de Cheat Engine.\n\nCatálogo y referencias: AC Valhalla Inventory Editor; budabum y los colaboradores acreditados en la tabla fuente.\n\nGalería con ilustraciones vectoriales originales, favoritos e imágenes locales. Controles con QtAwesome / Font Awesome. Créditos y licencias en THIRD_PARTY.md.\n\nInterfaz con navegación por teclado, búsqueda y tamaño de texto ajustable.\n\nEsta edición necesita validación dentro de la compilación concreta del juego.")

    def closeEvent(self, event):
        if self.busy:
            self.close_pending = True
            self.worker.cancel.set()
            self._activity("Cerrando: espera a que termine la operación en curso.")
            event.ignore()
            return
        if self.session:
            self.close_pending = True
            self.disconnect_game()
            event.ignore()
            return
        self.timer.stop()
        event.accept()
