"""Qt-owned controls follow the selected language without changing Windows."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from valhalla import qt_language


class QtLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        qt_language.install_qt_language(self.app, "en")

    def buttons(self):
        dialog = QMessageBox()
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                  QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
        values = {key: dialog.button(key).text().replace("&", "")
                  for key in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
                              QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Ok)}
        dialog.close()
        return values

    def test_qt_standard_message_buttons_switch_spanish_and_english(self):
        translator = qt_language.install_qt_language(self.app, "es")
        self.assertIs(self.app._valhalla_qt_translator, translator)
        spanish = self.buttons()
        self.assertEqual(spanish[QMessageBox.StandardButton.Yes], "Sí")
        self.assertEqual(spanish[QMessageBox.StandardButton.No], "No")
        self.assertEqual(spanish[QMessageBox.StandardButton.Cancel], "Cancelar")
        qt_language.install_qt_language(self.app, "en")
        english = self.buttons()
        self.assertEqual(english[QMessageBox.StandardButton.Yes], "Yes")
        self.assertEqual(english[QMessageBox.StandardButton.Cancel], "Cancel")
        self.assertIsNone(self.app._valhalla_qt_translator)

    def test_missing_qm_uses_fallback_and_does_not_blank_other_contexts(self):
        with patch.object(qt_language, "_translation_paths", return_value=()):
            translator = qt_language.install_qt_language(self.app, "es")
        self.assertIsInstance(translator, qt_language._SpanishStandardTranslator)
        self.assertEqual(self.buttons()[QMessageBox.StandardButton.Yes], "Sí")
        self.assertEqual(QCoreApplication.translate("QFileDialog", "&Open"), "&Abrir")
        self.assertEqual(QCoreApplication.translate("QFileDialog", "File &name:"), "&Nombre de archivo:")
        self.assertEqual(QCoreApplication.translate("OwnApplication", "Open"), "Open")
        self.assertEqual(QCoreApplication.translate("QFileDialog", "Unknown path string"), "Unknown path string")

    def test_repeated_switch_removes_only_the_previous_owned_translator(self):
        first = qt_language.install_qt_language(self.app, "es")
        with patch.object(self.app, "removeTranslator", wraps=self.app.removeTranslator) as removed:
            second = qt_language.install_qt_language(self.app, "es")
        self.assertIsNot(first, second)
        removed.assert_called_once_with(first)
        self.assertIs(self.app._valhalla_qt_translator, second)
        self.assertEqual(self.buttons()[QMessageBox.StandardButton.Yes], "Sí")

    def test_invalid_language_leaves_current_translator_untouched(self):
        current = qt_language.install_qt_language(self.app, "es")
        with self.assertRaises(ValueError):
            qt_language.install_qt_language(self.app, "de")
        self.assertIs(self.app._valhalla_qt_translator, current)


if __name__ == "__main__":
    unittest.main()
