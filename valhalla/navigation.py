"""A compact, inert catalogue tree with exact scopes for every folder and pack."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from urllib.parse import quote

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
import qtawesome

from .i18n import Translator
from .icons import LABELS


SCOPE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
COUNT_ROLE = SCOPE_ROLE + 1
EXPLANATION_ROLE = SCOPE_ROLE + 2
VALUES_ROLE = SCOPE_ROLE + 3


@dataclass(frozen=True)
class CatalogScope:
    key: str
    title: str
    hashes: frozenset[str] | None = None
    pack_ids: frozenset[str] | None = None
    mode: str | None = None
    kind: str | None = None
    pack_id: str | None = None


class CatalogNavigator(QWidget):
    """Folders select lists; expanding a branch never submits game requests.

    Only the small number of type folders request semantic artwork. Individual
    objects remain rows in the main list, rather than thousands of tree widgets.
    """

    scope_changed = Signal(object)

    def __init__(self, catalog, icon_provider, parent=None, translator=None):
        super().__init__(parent)
        self.catalog = catalog
        self.icon_provider = icon_provider
        self.translator = translator if translator is not None else Translator("es")
        self._nodes: dict[str, QTreeWidgetItem] = {}
        self._folder_paths: dict[tuple[str, ...], QTreeWidgetItem] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setObjectName("catalogTree")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(15)
        self.tree.setIconSize(QSize(19, 19))
        self.tree.setAnimated(False)
        self.tree.setStyleSheet("""
            QTreeWidget#catalogTree { background: #0d1823; border: 1px solid #344b5e;
                border-radius: 5px; outline: none; }
            QTreeWidget#catalogTree::item { min-height: 26px; padding: 1px 2px; }
            QTreeWidget#catalogTree::item:hover { background: #203746; }
            QTreeWidget#catalogTree::item:selected { background: #285c68; color: #ffffff; }
            QTreeWidget#catalogTree::item:focus { border: 1px solid #6de0c6; }
        """)
        layout.addWidget(self.tree)
        self._folder_icon = qtawesome.icon("fa5s.folder", color="#d4ae70")
        self._pack_icon = qtawesome.icon("fa5s.box-open", color="#bcd5e4")
        self._build()
        self.retranslate()
        self.tree.setCurrentItem(self._nodes["all"])
        self.tree.currentItemChanged.connect(self._changed)

    def _node(self, parent, scope, count=None, icon=None, explanation="", values=None):
        node = QTreeWidgetItem(parent if parent is not None else self.tree)
        node.setData(0, SCOPE_ROLE, scope)
        node.setData(0, COUNT_ROLE, count)
        node.setData(0, EXPLANATION_ROLE, explanation)
        node.setData(0, VALUES_ROLE, values or {})
        node.setIcon(0, icon or self._folder_icon)
        self._nodes[scope.key] = node
        self._render_node(node)
        return node

    def _display_title(self, scope):
        if scope.pack_id:
            return scope.title
        if scope.kind:
            return self.translator.text(LABELS.get(scope.kind, scope.title))
        return self.translator.category(scope.title)

    def _render_node(self, node):
        scope = node.data(0, SCOPE_ROLE)
        title = self._display_title(scope)
        title = re.sub(r"\s*\((?:\d+\s*|Lots\s+Of\s+)Pcs\)\s*$", "", title, flags=re.IGNORECASE)
        count = node.data(0, COUNT_ROLE)
        text = title if count is None else f"{title} ({count})"
        values = dict(node.data(0, VALUES_ROLE) or {})
        values.setdefault("title", self._display_title(scope))
        explanation = self.translator.text(node.data(0, EXPLANATION_ROLE) or "", **values)
        node.setText(0, text)
        node.setData(0, Qt.ItemDataRole.AccessibleTextRole, text)
        node.setData(0, Qt.ItemDataRole.AccessibleDescriptionRole, explanation)
        node.setToolTip(0, explanation or text)

    def retranslate(self):
        """Refresh presentation only: scope keys, membership and expansion stay intact."""
        self.tree.setAccessibleName(self.translator.text("Categorías del catálogo"))
        self.tree.setAccessibleDescription(self.translator.text("Usa las flechas para abrir categorías y seleccionar una lista de packs u objetos. No se modifica la partida al navegar."))
        for node in self._nodes.values():
            self._render_node(node)

    def _build(self):
        all_hashes = frozenset(self.catalog.by_hash)
        all_packs = frozenset(self.catalog.by_pack)
        self._node(None, CatalogScope("all", "Todo el catálogo"), len(all_hashes),
                   qtawesome.icon("fa5s.list-ul", color="#bcd5e4"),
                   "{items} objetos distintos y {packs} packs. Quita el límite de carpeta.",
                   {"items": len(all_hashes), "packs": len(all_packs)})
        packs_root = self._node(None, CatalogScope("packs", "Packs", frozenset(), frozenset(), "packs"),
                                len(all_packs), explanation="Packs organizados como en la tabla original. Abre un pack para ver sus objetos.")
        self._folder_paths[()] = packs_root
        folder_members = defaultdict(set)
        folder_hashes = defaultdict(set)
        paths = getattr(self.catalog, "pack_paths", {})
        for pack in self.catalog.packs:
            raw_path = paths.get(pack.id, (pack.category,))
            path = tuple(str(part).strip() for part in raw_path if str(part).strip())
            if path and path[-1] == pack.name:
                path = path[:-1]
            path = path or (pack.category or "Otros packs",)
            parent_path = ()
            folder_members[()].add(pack.id)
            folder_hashes[()].update(pack.hashes)
            for part in path:
                full_path = (*parent_path, part)
                if full_path not in self._folder_paths:
                    key = "folder:" + "/".join(quote(value, safe="") for value in full_path)
                    self._folder_paths[full_path] = self._node(self._folder_paths[parent_path], CatalogScope(key, part))
                folder_members[full_path].add(pack.id)
                folder_hashes[full_path].update(pack.hashes)
                parent_path = full_path
            scope = CatalogScope(f"pack:{pack.id}", pack.name, frozenset(pack.hashes),
                                 frozenset({pack.id}), "items", pack_id=pack.id)
            self._node(self._folder_paths[parent_path], scope, len(pack.hashes), self._pack_icon,
                       "{name}. {items} objetos distintos. Cantidad predeterminada por objeto: {quantity}. Selecciona para ver su contenido.",
                       {"name": pack.name, "items": len(pack.hashes), "quantity": pack.quantity})
        for path, node in self._folder_paths.items():
            previous = node.data(0, SCOPE_ROLE)
            scope = CatalogScope(previous.key, previous.title, frozenset(folder_hashes[path]),
                                 frozenset(folder_members[path]), "packs")
            node.setData(0, SCOPE_ROLE, scope)
            node.setData(0, COUNT_ROLE, len(scope.pack_ids))
            node.setData(0, EXPLANATION_ROLE, "{title}: {packs} packs y {items} objetos distintos.")
            node.setData(0, VALUES_ROLE, {"packs": len(scope.pack_ids), "items": len(scope.hashes)})
            self._render_node(node)
        packs_root.setExpanded(True)

        membership = defaultdict(set)
        for pack in self.catalog.packs:
            for hash_id in pack.hashes:
                membership[hash_id].add(pack.id)
        groups = defaultdict(list)
        labels = {}
        for item in self.catalog.items:
            description = self.icon_provider.describe(item)
            groups[description.kind].append(item)
            labels[description.kind] = LABELS.get(description.kind, description.label)
        type_root = self._node(None, CatalogScope("items", "Objetos por tipo", all_hashes, all_packs, "items"), len(all_hashes))
        for kind in sorted(groups, key=lambda value: labels[value].casefold()):
            items = groups[kind]
            hashes = frozenset(item.hash_id for item in items)
            pack_ids = frozenset(pack_id for hash_id in hashes for pack_id in membership[hash_id])
            icon = self.icon_provider.icon(items[0], 32) if hasattr(self.icon_provider, "icon") else self._folder_icon
            self._node(type_root, CatalogScope(f"kind:{kind}", labels[kind], hashes, pack_ids, "items", kind),
                       len(hashes), icon, "{title}: {items} objetos. El tipo se infiere del nombre y contexto; no confirma atributos dentro del juego.",
                       {"items": len(hashes)})
        category_root = self._node(None, CatalogScope("categories", "Objetos por categoría", all_hashes, all_packs, "items"), len(all_hashes))
        for category in self.catalog.categories:
            hashes = frozenset(item.hash_id for item in self.catalog.items if category in item.categories)
            pack_ids = frozenset(pack_id for hash_id in hashes for pack_id in membership[hash_id])
            self._node(category_root, CatalogScope(f"category:{category}", category, hashes, pack_ids, "items"), len(hashes))

    def _changed(self, current, previous=None):
        if current is not None:
            scope = current.data(0, SCOPE_ROLE)
            if scope is not None:
                self.scope_changed.emit(scope)

    def current_scope(self):
        node = self.tree.currentItem()
        return node.data(0, SCOPE_ROLE) if node is not None else self._nodes["all"].data(0, SCOPE_ROLE)

    def select_key(self, key):
        node = self._nodes.get(key)
        if node is None:
            return False
        parent = node.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(node)
        self.tree.scrollToItem(node)
        return True

    def reset_scope(self):
        already_selected = self.current_scope().key == "all"
        self.select_key("all")
        if already_selected:
            self.scope_changed.emit(self.current_scope())

    def breadcrumb(self):
        node = self.tree.currentItem()
        names = []
        while node is not None:
            names.append(self._display_title(node.data(0, SCOPE_ROLE)))
            node = node.parent()
        return " › ".join(reversed(names))
