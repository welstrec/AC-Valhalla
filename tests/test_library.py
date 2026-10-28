"""Local visual preferences and request review tests; no game or network."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

from valhalla.library import MAX_IMAGE_BYTES, RequestQueue, UserLibrary, normalize_id
from valhalla.quantities import MAX_STACK_QUANTITY, QuantityPolicy
from valhalla.catalog import Catalog


IRON = "000001AF7031F3F9"
LEATHER = "000001AF7031F403"
SILVER = "0000001BFDB9F655"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "library"
        self.library = UserLibrary(self.root)

    def image(self, name="input.png", size=(80, 40), color="red", **save_options):
        path = self.directory / name
        Image.new("RGB", size, color).save(path, **save_options)
        return path

    def test_favorites_round_trip_normalizes_and_returns_detached_set(self):
        self.assertTrue(self.library.toggle_favorite("0x" + IRON.lower()))
        self.assertTrue(self.library.is_favorite(int(IRON, 16)))
        self.library.favorites.clear()
        reloaded = UserLibrary(self.root)
        self.assertEqual(reloaded.favorites, {IRON})
        self.assertFalse(reloaded.toggle_favorite(IRON))
        self.assertEqual(UserLibrary(self.root).favorites, set())
        self.assertEqual(reloaded.warnings, [])

    def test_invalid_identifiers_do_not_mutate_or_write_manifest(self):
        for value in (True, 1.2, None, "123", "0" * 16, "../picture.png"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.library.toggle_favorite(value)
        self.assertFalse(self.library.manifest.exists())
        self.assertEqual(self.library.favorites, set())

    def test_manifest_write_failure_preserves_previous_state_and_cleans_temp(self):
        self.library.toggle_favorite(IRON)
        previous = self.library.manifest.read_bytes()
        with patch("valhalla.library.os.replace", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                self.library.toggle_favorite(LEATHER)
        self.assertEqual(self.library.manifest.read_bytes(), previous)
        self.assertEqual(self.library.favorites, {IRON})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_corrupt_manifest_is_preserved_and_warned_before_recovery(self):
        raw = b'{"favorites": invalid-json'
        self.library.manifest.write_bytes(raw)
        recovered = UserLibrary(self.root)
        copies = list(self.root.glob("library.invalid-*.json"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), raw)
        self.assertFalse(recovered.manifest.exists())
        self.assertTrue(recovered.warnings)
        recovered.toggle_favorite(IRON)
        self.assertEqual(UserLibrary(self.root).favorites, {IRON})
        self.assertEqual(copies[0].read_bytes(), raw)

    def test_unknown_schema_is_preserved_without_silent_overwrite(self):
        raw = b'{"schema_version": 999, "favorites": ["future-state"]}'
        self.library.manifest.write_bytes(raw)
        recovered = UserLibrary(self.root)
        self.assertEqual(recovered.favorites, set())
        self.assertTrue(recovered.warnings)
        self.assertEqual(next(self.root.glob("library.invalid-*.json")).read_bytes(), raw)

    def test_manifest_traversal_is_ignored_and_preserves_valid_favorite(self):
        outside = self.directory / "outside.png"
        outside.write_bytes(b"do-not-touch")
        document = {"schema_version": 1, "favorites": [IRON], "images": {IRON: "../outside.png"}}
        self.library.manifest.write_text(json.dumps(document), encoding="utf-8")
        recovered = UserLibrary(self.root)
        self.assertIsNone(recovered.image_path(IRON))
        self.assertEqual(recovered.favorites, {IRON})
        self.assertTrue(recovered.warnings)
        self.assertEqual(outside.read_bytes(), b"do-not-touch")
        self.assertEqual(len(list(self.root.glob("library.invalid-*.json"))), 1)

    def test_raster_import_is_normalized_bounded_and_persistent(self):
        source = self.image(size=(1600, 800), color="purple")
        original = source.read_bytes()
        target = self.library.set_image(IRON, source)
        self.assertEqual(target, (self.root / "images" / f"{IRON}.png").resolve())
        self.assertEqual(source.read_bytes(), original)
        with Image.open(target) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (512, 256))
        self.assertEqual(UserLibrary(self.root).image_path(IRON), target)
        self.assertEqual(list(self.library.images.glob("*.tmp")), [])

    def test_jpeg_orientation_is_applied_and_metadata_removed(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "private author metadata"
        source = self.image("portrait.jpg", size=(40, 20), exif=exif)
        target = self.library.set_image(IRON, source)
        with Image.open(target) as image:
            self.assertEqual(image.size, (20, 40))
            self.assertEqual(len(image.getexif()), 0)

    def test_svg_nonimage_and_animated_image_are_rejected(self):
        svg = self.directory / "test.svg"
        svg.write_text('<svg><script>never run</script></svg>')
        with self.assertRaises(ValueError):
            self.library.set_image(IRON, svg)
        bogus = self.directory / "bogus.png"
        bogus.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            self.library.set_image(IRON, bogus)
        animated = self.directory / "animated.png"
        first, second = Image.new("RGBA", (4, 4), "red"), Image.new("RGBA", (4, 4), "blue")
        first.save(animated, save_all=True, append_images=[second], duration=20, loop=0)
        with self.assertRaisesRegex(ValueError, "estática"):
            self.library.set_image(IRON, animated)
        self.assertIsNone(self.library.image_path(IRON))

    def test_file_and_pixel_limits_are_checked_before_full_decode(self):
        huge_file = self.directory / "oversized.png"
        with huge_file.open("wb") as stream:
            stream.seek(MAX_IMAGE_BYTES)
            stream.write(b"x")
        with self.assertRaisesRegex(ValueError, "20 MB"):
            self.library.set_image(IRON, huge_file)
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        header = struct.pack(">IIBBBBB", 6000, 6000, 8, 2, 0, 0, 0)
        oversized = self.directory / "too-many-pixels.png"
        oversized.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))
        with self.assertRaisesRegex(ValueError, "20 millones"):
            self.library.set_image(IRON, oversized)

    def test_failed_image_manifest_commit_restores_existing_thumbnail(self):
        old = self.library.set_image(IRON, self.image("red.png", color="red"))
        previous = old.read_bytes()
        previous_manifest = self.library.manifest.read_bytes()
        with patch.object(self.library, "_save", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.library.set_image(IRON, self.image("blue.png", color="blue"))
        self.assertEqual(old.read_bytes(), previous)
        self.assertEqual(self.library.manifest.read_bytes(), previous_manifest)
        self.assertEqual(self.library.image_path(IRON), old)

    def test_clear_image_removes_only_owned_image_and_keeps_favorites(self):
        self.library.toggle_favorite(IRON)
        source = self.image()
        target = self.library.set_image(IRON, source)
        self.library.clear_image(IRON)
        self.assertFalse(target.exists())
        self.assertTrue(source.exists())
        self.assertIsNone(self.library.image_path(IRON))
        self.assertEqual(UserLibrary(self.root).favorites, {IRON})


class QueueTests(unittest.TestCase):
    def test_duplicate_uses_latest_quantity_and_keeps_order(self):
        queue = RequestQueue()
        queue.add([IRON, LEATHER, IRON], 2, "First pack")
        queue.add(["0x" + IRON.lower()], 7, "New quantity")
        self.assertEqual(queue.count, 2)
        self.assertEqual([(e.hash_id, e.quantity, e.label) for e in queue.entries],
                         [(IRON, 7, "New quantity"), (LEATHER, 2, "First pack")])
        detached = queue.entries
        detached.clear()
        self.assertEqual(queue.count, 2)

    def test_bound_and_all_validation_happen_before_mutation(self):
        queue = RequestQueue(max_entries=2)
        queue.add([IRON], 1, "Iron")
        for hashes, quantity in (([LEATHER, SILVER], 2), ([LEATHER, "bad"], 2), ([IRON], True), ([IRON], MAX_STACK_QUANTITY + 1), (IRON, 1), ([], 1)):
            with self.subTest(hashes=hashes, quantity=quantity), self.assertRaises(ValueError):
                queue.add(hashes, quantity, "Invalid")
            self.assertEqual(queue.count, 1)
            self.assertEqual(queue.entries[0].quantity, 1)
        queue.add([LEATHER], 2, "Leather")
        queue.add([IRON], 3, "Existing at capacity")
        self.assertEqual(queue.count, 2)
        self.assertEqual(queue.entries[0].quantity, 3)

    def test_large_resource_request_and_mixed_equipment_quantities(self):
        catalog = Catalog.load()
        policy = QuantityPolicy(catalog)
        armour = next(item for item in catalog.items if item.name == "Berserker Armor")
        queue = RequestQueue(quantity_policy=policy)
        queue.add([IRON, armour.hash_id], 100_000, "Mixed request")
        self.assertEqual({entry.hash_id: entry.quantity for entry in queue.entries},
                         {IRON: 100_000, armour.hash_id: 1})
        queue.add([IRON], MAX_STACK_QUANTITY, "Upper edit range")
        self.assertEqual(queue.entries[0].quantity, MAX_STACK_QUANTITY)

    def test_remove_and_clear_are_inert_and_deterministic(self):
        queue = RequestQueue()
        queue.add([IRON, LEATHER], 1, "Review")
        self.assertTrue(queue.remove(IRON))
        self.assertFalse(queue.remove(IRON))
        self.assertEqual(queue.entries[0].hash_id, LEATHER)
        queue.clear()
        self.assertEqual(queue.count, 0)


if __name__ == "__main__":
    unittest.main()
