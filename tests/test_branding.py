"""Windows identity and real executable artwork, without touching Explorer."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication, QWidget

from valhalla import branding
from valhalla import __version__
from tools.build import verify_executable_icon, version_resource

ROOT = Path(__file__).resolve().parent.parent


class BrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_original_icon_contains_all_windows_sizes(self):
        with Image.open(ROOT / "assets/valhalla.ico") as source:
            self.assertEqual(source.ico.sizes(), {(size, size) for size in branding.ICON_SIZES})
        icon = branding.application_icon()
        self.assertFalse(icon.isNull())
        for size in branding.ICON_SIZES:
            pixmap = icon.pixmap(size, size)
            self.assertFalse(pixmap.isNull())
            self.assertGreater(pixmap.toImage().pixelColor(size // 2, size // 2).alpha(), 0)

    def test_window_has_explicit_application_icon_without_changing_app_default(self):
        default_key = self.app.windowIcon().cacheKey()
        window = QWidget()
        try:
            self.assertTrue(branding.install_window_icon(window))
            self.assertFalse(window.windowIcon().isNull())
            self.assertEqual({(size.width(), size.height()) for size in window.windowIcon().availableSizes()},
                             {(size, size) for size in branding.ICON_SIZES})
            self.assertEqual(self.app.windowIcon().cacheKey(), default_key)
        finally:
            window.close()

    def test_missing_icon_reports_failure_without_installing_generic_fallback(self):
        with patch.object(branding, "resource_path", return_value=ROOT / "missing-icon.ico"):
            window = QWidget()
            try:
                with self.assertLogs(branding.LOG, level="ERROR"):
                    self.assertFalse(branding.install_window_icon(window))
            finally:
                window.close()

    def test_other_platform_does_not_load_windows_shell(self):
        with patch.object(branding.sys, "platform", "linux"), patch.object(branding.ctypes, "WinDLL") as dll:
            self.assertFalse(branding.configure_windows_identity())
            dll.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows application identity")
    def test_real_windows_identity_is_set_only_in_own_short_lived_child(self):
        program = """import ctypes, json
from valhalla.branding import configure_windows_identity
assert configure_windows_identity()
shell = ctypes.WinDLL('shell32')
get_id = shell.GetCurrentProcessExplicitAppUserModelID
get_id.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
get_id.restype = ctypes.c_long
pointer = ctypes.c_void_p()
assert get_id(ctypes.byref(pointer)) == 0
try:
    print(json.dumps({'app_id': ctypes.wstring_at(pointer)}))
finally:
    free = ctypes.WinDLL('ole32').CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    free(pointer)
"""
        completed = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                                   capture_output=True, text=True, check=True, timeout=15)
        self.assertEqual(json.loads(completed.stdout)["app_id"], branding.APP_USER_MODEL_ID)

    def test_version_metadata_uses_current_product_name_and_numeric_version(self):
        from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "version.txt"
            path.write_text(version_resource("1.23.456"), encoding="utf-8")
            metadata = load_version_info_from_text_file(str(path))
        self.assertEqual(metadata.ffi.fileVersionMS, (1 << 16) | 23)
        self.assertEqual(metadata.ffi.fileVersionLS, 456 << 16)
        self.assertIn("Valhalla Companion", str(metadata))
        with self.assertRaises(ValueError):
            version_resource("1.2.70000")

    def test_existing_exe_has_original_resources_and_rejects_different_art(self):
        executable = ROOT / "dist" / f"ValhallaCompanion-{__version__}.exe"
        if not executable.is_file():
            self.skipTest("First build has not been created yet")
        self.assertEqual(verify_executable_icon(executable, ROOT / "assets/valhalla.ico"),
                         list(branding.ICON_SIZES))
        with tempfile.TemporaryDirectory() as temporary:
            different = Path(temporary) / "different.ico"
            Image.new("RGBA", (256, 256), "#e12456").save(different, sizes=[(size, size) for size in branding.ICON_SIZES])
            with self.assertRaisesRegex(RuntimeError, "no coincide"):
                verify_executable_icon(executable, different)


if __name__ == "__main__":
    unittest.main()
