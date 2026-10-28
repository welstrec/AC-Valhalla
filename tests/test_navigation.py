"""Exact, keyboard accessible catalogue scopes without a game connection."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from dataclasses import FrozenInstanceError
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from valhalla.catalog import Catalog
from valhalla.icons import IconProvider
from valhalla.navigation import CatalogNavigator, CatalogScope, SCOPE_ROLE
from valhalla.i18n import Translator


class TrackingProvider(IconProvider):
    def __init__(self, catalog):
        super().__init__(catalog)
        self.icon_requests = []

    def icon(self, item, size=64):
        self.icon_requests.append(item.hash_id)
        return super().icon(item, size)


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.catalog = Catalog.load()
        self.provider = TrackingProvider(self.catalog)
        self.navigator = CatalogNavigator(self.catalog, self.provider)
        self.navigator.resize(330, 560)
        self.navigator.show()
        QTest.qWait(5)

    def tearDown(self):
        self.navigator.close()
        self.navigator.deleteLater()
        QTest.qWait(5)

    def test_every_pack_has_one_exact_leaf_and_no_items_disappear(self):
        leaf_keys = {key for key in self.navigator._nodes if key.startswith("pack:")}
        self.assertEqual(leaf_keys, {f"pack:{pack.id}" for pack in self.catalog.packs})
        for pack in self.catalog.packs:
            self.assertTrue(self.navigator.select_key(f"pack:{pack.id}"))
            scope = self.navigator.current_scope()
            self.assertEqual(scope.hashes, frozenset(pack.hashes))
            self.assertEqual(scope.pack_ids, frozenset({pack.id}))
            self.assertEqual(scope.pack_id, pack.id)
            self.assertEqual(scope.mode, "items")
        self.navigator.select_key("items")
        self.assertEqual(self.navigator.current_scope().hashes, frozenset(self.catalog.by_hash))

    def test_folders_count_unique_members_and_never_widen_a_leaf(self):
        for path, node in self.navigator._folder_paths.items():
            scope = node.data(0, SCOPE_ROLE)
            self.assertEqual(scope.mode, "packs")
            self.assertEqual(scope.hashes, frozenset(hash_id for pack_id in scope.pack_ids for hash_id in self.catalog.by_pack[pack_id].hashes))
            expected_packs = set()
            def visit(parent):
                for index in range(parent.childCount()):
                    child = parent.child(index)
                    child_scope = child.data(0, SCOPE_ROLE)
                    if child_scope.pack_id:
                        expected_packs.add(child_scope.pack_id)
                    visit(child)
            visit(node)
            self.assertEqual(scope.pack_ids, frozenset(expected_packs))
            self.assertIn(f"({len(expected_packs)})", node.text(0))
        self.navigator.select_key("packs")
        self.assertEqual(self.navigator.current_scope().pack_ids, frozenset(self.catalog.by_pack))

    def test_type_scopes_partition_all_items_and_do_not_request_per_item_art(self):
        kinds = {self.provider.describe(item).kind for item in self.catalog.items}
        self.assertEqual(len(self.provider.icon_requests), len(kinds))
        combined = set()
        for kind in kinds:
            self.navigator.select_key(f"kind:{kind}")
            scope = self.navigator.current_scope()
            expected = {item.hash_id for item in self.catalog.items if self.provider.describe(item).kind == kind}
            self.assertEqual(scope.hashes, frozenset(expected))
            self.assertFalse(combined.intersection(expected))
            combined.update(expected)
            self.assertEqual(scope.kind, kind)
            self.assertEqual(scope.mode, "items")
        self.assertEqual(combined, set(self.catalog.by_hash))
        self.assertLess(len(self.navigator._nodes), len(self.catalog.items))
        self.assertLess(len(self.navigator.findChildren(QWidget)), 20)

    def test_category_scopes_use_membership_not_title_substrings(self):
        for category in self.catalog.categories:
            self.assertTrue(self.navigator.select_key(f"category:{category}"))
            scope = self.navigator.current_scope()
            expected = {item.hash_id for item in self.catalog.items if category in item.categories}
            self.assertEqual(scope.hashes, frozenset(expected))
            self.assertEqual(scope.pack_ids, frozenset(pack.id for pack in self.catalog.packs if expected.intersection(pack.hashes)))

    def test_reset_and_selection_signals_keep_exact_immutable_scopes(self):
        spy = QSignalSpy(self.navigator.scope_changed)
        pack = self.catalog.packs[0]
        self.navigator.select_key(f"pack:{pack.id}")
        self.assertEqual(spy.count(), 1)
        scope = spy.at(0)[0]
        self.assertIsInstance(scope, CatalogScope)
        with self.assertRaises(FrozenInstanceError):
            scope.title = "changed"
        before = self.navigator.current_scope()
        self.assertFalse(self.navigator.select_key("pack:does-not-exist"))
        self.assertEqual(self.navigator.current_scope(), before)
        self.navigator.reset_scope()
        self.assertEqual(spy.count(), 2)
        self.assertEqual(self.navigator.current_scope().key, "all")
        self.assertIsNone(self.navigator.current_scope().hashes)
        self.navigator.reset_scope()
        self.assertEqual(spy.count(), 3)

    def test_initial_tree_shows_main_branches_without_opening_hundreds_of_packs(self):
        root = self.navigator._nodes["packs"]
        self.assertTrue(root.isExpanded())
        self.assertEqual(root.childCount(), 3)
        self.assertEqual({root.child(index).data(0, SCOPE_ROLE).title for index in range(root.childCount())},
                         {"Tienda y eventos", "Juego base", "Expansiones y extras"})
        self.assertTrue(all(not root.child(index).isExpanded() for index in range(root.childCount())))
        for pack in self.catalog.packs:
            leaf = self.navigator._nodes[f"pack:{pack.id}"]
            self.assertEqual(leaf.data(0, SCOPE_ROLE).title, pack.name)
            self.assertNotIn(" Pcs)", leaf.text(0))
            self.assertTrue(leaf.text(0).endswith(f"({len(pack.hashes)})"))

    def test_deep_paths_keep_identical_folder_names_separate_and_breadcrumb_exact(self):
        self.navigator.close()
        chosen = self.catalog.packs[:2]
        self.catalog.pack_paths = {pack.id: ("Tienda y eventos" if index == 0 else "Juego base", "Compartido", "Más")
                                   for index, pack in enumerate(chosen)}
        self.navigator = CatalogNavigator(self.catalog, self.provider)
        for index, pack in enumerate(chosen):
            self.navigator.select_key(f"pack:{pack.id}")
            root = "Tienda y eventos" if index == 0 else "Juego base"
            self.assertEqual(self.navigator.breadcrumb(), f"Packs › {root} › Compartido › Más › {pack.name}")
            node = self.navigator.tree.currentItem()
            while node.parent() is not None:
                node = node.parent()
                self.assertTrue(node.isExpanded())
        shared_nodes = [node for node in self.navigator._folder_paths if len(node) == 2 and node[-1] == "Compartido"]
        self.assertEqual(len(shared_nodes), 2)

    def test_legacy_catalogue_without_paths_has_category_folders(self):
        self.navigator.close()
        if hasattr(self.catalog, "pack_paths"):
            del self.catalog.pack_paths
        self.navigator = CatalogNavigator(self.catalog, self.provider)
        self.assertEqual({path[0] for path in self.navigator._folder_paths if path}, {pack.category for pack in self.catalog.packs})
        self.assertEqual(len([key for key in self.navigator._nodes if key.startswith("pack:")]), len(self.catalog.packs))

    def test_keyboard_moves_and_expands_tree_without_submitting_anything(self):
        self.navigator.select_key("packs")
        self.navigator.tree.setFocus()
        self.navigator.tree.currentItem().setExpanded(False)
        spy = QSignalSpy(self.navigator.scope_changed)
        QTest.keyClick(self.navigator.tree, Qt.Key.Key_Right)
        self.assertTrue(self.navigator.tree.currentItem().isExpanded())
        QTest.keyClick(self.navigator.tree, Qt.Key.Key_Down)
        self.assertGreater(spy.count(), 0)
        self.assertTrue(self.navigator.current_scope().key.startswith("folder:"))
        node = self.navigator.tree.currentItem()
        self.assertTrue(node.data(0, Qt.ItemDataRole.AccessibleTextRole))
        self.assertTrue(node.data(0, Qt.ItemDataRole.AccessibleDescriptionRole))
        self.assertIn("Categorías", self.navigator.tree.accessibleName())

    def test_language_switch_preserves_exact_scope_selection_and_expansion(self):
        self.navigator.close()
        translator = Translator("en")
        provider = IconProvider(self.catalog, translator=translator)
        self.navigator = CatalogNavigator(self.catalog, provider, translator=translator)
        pack = self.catalog.packs[0]
        self.navigator.select_key(f"pack:{pack.id}")
        scope = self.navigator.current_scope()
        expanded = {key: node.isExpanded() for key, node in self.navigator._nodes.items()}
        selected = self.navigator.tree.currentItem()
        self.assertEqual(self.navigator._nodes["all"].text(0), "All items and packs (2327)")
        self.assertEqual(self.navigator.tree.accessibleName(), "Catalogue categories")
        self.assertIn("Default quantity per item", selected.toolTip(0))
        self.assertIn(pack.name, self.navigator.breadcrumb())
        self.assertEqual(scope.title, pack.name)
        changes = QSignalSpy(self.navigator.scope_changed)
        translator.set_language("es")
        self.navigator.retranslate()
        self.assertEqual(self.navigator._nodes["all"].text(0), "Todo el catálogo (2327)")
        self.assertIs(self.navigator.tree.currentItem(), selected)
        self.assertIs(self.navigator.current_scope(), scope)
        self.assertEqual({key: node.isExpanded() for key, node in self.navigator._nodes.items()}, expanded)
        self.assertEqual(changes.count(), 0)
        self.assertEqual(scope.hashes, frozenset(pack.hashes))


if __name__ == "__main__":
    unittest.main()
