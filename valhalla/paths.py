from pathlib import Path
import os
import sys


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name


def user_dir() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ValhallaCompanion"
    path.mkdir(parents=True, exist_ok=True)
    return path
