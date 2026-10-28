"""Reviewed queue execution with inert game/session fixtures only."""
import threading
from types import SimpleNamespace
import unittest

from valhalla.library import QueueEntry, RequestQueue
from valhalla.requests import execute_requests


IRON = "000001AF7031F3F9"
LEATHER = "000001AF7031F403"
SILVER = "0000001BFDB9F655"


def response(completed=1, confirmed=1, cancelled=False, warnings=()):
    return SimpleNamespace(completed=completed, confirmed=confirmed,
                           cancelled=cancelled, warnings=list(warnings))


class FakeSession:
    def __init__(self, inventory_available=True):
        self.features = {"inventory": SimpleNamespace(available=inventory_available)}
        self.tainted = False
        self.calls = []
        self.reads = 0
        self.inventory = [SimpleNamespace(hash_id=int(IRON, 16), quantity=10)]
        self.read_error = None
        self.grant_errors = {}
        self.responses = []
        self.after_grant = None

    def grant_items(self, hashes, quantity=1, cancel=None):
        self.calls.append((tuple(hashes), quantity))
        if hashes[0] in self.grant_errors:
            raise self.grant_errors[hashes[0]]
        result = self.responses.pop(0) if self.responses else response()
        if self.after_grant:
            self.after_grant(self, len(self.calls))
        return result

    def list_inventory(self):
        self.reads += 1
        if self.read_error:
            raise self.read_error
        return list(self.inventory)


class ReviewedRequestTests(unittest.TestCase):
    def entries(self):
        return [QueueEntry(IRON, 3, "Iron"), QueueEntry(LEATHER, 7, "Leather")]

    def test_each_entry_keeps_quantity_and_refreshes_inventory(self):
        session, progress = FakeSession(), []
        session.responses = [response(confirmed=0, warnings=["Unique item"]), response()]
        result = execute_requests(session, self.entries(), progress=lambda *args: progress.append(args))
        self.assertEqual(session.calls, [((IRON,), 3), ((LEATHER,), 7)])
        self.assertEqual(result.requested, 2)
        self.assertEqual(result.attempted, [IRON, LEATHER])
        self.assertEqual((result.completed, result.confirmed), (2, 1))
        self.assertEqual(result.warnings, ["Unique item"])
        self.assertEqual(result.inventory, session.inventory)
        self.assertEqual(session.reads, 1)
        self.assertEqual(progress, [(1, 2, "Iron"), (2, 2, "Leather")])

    def test_owned_equipment_and_adjusted_quantities_are_reported_without_retry(self):
        session = FakeSession()
        owned, adjusted = response(completed=0, confirmed=0), response()
        owned.skipped, owned.adjusted = 1, 0
        adjusted.skipped, adjusted.adjusted = 0, 1
        session.responses = [owned, adjusted]
        result = execute_requests(session, self.entries())
        self.assertEqual((result.completed, result.confirmed, result.skipped, result.adjusted), (1, 1, 1, 1))
        self.assertEqual(result.attempted, [IRON, LEATHER])
        self.assertEqual(len(session.calls), 2)

    def test_input_is_snapshotted_before_game_callbacks_mutate_review_list(self):
        session, entries = FakeSession(), self.entries()
        session.after_grant = lambda *_: entries.append(QueueEntry(SILVER, 99, "Added later"))
        result = execute_requests(session, entries)
        self.assertEqual(len(entries), 4)
        self.assertEqual(result.requested, 2)
        self.assertEqual(result.attempted, [IRON, LEATHER])
        self.assertEqual(len(session.calls), 2)

    def test_cancellation_before_and_between_requests_preserves_unsent_entries(self):
        for cancel_before in (True, False):
            with self.subTest(cancel_before=cancel_before):
                session, cancel = FakeSession(), threading.Event()
                if cancel_before:
                    cancel.set()
                else:
                    session.after_grant = lambda *_: cancel.set()
                result = execute_requests(session, self.entries(), cancel=cancel)
                expected = [] if cancel_before else [IRON]
                self.assertEqual(result.attempted, expected)
                self.assertEqual(len(session.calls), len(expected))
                self.assertTrue(result.cancelled)
                self.assertEqual([e.hash_id for e in self.entries() if e.hash_id not in result.attempted],
                                 [IRON, LEATHER] if cancel_before else [LEATHER])

    def test_game_cancelled_response_stops_before_next_request(self):
        session = FakeSession()
        session.responses = [response(completed=0, confirmed=0, cancelled=True)]
        result = execute_requests(session, self.entries())
        self.assertEqual(result.attempted, [IRON])
        self.assertEqual(result.completed, 0)
        self.assertTrue(result.cancelled)
        self.assertEqual(len(session.calls), 1)

    def test_uncertain_failure_is_consumed_once_and_later_entries_remain_reviewable(self):
        session, queue = FakeSession(), RequestQueue()
        queue.add([IRON], 3, "Iron")
        queue.add([LEATHER], 7, "Leather")
        queue.add([SILVER], 1, "Silver")
        session.grant_errors[LEATHER] = RuntimeError("uncertain worker result")
        result = execute_requests(session, queue.entries)
        self.assertEqual(result.attempted, [IRON, LEATHER])
        self.assertEqual(session.calls, [((IRON,), 3), ((LEATHER,), 7)])
        self.assertEqual(result.completed, 1)
        self.assertIn("uncertain worker result", result.error)
        self.assertTrue(result.warnings)
        for hash_id in result.attempted:
            queue.remove(hash_id)
        self.assertEqual([entry.hash_id for entry in queue.entries], [SILVER])

    def test_tainted_session_never_consumes_a_not_submitted_entry_or_reads_inventory(self):
        for initially_tainted in (True, False):
            with self.subTest(initially_tainted=initially_tainted):
                session = FakeSession()
                if initially_tainted:
                    session.tainted = True
                else:
                    session.after_grant = lambda target, _: setattr(target, "tainted", True)
                result = execute_requests(session, self.entries())
                expected = [] if initially_tainted else [IRON]
                self.assertEqual(result.attempted, expected)
                self.assertEqual(len(session.calls), len(expected))
                self.assertEqual(session.reads, 0)
                self.assertIsNone(result.inventory)
                self.assertTrue(result.error or result.warnings)

    def test_final_read_errors_preserve_consumed_requests_and_explain_missing_refresh(self):
        for failure in (RuntimeError("not loaded"), ValueError("invalid range"), OSError("read failed")):
            with self.subTest(failure=type(failure).__name__):
                session = FakeSession()
                session.read_error = failure
                result = execute_requests(session, self.entries())
                self.assertEqual(result.attempted, [IRON, LEATHER])
                self.assertEqual(result.completed, 2)
                self.assertIsNone(result.inventory)
                self.assertTrue(any(str(failure) in warning for warning in result.warnings))
                self.assertEqual(len(session.calls), 2)

    def test_inventory_unavailable_or_empty_feature_map_does_not_probe_it(self):
        for absent in (True, False):
            with self.subTest(absent=absent):
                session = FakeSession(inventory_available=False)
                if absent:
                    session.features = {}
                result = execute_requests(session, self.entries())
                self.assertEqual(result.completed, 2)
                self.assertEqual(session.reads, 0)
                self.assertIsNone(result.inventory)


if __name__ == "__main__":
    unittest.main()
