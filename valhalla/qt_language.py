"""Translate Qt's own buttons and file picker independently of game text."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QTranslator

from .paths import resource_path


LOG = logging.getLogger(__name__)

_CONTEXTS = frozenset({"QPlatformTheme", "QDialogButtonBox", "QFileDialog"})
_SPANISH_BUTTONS = {
    "OK": "Aceptar", "&OK": "&Aceptar",
    "Yes": "Sí", "&Yes": "&Sí",
    "No": "No", "&No": "&No",
    "Yes to All": "Sí a todo", "Yes to &All": "Sí a &todo", "&Yes to All": "&Sí a todo",
    "No to All": "No a todo", "N&o to All": "N&o a todo", "&No to All": "&No a todo",
    "Cancel": "Cancelar", "&Cancel": "&Cancelar",
    "Close": "Cerrar", "&Close": "&Cerrar",
    "Open": "Abrir", "&Open": "&Abrir",
    "Save": "Guardar", "&Save": "&Guardar",
    "Save All": "Guardar todo", "Save &All": "Guardar &todo",
    "Discard": "Descartar", "&Discard": "&Descartar",
    "Apply": "Aplicar", "&Apply": "&Aplicar",
    "Reset": "Restablecer", "&Reset": "&Restablecer",
    "Restore Defaults": "Restaurar valores predeterminados",
    "Restore &Defaults": "Restaurar valores &predeterminados",
    "Help": "Ayuda", "&Help": "A&yuda",
    "Abort": "Anular", "&Abort": "&Anular",
    "Retry": "Reintentar", "&Retry": "&Reintentar",
    "Ignore": "Ignorar", "&Ignore": "&Ignorar",
}
_SPANISH_PICKER = {
    "Look in:": "Buscar en:", "Look in": "Buscar en",
    "File &name:": "&Nombre de archivo:", "File name:": "Nombre de archivo:",
    "Files of type:": "Archivos de tipo:", "Files of type": "Archivos de tipo",
    "File type:": "Tipo de archivo:", "&File name:": "&Nombre de archivo:",
    "Directory:": "Directorio:", "&Directory:": "&Directorio:",
    "Folder:": "Carpeta:", "New Folder": "Nueva carpeta",
    "Create New Folder": "Crear nueva carpeta", "Go to the parent directory": "Ir a la carpeta superior",
    "Back": "Atrás", "Forward": "Adelante", "Parent Directory": "Carpeta superior",
    "List View": "Vista de lista", "Detail View": "Vista detallada",
    "Show ": "Mostrar ", "All Files (*)": "Todos los archivos (*)",
    "Choose": "Elegir", "Select": "Seleccionar", "Find Directory": "Buscar directorio",
    "Name": "Nombre", "Size": "Tamaño", "Type": "Tipo", "Date Modified": "Fecha de modificación",
    "Computer": "Equipo", "My Computer": "Mi equipo", "Show hidden files": "Mostrar archivos ocultos",
}


class _SpanishStandardTranslator(QTranslator):
    """Small fallback when Qt's complete Spanish catalogue is unavailable."""

    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context not in _CONTEXTS:
            return None
        if sourceText in _SPANISH_BUTTONS:
            return _SPANISH_BUTTONS[sourceText]
        if context == "QFileDialog":
            return _SPANISH_PICKER.get(sourceText)
        # Returning None preserves the null QString that lets Qt fall through
        # to other translators or its source. An empty string would blank it.
        return None


def _translation_paths():
    bundled = resource_path("assets/qtbase_es.qm")
    installed = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_es.qm"
    return tuple(dict.fromkeys((bundled, installed)))


def install_qt_language(app, language):
    """Install/remove only this app's Qt translator; never changes OS settings.

    Keep a Python reference on QApplication because installed translators must
    remain alive. English uses Qt's original source strings. Spanish prefers
    Qt's full catalogue, with a standard-control fallback for reduced bundles.
    """
    if language not in {"en", "es"}:
        raise ValueError("Unsupported Qt UI language")
    if app is None:
        raise ValueError("A QApplication is required")
    previous = getattr(app, "_valhalla_qt_translator", None)
    if previous is not None:
        app.removeTranslator(previous)
        previous.deleteLater()
    app._valhalla_qt_translator = None
    if language == "en":
        return None
    translator = QTranslator(app)
    for path in _translation_paths():
        if path.is_file() and translator.load(str(path)):
            break
    else:
        translator.deleteLater()
        translator = _SpanishStandardTranslator(app)
    if not app.installTranslator(translator):
        translator.deleteLater()
        LOG.warning("Qt could not install its Spanish UI translator")
        return None
    app._valhalla_qt_translator = translator
    return translator
