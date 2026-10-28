"""Memory boundary and lifecycle tests using a fake WinAPI, never the game."""
import ctypes
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from valhalla import memory as M


class FakeKernel:
    def __init__(self, data=b"\x00" * 128):
        self.base = 0x10000
        self.data = bytearray(data)
        self.alive = True
        self.created = 123456
        self.thread_wait = M.WAIT_OBJECT_0
        self.closed = []
        self.freed = []
        self.protections = []
        self.flushed = []
        self.corrupt_write = False
        self.restore_fail = False
        self.fail_write = False
        self.thread_calls = 0

    def WaitForSingleObject(self, handle, _timeout):
        if handle == 11:
            return M.WAIT_TIMEOUT if self.alive else M.WAIT_OBJECT_0
        return self.thread_wait

    def GetProcessTimes(self, _handle, creation, *_others):
        creation._obj.dwLowDateTime = self.created & 0xFFFFFFFF
        creation._obj.dwHighDateTime = self.created >> 32
        return True

    def ReadProcessMemory(self, _handle, address, buffer, size, transferred):
        offset = address - self.base
        if offset < 0 or offset + size > len(self.data):
            return False
        ctypes.memmove(buffer, bytes(self.data[offset:offset + size]), size)
        transferred._obj.value = size
        return True

    def WriteProcessMemory(self, _handle, address, buffer, size, transferred):
        if self.fail_write:
            return False
        offset = address - self.base
        if offset < 0 or offset + size > len(self.data):
            return False
        self.data[offset:offset + size] = ctypes.string_at(buffer, size)
        if self.corrupt_write:
            self.data[offset] ^= 1
        transferred._obj.value = size
        return True

    def VirtualProtectEx(self, _handle, address, size, protection, previous):
        self.protections.append((address, size, protection))
        previous._obj.value = 0x20
        return not (self.restore_fail and protection != M.PAGE_EXECUTE_READWRITE)

    def FlushInstructionCache(self, _handle, address, size):
        self.flushed.append((address, size))
        return True

    def VirtualFreeEx(self, _handle, address, _size, _kind):
        self.freed.append(address)
        return True

    def CreateRemoteThread(self, *_args):
        self.thread_calls += 1
        return 22

    def GetExitCodeThread(self, _handle, code):
        code._obj.value = 7
        return True

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


def fake_memory(kernel):
    mem = M.ProcessMemory.__new__(M.ProcessMemory)
    mem.pid, mem.name, mem.path = 123, "ACValhalla.exe", "C:\\game\\ACValhalla.exe"
    mem.base, mem.size = kernel.base, len(kernel.data)
    mem.identity = (123, kernel.created)
    mem.tainted = False
    mem._closing = False
    mem._handle = 11
    mem._threads = set()
    mem._allocations = {}
    mem._region = lambda addr: SimpleNamespace(
        BaseAddress=kernel.base, RegionSize=len(kernel.data),
        State=M.MEM_COMMIT, Protect=0x20,
    )
    return mem


class PatternTests(unittest.TestCase):
    def test_nibbles_wildcard_newline_and_regex_metacharacters(self):
        data = bytes.fromhex("48 3F A0 0A 2E 5B 48 30 F0 FF 2E 5B")
        self.assertEqual(M.find_matches(data, "48 3? ?0 * 2E 5B"), [0, 6])
        self.assertEqual(M.find_matches(b"\x48\x0a\xff", "48 ?? ?"), [0])

    def test_compact_hex_and_overlapping_hits(self):
        self.assertEqual(M.find_matches(b"\xaa\xaa\xaa", "AAAA", 0x10000), [0x10000, 0x10001])
        self.assertEqual(M.find_matches(bytes.fromhex("48 3F B0"), "483??0"), [0])

    def test_invalid_patterns_fail_closed(self):
        for pattern in ("", "  ", "?? * ?", "GG", "F", "4", "488", "0x48", "48 **", None):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                M.compile_pattern(pattern)

    def test_every_nibble_value(self):
        data = bytes(range(256))
        self.assertEqual(M.find_matches(data, "A?"), list(range(0xA0, 0xB0)))
        self.assertEqual(M.find_matches(data, "?B"), list(range(0x0B, 256, 16)))


class RangeTests(unittest.TestCase):
    def test_valid_bounds(self):
        M.validate_range(M.MIN_ADDRESS, 1)
        M.validate_range(M.MAX_ADDRESS, 1)

    def test_reject_invalid_bounds(self):
        for address, size in ((0, 1), (-1, 4), (True, 1), (M.MIN_ADDRESS, True),
                              (M.MIN_ADDRESS, 0), (M.MIN_ADDRESS, -1),
                              (M.MAX_ADDRESS, 2), (M.MIN_ADDRESS, M.MAX_TRANSFER + 1)):
            with self.subTest(address=address, size=size), self.assertRaises(ValueError):
                M.validate_range(address, size)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.kernel = FakeKernel()
        self.native_patch = patch.object(M, "_kernel", self.kernel)
        self.native_patch.start()
        self.mem = fake_memory(self.kernel)
        self.addCleanup(self.native_patch.stop)
        self.addCleanup(M._QUARANTINE.clear)
        self.addCleanup(M._TAINTED_IDENTITIES.clear)

    def test_read_write_and_identity_change(self):
        self.mem.write(self.mem.base, b"test")
        self.assertEqual(self.mem.read(self.mem.base, 4), b"test")
        self.kernel.created += 1
        with self.assertRaises(M.MemoryAccessError):
            self.mem.read(self.mem.base, 1)

    def test_write_validation_and_readback(self):
        for data in ("text", 12, None):
            with self.assertRaises(TypeError):
                self.mem.write(self.mem.base, data)
        with self.assertRaises(ValueError):
            self.mem.write(self.mem.base, b"")
        self.kernel.corrupt_write = True
        with self.assertRaises(M.MemoryAccessError):
            self.mem.write(self.mem.base, b"abc")

    def test_code_expected_bytes_and_restore(self):
        with self.assertRaises(M.MemoryAccessError):
            self.mem.write_code(self.mem.base, b"\x90", expected=b"\xFF")
        self.assertEqual(self.kernel.protections, [])
        self.mem.write_code(self.mem.base, b"\x90", expected=b"\x00")
        self.assertEqual(self.kernel.data[0], 0x90)
        self.assertEqual(self.kernel.protections[-1][-1], 0x20)
        self.assertEqual(self.kernel.flushed, [(self.mem.base, 1)])

    def test_restore_even_on_failed_write(self):
        self.kernel.fail_write = True
        with self.assertRaises(M.MemoryAccessError):
            self.mem.write_code(self.mem.base, b"\x90")
        self.assertEqual(self.kernel.protections[-1][-1], 0x20)

    def test_partial_code_write_rolls_back_verified_original(self):
        original = bytes(self.kernel.data[:4])
        native_write = self.kernel.WriteProcessMemory
        calls = []

        def partial_first(handle, address, buffer, size, transferred):
            calls.append(1)
            if len(calls) == 1:
                self.kernel.data[0] = 0xCC
                transferred._obj.value = 1
                return False
            return native_write(handle, address, buffer, size, transferred)

        self.kernel.WriteProcessMemory = partial_first
        with self.assertRaises(M.MemoryAccessError):
            self.mem.write_code(self.mem.base, b"\x90" * 4)
        self.assertEqual(bytes(self.kernel.data[:4]), original)
        self.assertFalse(self.mem.tainted)

    def test_executable_address_check(self):
        self.assertTrue(self.mem.is_executable(self.mem.base))
        self.assertFalse(self.mem.is_executable(0))
        self.mem._region = lambda addr: SimpleNamespace(State=M.MEM_COMMIT, Protect=0x04)
        self.assertFalse(self.mem.is_executable(self.mem.base))

    def test_restore_failure_taints(self):
        self.kernel.restore_fail = True
        with self.assertRaises(M.TargetTaintedError):
            self.mem.write_code(self.mem.base, b"\x90")
        self.assertTrue(self.mem.tainted)
        self.assertIn(self.mem.identity, M._TAINTED_IDENTITIES)
        with self.assertRaises(M.TargetTaintedError):
            self.mem.read(self.mem.base, 1)

    def test_rip_signed_displacement(self):
        self.kernel.data[3:7] = struct.pack("<i", -7)
        self.assertEqual(self.mem.resolve_rip(self.mem.base), self.mem.base)

    def test_scan_across_chunk_boundaries_and_unique_failure(self):
        self.kernel.data[6:10] = bytes.fromhex("48 3F A0 90")
        with patch.object(M, "SCAN_CHUNK", 8):
            self.assertEqual(self.mem.scan("48 3? ?0 90"), [self.mem.base + 6])
        self.assertEqual(self.mem.scan_unique("48 3? ?0 90"), self.mem.base + 6)
        with self.assertRaises(M.MemoryAccessError):
            self.mem.scan_unique("00 00")
        with self.assertRaises(M.MemoryAccessError):
            self.mem.scan_unique("FA FB FC")

    def test_scan_cancel(self):
        with self.assertRaises(M.ScanCancelled):
            self.mem.scan("90", cancel=lambda: True)

    def test_thread_success_closes_handle(self):
        self.assertEqual(self.mem.run_thread(self.mem.base), 7)
        self.assertEqual(self.kernel.closed, [22])
        self.assertEqual(self.mem._threads, set())

    def test_timeout_retains_worker_and_allocations_until_signaled(self):
        self.kernel.thread_wait = M.WAIT_TIMEOUT
        self.mem._allocations[self.mem.base] = 128
        with self.assertRaises(M.TargetTaintedError):
            self.mem.run_thread(self.mem.base, timeout_ms=1)
        self.assertTrue(self.mem.tainted)
        self.assertEqual(self.mem._threads, {22})
        with self.assertRaises(M.TargetTaintedError):
            self.mem.free(self.mem.base)
        with self.assertRaises(M.TargetTaintedError):
            self.mem.run_thread(self.mem.base)
        self.assertFalse(self.mem.close())
        self.assertEqual(self.kernel.closed, [])
        self.assertEqual(self.kernel.freed, [])
        self.kernel.thread_wait = M.WAIT_OBJECT_0
        self.assertTrue(self.mem.close())
        self.assertEqual(self.kernel.closed, [22, 11])
        self.assertEqual(self.kernel.freed, [])
        self.assertTrue(self.mem.tainted)

    def test_close_does_not_free_possible_game_hook(self):
        self.mem._allocations[self.mem.base] = 128
        self.assertTrue(self.mem.close())
        self.assertEqual(self.kernel.freed, [])
        with self.assertRaises(M.MemoryAccessError):
            self.mem.write(self.mem.base, b"x")

    def test_only_owned_allocations_can_be_freed(self):
        with self.assertRaises(ValueError):
            self.mem.free(self.mem.base)
        self.mem._allocations[self.mem.base] = 128
        self.mem.free(self.mem.base)
        self.assertEqual(self.kernel.freed, [self.mem.base])


if __name__ == "__main__":
    unittest.main()
