"""Bounded assistance for the missing resource records described as ?? in CE.

An absent inventory entry is never interpreted as a confirmed zero balance.
Only the explicit base-game allowlist may receive a single bootstrap request.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RATIONS_HASH = 0x000001AF7907BA3F
BASE_NAMES = {
    0x0000001BFDB9F655: "Plata",
    0x000001AF7031F403: "Cuero",
    0x000001AF7031F3F9: "Hierro",
    0x000001C7BCD89F8E: "Titanio",
    0x000001C7BCD89F93: "Tela",
    0x000001AF70363CAF: "Lingote de carbono",
    0x000001AF70363CB0: "Lingote de níquel",
    0x000001AF70363CB1: "Lingote de tungsteno",
    0x00000183257CB6A1: "Suministros",
    0x00000183257CB6A2: "Materias primas",
    0x00000043E344147B: "Flechas de cazador",
    0x000001CAC79270DF: "Flechas de arco ligero",
    0x000001CAC79270D8: "Flechas de depredador",
}


@dataclass(frozen=True)
class ResourceRule:
    hash_id: int
    name: str
    group: str
    automatic: bool = False
    requirement: str = ""


@dataclass(frozen=True)
class ResourceStatus:
    rule: ResourceRule
    quantity: int | None
    state: str
    detail: str
    can_initialize: bool


@dataclass
class ResourceRepairResult:
    statuses: list[ResourceStatus] = field(default_factory=list)
    inventory: list = field(default_factory=list)
    requested: int = 0
    initialized: int = 0
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)


def _cancelled(cancel):
    return bool(cancel and cancel.is_set())


class ResourceAssistant:
    def __init__(self, catalog):
        self.catalog = catalog
        self.attempts: set[tuple[tuple, int]] = set()
        self.rules = [ResourceRule(h, name, "Juego base", True) for h, name in BASE_NAMES.items()]
        excluded = set(BASE_NAMES)
        groups = [
            ("99295", "Dawn of Ragnarök", "Puede requerir contenido o progreso de Dawn of Ragnarök."),
            ("99486", "Wrath of the Druids", "Según la guía de la tabla, el comercio de Azar debe estar iniciado para materiales como Texts."),
            ("99488", "Incursiones", "Puede requerir haber accedido a las incursiones y a su contexto de inventario."),
            ("99490", "Siege of Paris", "Puede requerir contenido o progreso de The Siege of Paris."),
            ("99492", "Desafíos", "Puede requerir haber accedido a los desafíos correspondientes."),
            ("233799", "Dawn of Ragnarök", "Puede requerir contenido o progreso de Dawn of Ragnarök."),
            ("99178", "Festivales", "Puede depender de un evento o festival y de su disponibilidad."),
            ("99744", "Forgotten Saga", "Puede requerir haber accedido a The Forgotten Saga."),
        ]
        for pack_id, group, requirement in groups:
            pack = catalog.by_pack.get(pack_id)
            if not pack:
                continue
            for value in pack.hashes:
                hash_id = int(value, 16)
                if hash_id not in excluded:
                    excluded.add(hash_id)
                    self.rules.append(ResourceRule(hash_id, catalog.by_hash[value].name, group, False, requirement))
        self.rules.append(ResourceRule(RATIONS_HASH, "Raciones", "Tratamiento especial", False,
            "La tabla indica que no se editen directamente. Su pack se titula cinco raciones, pero solicita 50 unidades internas; la conversión necesita validación."))

    def inspect(self, session, inventory=None):
        rows = session.list_inventory() if inventory is None else inventory
        totals = {}
        for item in rows:
            totals[item.hash_id] = totals.get(item.hash_id, 0) + item.quantity
        statuses = []
        for rule in self.rules:
            quantity = totals.get(rule.hash_id)
            attempted = (session.identity, rule.hash_id) in self.attempts
            if quantity is not None:
                state = "Disponible" if rule.hash_id != RATIONS_HASH else "Tratamiento especial"
                detail = "Cantidad leída del inventario actual." if rule.hash_id != RATIONS_HASH else rule.requirement
            elif attempted:
                state = "Solicitud sin confirmar"
                detail = "Ya se solicitó una unidad en esta instancia. No se repetirá automáticamente."
            elif rule.automatic:
                state = "Sin registro"
                detail = "No se localizó un registro. Esto no confirma que la cantidad sea cero."
            else:
                state = "Requiere comprobación"
                detail = rule.requirement + " No se desbloquea automáticamente."
            statuses.append(ResourceStatus(rule, quantity, state, detail,
                rule.automatic and quantity is None and not attempted))
        return statuses

    def repair_missing_base(self, session, cancel=None, progress=None):
        # A failed read aborts before any write. Do not bootstrap while a menu,
        # incompatible layout or transient error prevents a valid snapshot.
        inventory = session.list_inventory()
        if not inventory:
            raise RuntimeError("No se pudo confirmar una partida con inventario. Carga una partida y vuelve a comprobar; no se añadió ningún recurso.")
        identity = session.identity
        candidates = [s.rule for s in self.inspect(session, inventory) if s.can_initialize]
        result = ResourceRepairResult(inventory=inventory)
        for number, rule in enumerate(candidates, 1):
            if _cancelled(cancel):
                result.cancelled = True
                break
            if session.identity != identity or session.tainted:
                raise RuntimeError("La sesión cambió o quedó bloqueada; no se enviarán más recursos.")
            # Re-read immediately before each request: never duplicate a resource
            # which appeared through normal gameplay since the initial snapshot.
            current = session.list_inventory()
            if not current or session.identity != identity or session.tainted:
                raise RuntimeError("La lectura ya no confirma la misma partida. Se detuvo la reparación antes de solicitar otro recurso.")
            if any(item.hash_id == rule.hash_id for item in current):
                continue
            self.attempts.add((identity, rule.hash_id))
            result.requested += 1
            try:
                response = session.grant_items([rule.hash_id], quantity=1, cancel=cancel)
                result.warnings.extend(response.warnings)
            except (RuntimeError, OSError, ValueError) as exc:
                result.warnings.append(f"{rule.name}: {exc}. No se reintentará automáticamente.")
                if session.tainted:
                    break
            if progress:
                progress(number, len(candidates), "Comprobando " + rule.name)
        if not session.tainted and session.identity == identity:
            # A final fresh read is the only basis for claiming initialization.
            result.inventory = session.list_inventory()
            if session.identity != identity or session.tainted:
                raise RuntimeError("La sesión cambió durante la comprobación final. No se confirma la reparación; vuelve a conectar a una partida válida.")
            after = {item.hash_id for item in result.inventory if item.quantity > 0}
            result.initialized = sum(rule.hash_id in after and (identity, rule.hash_id) in self.attempts for rule in candidates)
            result.statuses = self.inspect(session, result.inventory)
        else:
            result.warnings.append("No se pudo verificar el inventario final. Reinicia el juego antes de continuar.")
        return result
