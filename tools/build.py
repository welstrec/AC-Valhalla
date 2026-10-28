"""Reproducible local Windows build. No game access and no global installs."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import struct
import sys
import zipfile
from importlib import metadata as packages

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from valhalla import __version__ as VERSION
VALIDATION = ROOT / "docs" / "VALIDACION.md"


def verify_executable_icon(executable, source_icon):
    """Require every embedded Windows icon image to match our original ICO."""
    import pefile

    original = Path(source_icon).read_bytes()
    reserved, icon_type, count = struct.unpack_from("<HHH", original)
    if reserved != 0 or icon_type != 1 or count < 1:
        raise RuntimeError("El icono de la aplicación no es un archivo ICO válido")
    expected = {}
    for index in range(count):
        width, height, _, _, _, _, size, offset = struct.unpack_from("<BBBBHHII", original, 6 + 16 * index)
        expected[(width or 256, height or 256)] = original[offset:offset + size]
    with pefile.PE(str(executable), fast_load=True) as pe:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        resources = {entry.id: entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        if 3 not in resources or 14 not in resources:
            raise RuntimeError("El EXE no contiene recursos de icono de Windows")
        images = {}
        for node in resources[3].directory.entries:
            for language in node.directory.entries:
                data = language.data.struct
                images[node.id] = pe.get_data(data.OffsetToData, data.Size)
        checked = set()
        for group in resources[14].directory.entries:
            for language in group.directory.entries:
                data = language.data.struct
                contents = pe.get_data(data.OffsetToData, data.Size)
                if struct.unpack_from("<HH", contents) != (0, 1):
                    raise RuntimeError("El grupo de iconos del EXE no es válido")
                for index in range(struct.unpack_from("<H", contents, 4)[0]):
                    width, height, _, _, _, _, size, icon_id = struct.unpack_from("<BBBBHHIH", contents, 6 + 14 * index)
                    dimensions = (width or 256, height or 256)
                    payload = images.get(icon_id)
                    if payload != expected.get(dimensions) or payload is None or len(payload) != size:
                        raise RuntimeError("El icono integrado no coincide con el icono de la aplicación")
                    checked.add(dimensions)
        if checked != set(expected):
            raise RuntimeError("Faltan tamaños del icono dentro del EXE")
    return [width for width, height in sorted(checked) if width == height]


def version_resource(version):
    parts = tuple(int(part) for part in version.split("."))
    numeric = (*parts, *(0 for _ in range(4 - len(parts))))
    if len(numeric) != 4 or any(not 0 <= value <= 65535 for value in numeric):
        raise ValueError("La versión no cabe en los metadatos de Windows")
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numeric!r}, prodvers={numeric!r}, mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Valhalla Companion'),
    StringStruct('FileDescription', 'Valhalla Companion'),
    StringStruct('FileVersion', {version!r}),
    StringStruct('InternalName', 'ValhallaCompanion'),
    StringStruct('OriginalFilename', 'ValhallaCompanion-{version}.exe'),
    StringStruct('ProductName', 'Valhalla Companion'),
    StringStruct('ProductVersion', {version!r})])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    options = parser.parse_args()
    os.chdir(ROOT)
    if not options.skip_tests:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], env=env, check=True)
    subprocess.run([sys.executable, "run.py", "--self-test"], check=True)
    from PIL import Image, ImageDraw
    (ROOT / "assets").mkdir(exist_ok=True)
    image = Image.new("RGBA", (256, 256), "#101d2b")
    drawing = ImageDraw.Draw(image)
    drawing.rounded_rectangle((9, 9, 247, 247), radius=50, outline="#d9b778", width=8)
    drawing.line([(68, 62), (128, 174), (188, 62)], fill="#d9b778", width=17)
    drawing.line([(128, 38), (128, 218)], fill="#88dfc8", width=12)
    image.save(ROOT / "assets/valhalla.ico", sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)])
    version_file = ROOT / "artifacts/windows-version.txt"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(version_resource(VERSION), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                    "--name", f"ValhallaCompanion-{VERSION}", "--icon", "assets/valhalla.ico",
                    "--version-file", str(version_file),
                    "--add-data", "data/catalog.json;data", "--add-data", "assets/valhalla.ico;assets",
                    "--collect-binaries", "keystone", "--collect-data", "keystone", "--collect-data", "qtawesome",
                    "--hidden-import", "capstone", "run.py"], check=True)
    executable = ROOT / f"dist/ValhallaCompanion-{VERSION}.exe"
    embedded_icon_sizes = verify_executable_icon(executable, ROOT / "assets/valhalla.ico")
    subprocess.run([str(executable), "--self-test-output", str(ROOT / "artifacts/frozen-self-test.json")], check=True, timeout=90)
    result = json.loads((ROOT / "artifacts/frozen-self-test.json").read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"El ejecutable no pasó su comprobación: {result}")
    subprocess.run([str(executable), "--screenshot", str(ROOT / "artifacts/ui-exe.png")], check=True, timeout=90)
    subprocess.run([str(executable), "--screenshot", str(ROOT / "artifacts/ui-exe-explorer.png"),
                    "--preview-page", "catalog", "--preview-node", "pack:99201"], check=True, timeout=90)
    subprocess.run([str(executable), "--screenshot", str(ROOT / "artifacts/ui-exe-gallery.png"),
                    "--preview-page", "catalog", "--preview-query", "Berserker", "--preview-view", "cards"], check=True, timeout=90)
    metadata = {"application": "Valhalla Companion", "version": VERSION, "date": "2026-10-27",
                "file": executable.name, "size": executable.stat().st_size,
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "frozen_self_test": result, "gameplay_validated": False,
                "embedded_icon_sizes_checked": embedded_icon_sizes,
                "digital_signature": "unsigned"}
    (ROOT / "dist/release.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    portable_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    portable_readme = portable_readme.replace("(docs/VALIDACION.md)", "(VALIDACION.md)").replace("(third_party/)", "(licenses/)")
    (ROOT / "dist/README.md").write_text(portable_readme, encoding="utf-8")
    shutil.copy2(ROOT / "THIRD_PARTY.md", ROOT / "dist/THIRD_PARTY.md")
    shutil.copy2(VALIDATION, ROOT / "dist" / VALIDATION.name)
    licenses = ROOT / "dist/licenses"
    licenses.mkdir(exist_ok=True)
    for package in ("PySide6-Essentials", "shiboken6", "keystone-engine", "capstone", "pyinstaller", "Pillow", "QtAwesome", "QtPy"):
        distribution = packages.distribution(package)
        for item in distribution.files or []:
            if any(word in str(item).casefold() for word in ("license", "copying", "notice")) and (item.name.casefold().endswith((".txt", ".md", ".rst", ".bsd", ".gpl", ".lgpl")) or item.name.casefold() in {"license", "copying", "notice", "copyright"}):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    destination = licenses / package / Path(str(item).replace("..", "_"))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    python_license = Path(sys._base_executable).parent / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    for source in (ROOT / "third_party").rglob("*.txt"):
        destination = licenses / source.relative_to(ROOT / "third_party")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    portable = ROOT / f"dist/ValhallaCompanion-{VERSION}-Windows-x64.zip"
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in [executable, ROOT / "dist/release.json", ROOT / "dist/README.md", ROOT / "dist/THIRD_PARTY.md", ROOT / "dist" / VALIDATION.name, *licenses.rglob("*")]:
            if path.is_file():
                archive.write(path, path.relative_to(ROOT / "dist"))
    sources = ROOT / f"dist/ValhallaCompanion-source-{VERSION}.zip"
    with zipfile.ZipFile(sources, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in ("valhalla", "tools", "tests", "data", "assets", "third_party", "docs", ".github"):
            for path in (ROOT / folder).rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(ROOT))
        for filename in ("run.py", "requirements.txt", "build.ps1", "README.md", "THIRD_PARTY.md", ".gitignore", ".gitattributes"):
            archive.write(ROOT / filename, filename)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
