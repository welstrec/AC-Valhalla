import ctypes
import struct
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from capstone import Cs, CS_ARCH_X86, CS_MODE_64

from valhalla import assembly
from valhalla.engine import (GameSession, InventoryItem, Patch, PATTERNS,
                             CompatibilityError, StaleInventoryError, normalize_hash)
from valhalla.memory import MemoryAccessError


class QuantityFixture:
    def __init__(self, unique=(), maximum=2_147_483_647):
        self.unique = set(unique)
        self.maximum = maximum

    def is_unique(self, hash_id):
        return hash_id in self.unique

    def limit(self, hash_id):
        return 1 if self.is_unique(hash_id) else self.maximum


class FakeMemory:
    def __init__(self):
        self.pid, self.name, self.path = 10, "ACValhalla.exe", "fixture"
        self.identity = (10, 12345)
        self.base, self.size = 0x10000, 0x100000
        self.tainted = False
        self.data = {}
        self.freed, self.writes = [], []
        self.next_alloc = 0x90000
        self.worker_error = None
        self.worker_exit = 0
        self.alive = True
        self.closed = True

    def is_alive(self):
        return self.alive

    def is_executable(self, address):
        return self.base <= address < self.base + self.size

    def read(self, address, size):
        return bytes(self.data.get(address + i, 0) for i in range(size))

    def write(self, address, value):
        self.writes.append((address, bytes(value)))
        self.data.update({address + i: b for i, b in enumerate(value)})

    def write_code(self, address, value, expected=None):
        if expected is not None and self.read(address, len(value)) != expected:
            raise MemoryAccessError("unexpected code")
        self.write(address, value)

    def u64(self, address):
        return struct.unpack("<Q", self.read(address, 8))[0]

    def u32(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def alloc(self, size, executable=False):
        address = self.next_alloc
        self.next_alloc += 0x1000
        return address

    def free(self, address):
        self.freed.append(address)

    def run_thread(self, start, parameter=0, timeout_ms=10000):
        if self.worker_error:
            self.tainted = True
            raise self.worker_error
        self.write(parameter, struct.pack("<QQ", 1, 42))
        return self.worker_exit

    def close(self):
        return self.closed

    def scan_unique(self, pattern, **kwargs):
        if kwargs.get("cancel") and kwargs["cancel"]():
            raise MemoryAccessError("cancelled")
        if pattern == PATTERNS["stealth"]:
            self.write(0x12300, bytes.fromhex("80 78 7e 00 74 12 32 c0"))
            return 0x12300
        raise MemoryAccessError("missing fixture signature")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.memory = FakeMemory()
        self.session = GameSession(memory=self.memory)

    def inventory(self):
        vector, start, count = 0x20000, 0x30000, 1
        settings = 0x40000
        self.memory.write(start, struct.pack("<I", 5))
        self.memory.write(start + 8, struct.pack("<Q", settings))
        self.memory.write(settings + 0x10, struct.pack("<Q", 0x123456))
        self.session.features["inventory"].available = True
        self.session._resolve_inventory = Mock(return_value=(vector, start, count))
        return self.session.list_inventory()[0]

    def test_hash_input_is_exact_and_rejects_coercion(self):
        self.assertEqual(normalize_hash(" 0x123abc "), 0x123abc)
        for invalid in (True, 1.2, None, "-1", "+1", "1 2", "0", "f" * 17):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_hash(invalid)

    def test_diagnosis_is_independent_and_read_only(self):
        features = self.session.diagnose()
        self.assertTrue(features["stealth"].available)
        self.assertFalse(features["inventory"].available)
        self.assertFalse(features["shops"].available)
        self.assertEqual(self.session._patches, {})

    def test_inventory_write_resolves_and_checks_item_identity(self):
        row = self.inventory()
        result = self.session.set_quantity(row, 7)
        self.assertEqual(result.quantity, 7)
        self.assertEqual(self.memory.u32(row.address), 7)
        self.assertGreaterEqual(self.session._resolve_inventory.call_count, 6)

    def test_rations_cannot_be_edited_directly(self):
        from dataclasses import replace
        from valhalla.resources import RATIONS_HASH
        row = replace(self.inventory(), hash_id=RATIONS_HASH)
        previous_writes = list(self.memory.writes)
        with self.assertRaisesRegex(CompatibilityError, "raciones"):
            self.session.set_quantity(row, 99)
        self.assertEqual(self.memory.writes, previous_writes)

    def test_same_address_with_different_hash_is_not_written(self):
        row = self.inventory()
        self.memory.write(row.settings + 0x10, struct.pack("<Q", 999))
        with self.assertRaises(StaleInventoryError):
            self.session.set_quantity(row, 20)
        self.assertEqual(self.memory.u32(row.address), 5)

    def test_inventory_vector_relocation_blocks_stale_row(self):
        row = self.inventory()
        self.session._resolve_inventory.return_value = (0x20100, row.address, 1)
        with self.assertRaises(StaleInventoryError):
            self.session.set_quantity(row, 20)
        self.assertEqual(self.memory.u32(row.address), 5)

    def test_inventory_quantity_change_blocks_overwrite(self):
        row = self.inventory()
        self.memory.write(row.address, struct.pack("<I", 8))
        with self.assertRaises(StaleInventoryError):
            self.session.set_quantity(row, 20)

    def test_vector_aba_does_not_revalidate_old_generation(self):
        row = self.inventory()
        self.session._resolve_inventory.return_value = (0x20100, row.address, 1)
        self.session.list_inventory()
        self.session._resolve_inventory.return_value = row.vector
        self.session.list_inventory()
        with self.assertRaises(StaleInventoryError):
            self.session.set_quantity(row, 20)
        self.assertEqual(self.memory.u32(row.address), 5)

    def test_different_process_creation_blocks_old_row(self):
        row = self.inventory()
        self.memory.identity = (10, 12346)
        with self.assertRaises(StaleInventoryError):
            self.session.set_quantity(row, 20)

    def test_normal_worker_frees_only_after_completion(self):
        self.assertEqual(self.session._run("xor eax,eax; ret"), (1, 42))
        self.assertEqual(self.memory.freed, [0x91000, 0x90000])

    def test_timeout_does_not_free_worker_code_or_data(self):
        self.memory.worker_error = MemoryAccessError("pending")
        with self.assertRaises(MemoryAccessError):
            self.session._run("xor eax,eax; ret")
        self.assertTrue(self.session.tainted)
        self.assertEqual(self.memory.freed, [])

    def test_abnormal_thread_exit_taints_session(self):
        self.memory.worker_exit = 0xc0000005
        with self.assertRaises(CompatibilityError):
            self.session._run("xor eax,eax; ret")
        self.assertTrue(self.session.tainted)
        self.assertEqual(self.memory.freed, [])

    def test_failed_second_patch_restores_first(self):
        first, second = Patch(0x10000, b"abc", b"xyz"), Patch(0x20000, b"def", b"uvw")
        self.memory.write(first.address, first.original)
        self.memory.write(second.address, second.original)
        self.session.features["persistence"].available = True
        self.session._make_patches = Mock(return_value=[first, second])
        original_write = self.memory.write_code

        def fail_second(address, value, expected=None):
            if address == second.address:
                raise MemoryAccessError("fixture second write failed")
            original_write(address, value, expected)

        self.memory.write_code = fail_second
        with self.assertRaises(MemoryAccessError):
            self.session.toggle("persistence", True)
        self.assertEqual(self.memory.read(first.address, 3), b"abc")
        self.assertNotIn("persistence", self.session._patches)

    def test_restore_refuses_other_tool_modified_bytes(self):
        patch = Patch(0x10000, b"abc", b"xyz")
        self.session.features["horse"].available = True
        self.session._patches["horse"] = [patch]
        self.memory.write(patch.address, b"hij")
        with self.assertRaises(MemoryAccessError):
            self.session.toggle("horse", False)
        self.assertEqual(self.memory.read(patch.address, 3), b"hij")

    def test_close_reports_unrestored_pending_worker(self):
        self.memory.tainted = True
        self.memory.closed = False
        self.session._patches["horse"] = [Patch(0x10000, b"abc", b"xyz")]
        self.assertEqual(self.session.close(), {"restored": False, "pending": True, "tainted": True})

    def test_cancelled_diagnosis_does_not_scan(self):
        cancel = threading.Event()
        cancel.set()
        self.memory.scan_unique = Mock()
        self.session.diagnose(cancel=cancel)
        self.memory.scan_unique.assert_not_called()

    def test_function_outside_game_is_rejected(self):
        with self.assertRaises(CompatibilityError):
            self.session._validate_function(0x700000000000)

    def grant_fixture(self, policy, snapshots):
        self.session.quantity_policy = policy
        self.session.features["grant"].available = True
        self.session.features["inventory"].available = True
        self.session.list_inventory = Mock(side_effect=snapshots)
        self.session._thread_functions = Mock(return_value=(0x11000, 0x12000))
        self.session._validate_function = Mock()
        self.session._addresses["grant_call"] = 0x13000
        self.session._rip = Mock(return_value=0x50000)
        self.memory.write(0x50000, struct.pack("<Q", 0x60000))
        self.session._run = Mock(return_value=(1, 0))

    def test_equipment_edit_rejects_duplicates_but_resources_accept_large_values(self):
        row = self.inventory()
        self.session.quantity_policy = QuantityFixture([row.hash_id])
        writes = list(self.memory.writes)
        with self.assertRaisesRegex(ValueError, "0 y 1"):
            self.session.set_quantity(row, 2)
        self.assertEqual(self.memory.writes, writes)
        self.assertEqual(self.session.set_quantity(row, 1).quantity, 1)
        self.session.quantity_policy = QuantityFixture()
        current = self.session.list_inventory()[0]
        self.assertEqual(self.session.set_quantity(current, 100000).quantity, 100000)

    def test_unique_equipment_that_appears_after_preview_is_skipped_without_worker(self):
        unique, resource = 0x123456, 0x654321
        other = SimpleNamespace(hash_id=resource, quantity=5)
        owned = SimpleNamespace(hash_id=unique, quantity=1)
        self.grant_fixture(QuantityFixture([unique]), [[other], [other, owned], [other, owned]])
        progress = Mock()
        result = self.session.grant_items([unique], quantity=100000, progress=progress)
        self.assertEqual((result.requested, result.completed, result.skipped), (1, 0, 1))
        self.assertEqual(result.hashes, [])
        self.session._run.assert_not_called()
        self.session._thread_functions.assert_not_called()
        progress.assert_called_once_with(1, 1, f"{unique:016X}")

    def test_equipment_quantity_edit_cannot_create_a_second_owned_record(self):
        from dataclasses import replace
        row = self.inventory()
        self.session.quantity_policy = QuantityFixture([row.hash_id])
        self.session.list_inventory = Mock(return_value=[row, replace(row, address=row.address + 0x48, quantity=1)])
        writes = list(self.memory.writes)
        with self.assertRaisesRegex(ValueError, "otro registro"):
            self.session.set_quantity(row, 1)
        self.assertEqual(self.memory.writes, writes)
        self.assertEqual(self.session.set_quantity(row, 0).quantity, 0)

    def test_mixed_pack_uses_one_for_equipment_and_requested_quantity_for_resources(self):
        unique, resource = 0x123456, 0x654321
        before = [SimpleNamespace(hash_id=resource, quantity=5)]
        after = [SimpleNamespace(hash_id=resource, quantity=100005),
                 SimpleNamespace(hash_id=unique, quantity=1)]
        self.grant_fixture(QuantityFixture([unique]), [before, before, after])
        with patch.object(assembly, "grant_worker", return_value="fixture") as worker:
            result = self.session.grant_items([unique, resource], quantity=100000)
        self.assertEqual([(call.args[-2], call.args[-1]) for call in worker.call_args_list],
                         [(unique, 1), (resource, 100000)])
        self.assertEqual((result.completed, result.confirmed, result.adjusted), (2, 2, 1))
        self.assertEqual(result.hashes, [unique, resource])

    def test_unknown_unique_ownership_blocks_request_before_any_worker(self):
        unique, resource = 0x123456, 0x654321
        row = SimpleNamespace(hash_id=resource, quantity=5)
        for mode in ("unavailable", "initial-error", "fresh-error", "fresh-empty", "identity-changed"):
            with self.subTest(mode=mode):
                self.grant_fixture(QuantityFixture([unique]), [[row], [row]])
                if mode == "unavailable":
                    self.session.features["inventory"].available = False
                elif mode == "initial-error":
                    self.session.list_inventory.side_effect = MemoryAccessError("read failed")
                elif mode == "fresh-error":
                    self.session.list_inventory.side_effect = [[row], MemoryAccessError("read failed")]
                elif mode == "fresh-empty":
                    self.session.list_inventory.side_effect = [[row], []]
                else:
                    calls = []
                    def changed():
                        calls.append(1)
                        if len(calls) == 2:
                            self.memory.identity = (10, 12346)
                        return [row]
                    self.memory.identity = (10, 12345)
                    self.session.list_inventory.side_effect = changed
                with self.assertRaises((CompatibilityError, MemoryAccessError)):
                    self.session.grant_items([unique], quantity=9999)
                self.session._run.assert_not_called()

    def test_grant_rejects_known_total_overflow_or_confirmed_object_limit(self):
        resource = 0x654321
        for maximum, current, quantity in ((2_147_483_647, 2_147_483_640, 8),
                                            (99, 90, 10), (99, 0, 100),
                                            (2_147_483_647, 0xffffffff, 1)):
            with self.subTest(maximum=maximum, current=current, quantity=quantity):
                self.grant_fixture(QuantityFixture(maximum=maximum),
                                   [[SimpleNamespace(hash_id=resource, quantity=current)]])
                with self.assertRaises(ValueError):
                    self.session.grant_items([resource], quantity=quantity)
                self.session._run.assert_not_called()

    def test_app_requires_a_readable_nonempty_snapshot_for_stackable_grants(self):
        resource = 0x654321
        row = SimpleNamespace(hash_id=resource, quantity=5)
        for mode in ("unavailable", "read-error", "empty", "identity-changed"):
            with self.subTest(mode=mode):
                self.grant_fixture(QuantityFixture(), [[row]])
                self.memory.identity = (10, 12345)
                self.session.require_quantity_snapshot = True
                if mode == "unavailable":
                    self.session.features["inventory"].available = False
                elif mode == "read-error":
                    self.session.list_inventory.side_effect = MemoryAccessError("unreadable count")
                elif mode == "empty":
                    self.session.list_inventory.side_effect = [[]]
                else:
                    def changed():
                        self.memory.identity = (10, 12346)
                        return [row]
                    self.session.list_inventory.side_effect = changed
                with self.assertRaises(CompatibilityError):
                    self.session.grant_items([resource], quantity=100000)
                self.session._run.assert_not_called()
                self.session._thread_functions.assert_not_called()

    def test_app_allows_large_stackable_grants_after_confirming_current_quantity(self):
        resource = 0x654321
        self.grant_fixture(QuantityFixture(),
                           [[SimpleNamespace(hash_id=resource, quantity=5)],
                            [SimpleNamespace(hash_id=resource, quantity=100005)]])
        self.session.require_quantity_snapshot = True
        with patch.object(assembly, "grant_worker", return_value="fixture") as worker:
            result = self.session.grant_items([resource], quantity=100000)
        self.assertEqual(worker.call_args.args[-1], 100000)
        self.assertEqual((result.completed, result.confirmed), (1, 1))

    def test_present_zero_equipment_can_receive_one_and_cancel_does_not_grant(self):
        unique = 0x123456
        zero = SimpleNamespace(hash_id=unique, quantity=0)
        one = SimpleNamespace(hash_id=unique, quantity=1)
        self.grant_fixture(QuantityFixture([unique]), [[zero], [zero], [one]])
        with patch.object(assembly, "grant_worker", return_value="fixture") as worker:
            result = self.session.grant_items([unique], quantity=9999)
        self.assertEqual(worker.call_args.args[-1], 1)
        self.assertEqual((result.completed, result.confirmed), (1, 1))
        cancel = threading.Event()
        cancel.set()
        self.grant_fixture(QuantityFixture([unique]), [[one], [one]])
        result = self.session.grant_items([unique], cancel=cancel)
        self.assertTrue(result.cancelled)
        self.session._run.assert_not_called()


class AssemblyTests(unittest.TestCase):
    def workers(self):
        return [assembly.inventory_worker(0x140010000, 0x140020000, 0x140030000),
                assembly.grant_worker(0x140010000, 0x140020000, 0x140030000, 0x140040000, 0x123456789, 3),
                assembly.boost_worker(0x140010000, 0x140020000, 0x140030000, 0x140040000, 2.0),
                assembly.map_worker(*range(0x140010000, 0x140080000, 0x10000), 0x123456789)]

    def test_all_calls_have_shadow_space_and_aligned_stack(self):
        decoder = Cs(CS_ARCH_X86, CS_MODE_64)
        for source in self.workers():
            delta, reserved, saved = 0, 0, []
            for instruction in decoder.disasm(assembly.assemble(source), 0):
                operation, operands = instruction.mnemonic, instruction.op_str
                if operation == "push":
                    delta -= 8
                    saved.append(operands)
                elif operation == "pop":
                    self.assertEqual(saved.pop(), operands)
                    delta += 8
                elif operands.startswith("rsp, ") and operation in ("sub", "add"):
                    amount = int(operands.split(", ")[1], 0)
                    delta += amount if operation == "add" else -amount
                    reserved += -amount if operation == "add" else amount
                elif operation == "call":
                    self.assertEqual((8 + delta) % 16, 0)
                    self.assertGreaterEqual(reserved, 32)
                elif operation == "ret":
                    self.assertEqual(delta, 0)
                    self.assertEqual(saved, [])


@unittest.skipUnless(sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8,
                     "Native ABI fixture is Windows x64 only")
class NativeWorkerTests(unittest.TestCase):
    """Execute our workers ONLY inside this test process with synthetic objects.

    No game discovery, ProcessMemory, external PID, CE or original CT execution.
    """
    def setUp(self):
        self.buffers, self.pages = [], []
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
        self.kernel.VirtualAlloc.restype = ctypes.c_void_p
        self.kernel.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
        self.kernel.VirtualFree.restype = ctypes.c_int

    def tearDown(self):
        for page in self.pages:
            self.kernel.VirtualFree(page, 0, 0x8000)

    def buffer(self, size=64):
        buffer = ctypes.create_string_buffer(size)
        self.buffers.append(buffer)
        return ctypes.addressof(buffer)

    def put(self, address, value):
        ctypes.memmove(address, struct.pack("<Q", value), 8)

    def code(self, source):
        code = assembly.assemble(source)
        address = self.kernel.VirtualAlloc(None, len(code), 0x3000, 0x40)
        self.assertTrue(address)
        self.pages.append(address)
        ctypes.memmove(address, code, len(code))
        return address

    def run_worker(self, source):
        data = self.buffer()
        address = self.code(source)
        callback = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(address)
        self.assertEqual(callback(data), 0)
        return struct.unpack("<QQ", ctypes.string_at(data, 16))

    def test_inventory_worker_follows_synthetic_game_chain(self):
        a, b, c, d = self.buffer(0x1600), self.buffer(), self.buffer(0x80), self.buffer()
        vtable, holder, slots, vector = self.buffer(0xb0), self.buffer(), self.buffer(), self.buffer()
        index, index_global = self.buffer(0x50), self.buffer()
        self.put(a + 0x15f8, b)
        self.put(c + 0x70, d)
        self.put(d, vtable)
        self.put(vtable + 0xa0, self.code(f"mov rax, {holder}; ret"))
        self.put(holder, slots)
        self.put(slots, vector + 0x324)
        self.put(index_global, index)
        call1 = self.code(f"mov rax, {a}; ret")
        call2 = self.code(f"mov rax, {c}; ret")
        self.assertEqual(self.run_worker(assembly.inventory_worker(call1, call2, index_global)), (1, vector))

    def test_grant_worker_restores_flag_and_passes_seven_arguments(self):
        record, world, world_global = self.buffer(80), self.buffer(0x490), self.buffer()
        self.put(world_global, world)
        self.put(world + 0x480, 0x1234)
        noop = self.code("ret")
        call = self.code(f"""
            mov rax, {record}
            mov [rax], rcx
            mov [rax+8], rdx
            mov [rax+16], r8
            mov [rax+24], r9
            mov r10, [rsp+0x28]
            mov [rax+32], r10
            mov r10, [rsp+0x30]
            mov [rax+40], r10
            mov r10, [rsp+0x38]
            mov [rax+48], r10
            mov [rax+56], rsp
            mov rax, 77
            ret
        """)
        result = self.run_worker(assembly.grant_worker(call, world_global, noop, noop, 0x123456789, 7))
        values = struct.unpack("<8Q", ctypes.string_at(record, 64))
        self.assertEqual(values[:7], (0x123456789, 7, 0, 2, 3, 1, 2))
        self.assertEqual(values[7] % 16, 8)
        self.assertEqual(result, (1, 77))
        self.assertEqual(struct.unpack("<Q", ctypes.string_at(world + 0x480, 8))[0], 0x1234)

    def test_boost_worker_uses_xmm_argument_and_both_kinds(self):
        record, owner, owner_global = self.buffer(96), self.buffer(0x400), self.buffer()
        self.put(owner_global, owner)
        noop = self.code("ret")
        call = self.code(f"""
            mov r10, {record}
            inc qword ptr [r10]
            mov r11d, edx
            shl r11, 5
            lea r10, [r10+r11+8]
            mov [r10], rcx
            movd eax, xmm2
            mov [r10+8], eax
            mov rax, [rsp+0x28]
            mov [r10+16], rax
            mov rax, [rsp+0x30]
            mov [r10+24], rax
            ret
        """)
        self.assertEqual(self.run_worker(assembly.boost_worker(call, owner_global, noop, noop, 2.5))[0], 1)
        self.assertEqual(struct.unpack("<Q", ctypes.string_at(record, 8))[0], 2)
        for offset in (8, 40):
            address, multiplier, arg5, arg6 = struct.unpack("<Qf4xQQ", ctypes.string_at(record + offset, 32))
            self.assertEqual((address, multiplier, arg5, arg6), (owner + 0x3f0, 2.5, 0, 1))

    def test_map_worker_checks_handle_and_releases_after_reveal(self):
        environment, descriptor, handle, record = self.buffer(), self.buffer(), self.buffer(), self.buffer()
        self.put(environment, 1)
        self.put(handle, descriptor)
        self.put(handle + 8, 0x80000000)
        get_world = self.code("mov eax, 1; ret")
        get_module = self.code("mov eax, 123; ret")
        get_item = self.code(f"mov rax, {record}; mov [rax], rdx; mov rax, {handle}; ret")
        reveal = self.code(f"mov rax, {record}; mov [rax+8], rcx; ret")
        release = self.code(f"mov rax, {record}; mov [rax+16], rcx; ret")
        result = self.run_worker(assembly.map_worker(environment, get_world, get_module, 12345,
                                                      get_item, reveal, release, 0x123456789))
        self.assertEqual(result[0], 1)
        self.assertEqual(struct.unpack("<QQQ", ctypes.string_at(record, 24)),
                         (0x123456789, descriptor, handle))


if __name__ == "__main__":
    unittest.main()
