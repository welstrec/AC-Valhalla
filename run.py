"""Launch the desktop app; diagnostics never attach to a game process."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from valhalla import __version__
from valhalla.paths import resource_path, user_dir
from valhalla.branding import (APP_USER_MODEL_ID, ICON_SIZES, application_icon,
                               configure_windows_identity, install_window_icon)


def self_test() -> dict:
    import struct
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    from valhalla import assembly
    from valhalla.catalog import Catalog
    from valhalla.memory import find_matches
    from valhalla.icons import IconProvider
    import qtawesome
    from PIL import Image
    from valhalla.library import UserLibrary
    from valhalla.quantities import QuantityPolicy, MAX_STACK_QUANTITY

    windows_identity = configure_windows_identity()
    app = QApplication.instance() or QApplication(["ValhallaCompanion", "--self-test"])
    icon = application_icon()
    assert not icon.isNull()
    for size in ICON_SIZES:
        assert not icon.pixmap(size, size).isNull()
    if sys.platform == "win32":
        assert windows_identity

    catalog = Catalog.load(resource_path("data/catalog.json"))
    from valhalla.resources import ResourceAssistant
    assistant = ResourceAssistant(catalog)
    assert sum(rule.automatic for rule in assistant.rules) == 13
    assert len(catalog.items) == 2327 and len(catalog.packs) == 290
    navigation_roots = sorted({path[0] for path in catalog.pack_paths.values()})
    assert navigation_roots == ["Expansiones y extras", "Juego base", "Tienda y eventos"]
    assert len(catalog.pack_paths) == 290 and all(len(path) >= 2 for path in catalog.pack_paths.values())
    quantities = QuantityPolicy(catalog)
    assert quantities.limit("000001AF7031F3F9") == MAX_STACK_QUANTITY and MAX_STACK_QUANTITY >= 100000
    assert quantities.limit("000001B91B7D42BD") == 1
    assert len(catalog.by_pack["99199"].hashes) == 804
    assert "000001E9BC55CE53" in catalog.by_pack["99295"].hashes
    assert find_matches(bytes.fromhex("38 A0 39 B0"), "3? ?0") == [0, 2]
    samples = [
        assembly.inventory_worker(0x140001000, 0x140002000, 0x140003000),
        assembly.grant_worker(0x140001000, 0x140002000, 0x140003000, 0x140004000, 0x1AF7031F3F9, 1),
        assembly.boost_worker(0x140001000, 0x140002000, 0x140003000, 0x140004000, 1.5),
        assembly.map_worker(*range(0x140001000, 0x140008000, 0x1000), 0x1D5B2E91BF5),
    ]
    instructions = 0
    for source in samples:
        code = assembly.assemble(source)
        decoded = list(Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, 0))
        assert decoded and sum(i.size for i in decoded) == len(code)
        assert decoded[-1].mnemonic == "ret"
        instructions += len(decoded)
    icons = IconProvider(catalog)
    illustrations = {}
    for item in catalog.items:
        description = icons.describe(item)
        assert not description.exact
        illustrations.setdefault(description.kind, item)
    for item in illustrations.values():
        assert not icons.pixmap(item, 64).isNull()
    assert not qtawesome.icon("fa5s.star").pixmap(32, 32).isNull()
    with tempfile.TemporaryDirectory(prefix="valhalla-self-test-") as temporary:
        folder = Path(temporary)
        library = UserLibrary(folder / "library")
        sample_id = catalog.items[0].hash_id
        for extension in ("png", "jpg", "webp"):
            source = folder / f"sample.{extension}"
            Image.new("RGB", (24, 16), "#d4ae70").save(source)
            normalized = library.set_image(sample_id, source)
            with Image.open(normalized) as picture:
                assert picture.format == "PNG" and picture.size == (24, 16)
        library.toggle_favorite(sample_id)
        assert UserLibrary(folder / "library").is_favorite(sample_id)
    return {"ok": True, "application": "Valhalla Companion", "version": __version__,
            "architecture": struct.calcsize("P") * 8, "catalog_items": len(catalog.items),
            "catalog_packs": len(catalog.packs), "worker_instructions_checked": instructions,
            "automatic_basic_resources": 13,
            "visual_catalog_items": len(catalog.items), "visual_families_checked": len(illustrations),
            "interface_icons_checked": True,
            "application_icon_sizes_checked": list(ICON_SIZES),
            "windows_app_user_model_id": APP_USER_MODEL_ID if windows_identity else None,
            "equipment_duplicate_protection_checked": True, "stack_quantity_input_maximum": MAX_STACK_QUANTITY,
            "local_image_formats_checked": ["PNG", "JPEG", "WebP"],
            "navigation_roots": navigation_roots, "pack_paths_checked": len(catalog.pack_paths),
            "catalog_sha256": hashlib.sha256(resource_path("data/catalog.json").read_bytes()).hexdigest(),
            "attached_to_game": False, "gameplay_validated": False}


def main():
    parser = argparse.ArgumentParser(description="Valhalla Companion")
    parser.add_argument("--self-test", action="store_true", help="Verify embedded resources without opening the game")
    parser.add_argument("--self-test-output", type=Path)
    parser.add_argument("--screenshot", type=Path, help="Render an offline UI preview and exit")
    parser.add_argument("--preview-page", choices=("home", "catalog", "packs"), default="home")
    parser.add_argument("--preview-query", default="")
    parser.add_argument("--preview-node", default="")
    parser.add_argument("--preview-view", choices=("list", "cards"), default="list")
    options = parser.parse_args()
    if options.self_test or options.self_test_output:
        try:
            result = self_test()
            code = 0
        except Exception as exc:
            result, code = {"ok": False, "error": str(exc)}, 1
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if options.self_test_output:
            options.self_test_output.parent.mkdir(parents=True, exist_ok=True)
            options.self_test_output.write_text(text, encoding="utf-8")
        if sys.stdout:
            print(text)
        return code

    handler = RotatingFileHandler(user_dir() / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if options.screenshot:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer, QLocale
    from PySide6.QtWidgets import QApplication, QMessageBox
    from valhalla.catalog import Catalog
    from valhalla.theme import apply_theme
    from valhalla.ui import MainWindow

    configure_windows_identity()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Valhalla Companion")
    app.setOrganizationName("ValhallaCompanion")
    app.setApplicationVersion(__version__)
    QLocale.setDefault(QLocale("es_CO"))
    app.setWindowIcon(application_icon())
    apply_theme(app)
    try:
        window = MainWindow(Catalog.load(resource_path("data/catalog.json")))
        install_window_icon(window)
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "No se pudo abrir Valhalla Companion", f"{exc}\n\nConsulta el registro en {user_dir()}.")
        return 1
    window.show()
    if options.screenshot:
        if options.preview_page != "home":
            window.navigate(1)
            if options.preview_node:
                if not window.navigator.select_key(options.preview_node):
                    raise ValueError("Carpeta de vista previa desconocida: " + options.preview_node)
            window.search.setText(options.preview_query)
            window.search_timer.stop()
            window._filter_catalog()
            if options.preview_page == "packs":
                window.catalog_tabs.setCurrentIndex(1)
                if window.display_packs:
                    window.pack_table.selectRow(0)
            elif window.display_items:
                window.gallery.select_hash(window.display_items[0].hash_id)
            window.view_mode.setCurrentIndex(1 if options.preview_view == "cards" else 0)
        def save_preview():
            options.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(options.screenshot))
            options.screenshot.with_suffix(".json").write_text(json.dumps(window.diagnostic_data(), ensure_ascii=False, indent=2), encoding="utf-8")
            app.quit()
        QTimer.singleShot(500, save_preview)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
