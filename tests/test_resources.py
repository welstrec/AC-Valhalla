"""Resource recovery policy tests with inert inventory/session fixtures."""
from dataclasses import dataclass
import threading
from types import SimpleNamespace
import unittest

from valhalla.catalog import Catalog
from valhalla.resources import BASE_NAMES, RATIONS_HASH, ResourceAssistant


@dataclass(frozen=True)
class Row:
    hash_id: int
    quantity: int


class FakeSession:
    def __init__(self, rows=None):
        self.identity = (123, 456789)
        self.tainted = False
        self.rows = list(rows if rows is not None else [Row(0xABCDEF123, 1)])
        self.reads = 0
        self.read_hook = None
        self.read_error = None
        self.grants = []
        self.grant_hook = None
        self.grant_error = None
        self.appear = True

    def list_inventory(self):
        self.reads += 1
        if self.read_hook:
            self.read_hook(self, self.reads)
        if self.read_error:
            raise self.read_error
        return list(self.rows)

    def grant_items(self, hashes, quantity=1, cancel=None):
        self.grants.append((tuple(hashes), quantity))
        if self.grant_hook:
            self.grant_hook(self, hashes, quantity)
        if self.grant_error:
            raise self.grant_error
        if self.appear:
            self.rows.extend(Row(hash_id, quantity) for hash_id in hashes)
        return SimpleNamespace(warnings=[])


class ResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.load()

    def setUp(self):
        self.assistant = ResourceAssistant(self.catalog)
        self.target = next(iter(BASE_NAMES))

    def missing_one(self):
        return FakeSession([Row(h, 9) for h in BASE_NAMES if h != self.target])

    def test_only_thirteen_base_resources_are_automatic(self):
        automatic = {rule.hash_id for rule in self.assistant.rules if rule.automatic}
        self.assertEqual(len(automatic), 13)
        self.assertEqual(automatic, set(BASE_NAMES))
        excluded_packs = ("99486", "99488", "99490", "99492", "233799", "99178", "99744")
        for pack_id in excluded_packs:
            self.assertTrue(automatic.isdisjoint(int(h, 16) for h in self.catalog.by_pack[pack_id].hashes))
        self.assertNotIn(0x000001E9BC55CE53, automatic)
        self.assertNotIn(0x000001E82D13E204, automatic)
        self.assertNotIn(RATIONS_HASH, automatic)

    def test_absence_is_unknown_not_a_confirmed_zero(self):
        session = FakeSession()
        statuses = self.assistant.inspect(session)
        silver = next(s for s in statuses if s.rule.hash_id == self.target)
        self.assertIsNone(silver.quantity)
        self.assertTrue(silver.can_initialize)
        self.assertIn("no confirma", silver.detail.casefold())
        texts = next(s for s in statuses if s.rule.hash_id == 0x000001C78D91433C)
        self.assertFalse(texts.can_initialize)
        self.assertIn("Azar", texts.detail)
        self.assertEqual(session.grants, [])

    def test_present_zero_is_readable_and_is_not_initialized(self):
        session = FakeSession([Row(h, 0) for h in BASE_NAMES])
        statuses = self.assistant.inspect(session)
        self.assertTrue(all(not s.can_initialize for s in statuses if s.rule.automatic))
        result = self.assistant.repair_missing_base(session)
        self.assertEqual(result.requested, 0)
        self.assertEqual(session.grants, [])

    def test_missing_resource_gets_one_unit_and_fresh_verification(self):
        session = self.missing_one()
        before_reads = session.reads
        result = self.assistant.repair_missing_base(session)
        self.assertEqual(session.grants, [((self.target,), 1)])
        self.assertEqual(result.requested, 1)
        self.assertEqual(result.initialized, 1)
        self.assertGreaterEqual(session.reads - before_reads, 3)
        status = next(s for s in result.statuses if s.rule.hash_id == self.target)
        self.assertEqual(status.quantity, 1)
        self.assertFalse(status.can_initialize)

    def test_unconfirmed_request_is_not_repeated_for_same_instance(self):
        session = self.missing_one()
        session.appear = False
        first = self.assistant.repair_missing_base(session)
        second = self.assistant.repair_missing_base(session)
        self.assertEqual((first.requested, first.initialized), (1, 0))
        self.assertEqual(second.requested, 0)
        self.assertEqual(len(session.grants), 1)
        status = next(s for s in second.statuses if s.rule.hash_id == self.target)
        self.assertEqual(status.state, "Solicitud sin confirmar")
        self.assertFalse(status.can_initialize)

    def test_failed_request_still_consumes_single_attempt(self):
        session = self.missing_one()
        session.grant_error = RuntimeError("No confirmado")
        first = self.assistant.repair_missing_base(session)
        second = self.assistant.repair_missing_base(session)
        self.assertEqual(first.initialized, 0)
        self.assertTrue(first.warnings)
        self.assertEqual(second.requested, 0)
        self.assertEqual(len(session.grants), 1)

    def test_read_error_prevents_any_grant_or_attempt(self):
        session = FakeSession()
        session.read_error = RuntimeError("Carga una partida")
        with self.assertRaises(RuntimeError):
            self.assistant.repair_missing_base(session)
        self.assertEqual(session.grants, [])
        self.assertEqual(self.assistant.attempts, set())

    def test_empty_snapshot_prevents_any_grant(self):
        session = FakeSession([])
        with self.assertRaises(RuntimeError):
            self.assistant.repair_missing_base(session)
        self.assertEqual(session.grants, [])

    def test_reread_avoids_adding_resource_that_appeared_during_gameplay(self):
        session = self.missing_one()
        def appear_on_reread(target, number):
            if number == 2:
                target.rows.append(Row(self.target, 1))
        session.read_hook = appear_on_reread
        result = self.assistant.repair_missing_base(session)
        self.assertEqual(result.requested, 0)
        self.assertEqual(result.initialized, 0)
        self.assertEqual(session.grants, [])

    def test_identity_change_during_reread_prevents_first_grant(self):
        session = self.missing_one()
        def change_instance(target, number):
            if number == 2:
                target.identity = (123, 987654)
        session.read_hook = change_instance
        with self.assertRaises(RuntimeError):
            self.assistant.repair_missing_base(session)
        self.assertEqual(session.grants, [])

    def test_empty_reread_prevents_grant_after_valid_initial_snapshot(self):
        session = self.missing_one()
        session.read_hook = lambda target, number: target.rows.clear() if number == 2 else None
        with self.assertRaises(RuntimeError):
            self.assistant.repair_missing_base(session)
        self.assertEqual(session.grants, [])

    def test_tainted_session_prevents_any_grant(self):
        session = self.missing_one()
        session.tainted = True
        with self.assertRaises(RuntimeError):
            self.assistant.repair_missing_base(session)
        self.assertEqual(session.grants, [])

    def test_taint_during_request_stops_later_resources_and_claims_no_verification(self):
        session = FakeSession()
        session.grant_hook = lambda target, *_: setattr(target, "tainted", True)
        session.grant_error = RuntimeError("Worker pendiente")
        result = self.assistant.repair_missing_base(session)
        self.assertEqual(len(session.grants), 1)
        self.assertEqual(result.initialized, 0)
        self.assertTrue(result.warnings)

    def test_cancel_before_or_between_resources(self):
        cancel = threading.Event()
        cancel.set()
        session = FakeSession()
        result = self.assistant.repair_missing_base(session, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(session.grants, [])
        cancel.clear()
        session.grant_hook = lambda *_: cancel.set()
        result = self.assistant.repair_missing_base(session, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.requested, 1)
        self.assertEqual(len(session.grants), 1)

    def test_new_process_identity_allows_a_new_single_attempt(self):
        session = self.missing_one()
        session.appear = False
        self.assistant.repair_missing_base(session)
        session.identity = (999, 11111)
        result = self.assistant.repair_missing_base(session)
        self.assertEqual(result.requested, 1)
        self.assertEqual(len(session.grants), 2)

    def test_final_snapshot_from_another_instance_is_never_confirmed(self):
        session = self.missing_one()
        def change_final(target, number):
            if number == 3:
                target.identity = (999, 11111)
        session.read_hook = change_final
        with self.assertRaisesRegex(RuntimeError, "comprobación final"):
            self.assistant.repair_missing_base(session)
        self.assertEqual(len(session.grants), 1)

    def test_final_snapshot_after_taint_is_never_confirmed(self):
        session = self.missing_one()
        session.read_hook = lambda target, number: setattr(target, "tainted", True) if number == 3 else None
        with self.assertRaisesRegex(RuntimeError, "comprobación final"):
            self.assistant.repair_missing_base(session)
        self.assertEqual(len(session.grants), 1)


if __name__ == "__main__":
    unittest.main()
