"""Bilingual presentation and local preferences, without any game process."""
import ast
import json
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import patch

from valhalla import i18n


ROOT = Path(__file__).resolve().parent.parent


class TranslationTests(unittest.TestCase):
    def test_languages_are_normalized_and_unsupported_values_are_rejected(self):
        self.assertEqual(i18n.normalize_language("EN_us"), "en")
        self.assertEqual(i18n.normalize_language(" es-CO "), "es")
        for value in ("fr", "", None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                i18n.normalize_language(value)

    def test_exact_messages_switch_both_ways_from_the_same_source(self):
        translator = i18n.Translator("es")
        source = "Leer y editar cantidades"
        self.assertEqual(translator.text(source), source)
        translator.set_language("en")
        self.assertEqual(translator.text(source), "Read and edit quantities")
        translator.set_language("es")
        self.assertEqual(translator.text(source), source)

    def test_named_values_use_registered_numbered_templates(self):
        source = "La cantidad debe estar entre 1 y {maximum}"
        self.assertEqual(i18n.Translator("en").text(source, maximum=100000),
                         "The quantity must be between 1 and 100000")
        self.assertEqual(i18n.Translator("es").text(source, maximum=100000),
                         "La cantidad debe estar entre 1 y 100000")

    def test_backend_messages_preserve_numeric_values_and_addresses(self):
        translator = i18n.Translator("en")
        self.assertEqual(translator.message("No se pudieron leer 64 bytes en 0x140001000"),
                         "Could not read 64 bytes at 0x140001000")
        self.assertEqual(translator.message("La operación del juego terminó con código 0xC0000005"),
                         "The game operation exited with code 0xC0000005")
        self.assertEqual(translator.message("La cantidad actual más la solicitud excede el rango permitido para este objeto (99). No se envió la solicitud."),
                         "The current quantity plus the request exceeds this item's permitted range (99). No request was sent.")

    def test_nested_backend_errors_and_windows_codes_are_translated(self):
        translator = i18n.Translator("en")
        self.assertEqual(translator.message("No se pudo verificar el inventario final: La fila pertenece a otra sesión del juego"),
                         "The final inventory could not be verified: This row belongs to another game session")
        self.assertEqual(translator.message("Iron Ore: La cantidad debe estar entre 1 y 100000"),
                         "Iron Ore: The quantity must be between 1 and 100000")
        self.assertEqual(translator.message("No se pudo verificar la identidad del proceso (Windows: 5)."),
                         "The process identity could not be verified (Windows: 5).")

    def test_unknown_system_errors_paths_hashes_and_names_remain_intact(self):
        translator = i18n.Translator("en")
        unknown = r"Driver rejected [code 91]: C:\Users\Player\{my-file}\000001AF7031F3F9.png"
        self.assertEqual(translator.message(unknown), unknown)
        self.assertEqual(translator.text("Gungnir"), "Gungnir")
        self.assertEqual(translator.message("000001AF7031F3F9"), "000001AF7031F3F9")
        self.assertEqual(translator.message("Failed\nLa sesión quedó bloqueada. Reinicia el juego antes de continuar."),
                         "Failed\nThe session became blocked. Restart the game before continuing.")

    def test_timestamped_activity_preserves_the_original_time(self):
        self.assertEqual(i18n.Translator("en").message("23:04:01  Añadir objetos"),
                         "23:04:01  Add items")

    def test_operation_activity_does_not_match_an_unrelated_count_template(self):
        translator = i18n.Translator("en")
        self.assertEqual(translator.message("Leer inventario…"), "Read inventory…")
        self.assertEqual(translator.message("Leer inventario: Carga una partida antes de añadir objetos"),
                         "Read inventory: Load a save before adding items")
        self.assertEqual(translator.message("23:04:01  Leer inventario…"),
                         "23:04:01  Read inventory…")
        self.assertEqual(translator.message("Buscar dentro de «Berserker Pack (19 Pcs)»…"),
                         "Search within “Berserker Pack (19 Pcs)”…")

    def test_category_labels_never_change_the_stable_source_identifier(self):
        source = "ARMOR"
        self.assertEqual(i18n.Translator("es").category(source), "Armaduras")
        self.assertEqual(i18n.Translator("en").category(source), "Armor")
        self.assertEqual(source, "ARMOR")
        self.assertEqual(i18n.Translator("en").category("Sin categoría"), "Uncategorized")
        self.assertEqual(i18n.Translator("es").category("Unknown group"), "Unknown group")

    def test_translation_templates_keep_the_same_substitution_fields(self):
        formatter = string.Formatter()
        for source, target in i18n.Translator("en").translations.items():
            with self.subTest(source=source):
                original = {field for _, field, _, _ in formatter.parse(source) if field is not None}
                translated = {field for _, field, _, _ in formatter.parse(target) if field is not None}
                self.assertEqual(original, translated)

    def test_every_direct_backend_exception_has_an_english_translation(self):
        translations = i18n.Translator("en").translations
        def source_text(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.JoinedStr):
                pieces, number = [], 0
                for value in node.values:
                    if isinstance(value, ast.Constant):
                        pieces.append(str(value.value))
                    else:
                        pieces.append("{" + str(number) + "}")
                        number += 1
                return "".join(pieces)
            return None
        for module in ("engine", "memory", "resources", "library", "quantities", "requests", "catalog"):
            tree = ast.parse((ROOT / "valhalla" / f"{module}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                    source = source_text(node.exc.args[0])
                    if source:
                        with self.subTest(module=module, line=node.lineno):
                            self.assertIn(source, translations)


class LanguagePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "settings.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_explicit_saved_and_system_language_have_a_defined_precedence(self):
        with patch.object(i18n, "_system_language", return_value="es"):
            self.assertEqual(i18n.resolve_language(settings_path=self.path), "es")
            i18n.save_language("en", self.path)
            self.assertEqual(i18n.resolve_language(settings_path=self.path), "en")
            self.assertEqual(i18n.resolve_language("es", self.path), "es")

    def test_saving_language_keeps_other_settings_and_uses_atomic_replacement(self):
        self.path.write_text(json.dumps({"custom": {"enabled": True}, "text_size": 1.25}), encoding="utf-8")
        i18n.save_language("es-CO", self.path)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document, {"custom": {"enabled": True}, "text_size": 1.25, "language": "es"})
        self.assertEqual(list(self.path.parent.glob("language-*.tmp")), [])

    def test_invalid_settings_fall_back_and_are_preserved_before_explicit_save(self):
        original = b"broken previous preference\n"
        self.path.write_bytes(original)
        with self.assertLogs(i18n.LOG, level="WARNING"), patch.object(i18n, "_system_language", return_value="en"):
            self.assertEqual(i18n.resolve_language(settings_path=self.path), "en")
        self.assertEqual(self.path.read_bytes(), original)
        i18n.save_language("es", self.path)
        backups = list(self.path.parent.glob("settings.invalid-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(i18n.load_language(self.path), "es")

    def test_failed_write_preserves_previous_settings_and_removes_only_own_temp(self):
        i18n.save_language("es", self.path)
        original = self.path.read_bytes()
        with patch.object(i18n.os, "replace", side_effect=PermissionError("test refusal")):
            with self.assertRaises(PermissionError):
                i18n.save_language("en", self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("language-*.tmp")), [])

    def test_invalid_saved_language_does_not_become_a_third_supported_locale(self):
        self.path.write_text('{"language":"fr"}', encoding="utf-8")
        with self.assertLogs(i18n.LOG, level="WARNING"):
            self.assertIsNone(i18n.load_language(self.path))
        with self.assertRaises(ValueError):
            i18n.save_language("fr", self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"language":"fr"}')


if __name__ == "__main__":
    unittest.main()
