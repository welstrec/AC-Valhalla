import os
from pathlib import Path
from PySide6.QtGui import QColor, QPalette, QFont, QFontDatabase

_FONTS_LOADED = False

DARK = """
QWidget { background: #111c29; color: #edf3f7; font-family: 'Segoe UI'; font-size: 14px; }
QMainWindow, QWidget#body { background: #111c29; }
QWidget#sidebar { background: #0b141f; border-right: 1px solid #263747; }
QLabel#brand { color: #e0ba79; font-size: 24px; font-weight: 700; letter-spacing: 3px; }
QLabel#eyebrow { color: #9ab0c4; font-size: 11px; font-weight: 600; letter-spacing: 2px; }
QLabel#title { font-size: 30px; font-weight: 650; }
QLabel#sectionTitle { font-size: 22px; font-weight: 600; }
QLabel#muted { color: #b3c3d2; }
QLabel#stat { color: #e0ba79; font-size: 28px; font-weight: 600; }
QLabel#status { background: #152c32; color: #a6eadb; padding: 10px 14px; border-radius: 7px; }
QLabel#notice { background: #223140; color: #d6e1eb; padding: 14px; border: 1px solid #3b5164; border-radius: 8px; }
QFrame#card { background: #182737; border: 1px solid #304457; border-radius: 10px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #253b4d; color: #eff5fa; padding: 10px 16px; border: 1px solid #49647b; border-radius: 6px; min-height: 21px; }
QPushButton:hover { background: #35536a; border-color: #87b3cb; }
QPushButton:pressed { background: #122f3d; }
QPushButton:focus, QToolButton:focus { border: 2px solid #6de0c6; }
QPushButton:disabled { background: #1a2835; color: #8295a7; border-color: #304151; }
QPushButton#primary { background: #d4ae70; color: #14212e; border-color: #d4ae70; font-weight: 700; }
QPushButton#primary:hover { background: #edca92; }
QPushButton#primary:disabled { background: #44453e; color: #a5aaa7; border-color: #56605e; }
QPushButton#nav { background: transparent; border: 1px solid transparent; text-align: left; padding: 13px 15px; }
QPushButton#nav:checked { background: #213746; color: #a6eadb; border-left: 3px solid #73d7c1; }
QPushButton#nav:focus { border: 2px solid #6de0c6; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #0d1823; color: #eff5fa; border: 1px solid #516b81; border-radius: 5px; padding: 9px; min-height: 22px; selection-background-color: #356e77; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid #6de0c6; }
QComboBox QAbstractItemView { background: #152434; color: #edf3f7; selection-background-color: #315665; }
QTableWidget, QTableView { background: #111e2b; alternate-background-color: #182838; border: 1px solid #344b5e; border-radius: 5px; gridline-color: #273e50; selection-background-color: #285c68; selection-color: white; }
QTableWidget:focus { border: 2px solid #6de0c6; }
QHeaderView::section { background: #223747; color: #dce8f2; padding: 11px; border: none; border-bottom: 1px solid #466277; font-weight: 600; }
QTabWidget::pane { border: none; padding-top: 12px; }
QTabBar::tab { background: #192c3b; padding: 11px 24px; color: #bacddd; border: 1px solid #345064; }
QTabBar::tab:selected { color: #aaeee0; background: #274451; border-bottom: 2px solid #6de0c6; }
QPlainTextEdit { background: #0d1823; border: 1px solid #344b5e; border-radius: 5px; padding: 9px; color: #d9e6ef; }
QProgressBar { background: #0b1520; border: 1px solid #344b5e; border-radius: 4px; text-align: center; min-height: 20px; }
QProgressBar::chunk { background: #3f887e; }
QScrollBar:vertical { background: #0d1823; width: 14px; }
QScrollBar::handle:vertical { background: #516a7e; min-height: 24px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #edf3f7; color: #14212e; padding: 7px; border: 1px solid #739aaf; }
QStatusBar { background: #0b141f; color: #bacddd; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 20px; height: 20px; }
"""


def apply_theme(app, scale=1.0):
    global _FONTS_LOADED
    if not _FONTS_LOADED:
        # The offscreen Windows plugin does not enumerate system fonts. Explicit
        # loading also keeps the offline preview faithful to the native window.
        folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguisym.ttf"):
            path = folder / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        _FONTS_LOADED = True
    font = QFont("Segoe UI", round(10 * scale))
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#111c29"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#edf3f7"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0d1823"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#edf3f7"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#253b4d"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#edf3f7"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#285c68"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    sheet = DARK
    if scale != 1:
        import re
        sheet = re.sub(r"font-size: (\d+)px", lambda m: f"font-size: {round(int(m[1])*scale)}px", sheet)
    app.setStyleSheet(sheet)
