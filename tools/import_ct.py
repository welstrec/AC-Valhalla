"""Rebuild the inert catalogue while preserving the original CT byte for byte."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from valhalla.catalog import build_catalog, Catalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa únicamente datos literales de una tabla CT")
    parser.add_argument("source", type=Path)
    parser.add_argument("--source-name", help="Nombre original de la tabla para conservar los metadatos de procedencia")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "catalog.json")
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    document = build_catalog(source_bytes, args.source_name or args.source.name)
    Catalog(document)  # Validate the complete output before replacing anything.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    preserved = args.output.parent / "source.CT"
    if preserved.exists() and preserved.read_bytes() != source_bytes:
        parser.error("source.CT ya existe con otra identidad; conserva la procedencia antes de sustituirlo")
    if not preserved.exists():
        preserved.write_bytes(source_bytes)
    preserved.chmod(stat.S_IREAD)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document["source"]["statistics"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
