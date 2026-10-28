"""Execute a reviewed request list through the existing serialized game API."""
from dataclasses import dataclass, field


@dataclass
class QueueResult:
    requested: int
    attempted: list[str] = field(default_factory=list)
    completed: int = 0
    confirmed: int = 0
    cancelled: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    inventory: list | None = None
    skipped: int = 0
    adjusted: int = 0


def execute_requests(session, entries, cancel=None, progress=None):
    entries = tuple(entries)
    result = QueueResult(len(entries))
    for number, entry in enumerate(entries, 1):
        if cancel and cancel.is_set():
            result.cancelled = True
            break
        if session.tainted:
            result.error = "La sesión quedó bloqueada. Reinicia el juego antes de continuar."
            break
        # Even an uncertain outcome consumes this queue entry. The user can
        # deliberately re-add it after inspecting the game; there is no retry.
        result.attempted.append(entry.hash_id)
        try:
            response = session.grant_items([entry.hash_id], quantity=entry.quantity, cancel=cancel)
            result.completed += response.completed
            result.confirmed += response.confirmed
            result.skipped += getattr(response, "skipped", 0)
            result.adjusted += getattr(response, "adjusted", 0)
            result.warnings.extend(response.warnings)
        except (RuntimeError, OSError, ValueError) as exc:
            result.error = f"{entry.label}: {exc}"
            result.warnings.append("Se detuvo la lista. La solicitud con resultado incierto se retiró para evitar repetirla accidentalmente.")
            break
        if progress:
            progress(number, len(entries), entry.label)
        if response.cancelled:
            result.cancelled = True
            break
    if not session.tainted and session.features.get("inventory") and session.features["inventory"].available:
        try:
            result.inventory = session.list_inventory()
        except (RuntimeError, OSError, ValueError) as exc:
            result.warnings.append("Actualización de inventario pendiente: " + str(exc))
    return result
