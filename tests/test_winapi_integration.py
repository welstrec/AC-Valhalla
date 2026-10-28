"""Real WinAPI checks against short-lived Python children owned by this test.

No game is enumerated or opened. Every child is hidden and stopped in teardown;
timeouts cannot leave a helper process or its synthetic worker running.
"""
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import unittest

from valhalla.memory import MemoryAccessError, ProcessMemory, TargetTaintedError

try:
    from keystone import KS_ARCH_X86, KS_MODE_64, Ks
except ImportError:
    Ks = None


CHILD_SCRIPT = r'''
import ctypes, json, os, sys
buffer = ctypes.create_string_buffer(b"valhalla-native-probe", 128)
print(json.dumps({"pid": os.getpid(), "address": ctypes.addressof(buffer)}), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    if command["op"] == "read":
        result = ctypes.string_at(command["address"], command["size"]).hex()
    elif command["op"] == "release":
        ctypes.c_uint32.from_address(command["address"]).value = 1
        result = "released"
    else:
        result = "unknown"
    print(json.dumps({"result": result}), flush=True)
'''


def assemble(source, address):
    return bytes(Ks(KS_ARCH_X86, KS_MODE_64).asm(source, addr=address)[0])


@unittest.skipUnless(sys.platform == "win32", "Windows-only controlled integration")
class WinApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.mem = None
        self.child = subprocess.Popen(
            [getattr(sys, "_base_executable", sys.executable), "-u", "-c", CHILD_SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=0x08000000,
        )
        self.addCleanup(self.cleanup_child)
        self.responses = queue.Queue()

        def consume_output():
            for line in self.child.stdout:
                self.responses.put(line)
            self.responses.put(None)

        self.reader = threading.Thread(target=consume_output, daemon=True)
        self.reader.start()
        self.info = self.response()
        self.assertEqual(self.info["pid"], self.child.pid)
        self.assertNotEqual(self.child.pid, os.getpid())
        self.mem = ProcessMemory(self.child.pid, allow_test_target=True)

    def response(self):
        line = self.responses.get(timeout=5)
        if line is None:
            raise AssertionError("The controlled helper exited unexpectedly.")
        return json.loads(line)

    def command(self, **payload):
        self.child.stdin.write(json.dumps(payload) + "\n")
        self.child.stdin.flush()
        return self.response()["result"]

    def cleanup_child(self):
        # Never leave a synthetic worker alive, even after a failed assertion.
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait(timeout=5)
        if self.mem is not None:
            deadline = time.monotonic() + 2
            while not self.mem.close() and time.monotonic() < deadline:
                time.sleep(0.01)
            if self.mem._threads:
                raise AssertionError("A signaled helper worker handle was not collected.")
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            if stream:
                stream.close()
        self.reader.join(timeout=1)

    def test_real_read_write_and_process_identity(self):
        address = self.info["address"]
        self.assertEqual(self.mem.read(address, len(b"valhalla-native-probe")), b"valhalla-native-probe")
        self.mem.write(address, b"changed-by-native-api")
        self.assertEqual(self.command(op="read", address=address, size=21), b"changed-by-native-api".hex())
        self.assertTrue(self.mem.is_alive())
        self.assertEqual(self.mem.identity[0], self.child.pid)
        self.assertGreater(self.mem.identity[1], 0)
        self.assertGreater(self.mem.base, 0)
        self.assertGreater(self.mem.size, 0)

    def test_real_alloc_free_and_code_protection_restore(self):
        address = self.mem.alloc(4096)
        self.assertEqual(self.mem.read(address, 8), b"\x00" * 8)
        self.assertFalse(self.mem.is_executable(address))
        original_protection = self.mem._region(address).Protect
        self.mem.write_code(address, b"\x31\xC0\xC3", expected=b"\x00" * 3)
        self.assertEqual(self.mem.read(address, 3), b"\x31\xC0\xC3")
        self.assertEqual(self.mem._region(address).Protect, original_protection)
        self.mem.free(address)
        with self.assertRaises(MemoryAccessError):
            self.mem.read(address, 1)

    @unittest.skipIf(Ks is None, "Keystone is required for x64 integration")
    def test_real_remote_worker_rcx_abi_and_nonvolatile_registers(self):
        data = self.mem.alloc(4096)
        worker = self.mem.alloc(4096, executable=True)
        wrapper = self.mem.alloc(4096, executable=True)
        # The synthetic worker reports incoming stack alignment and writes data
        # through the Win64 RCX parameter, while preserving its nonvolatiles.
        worker_source = """
            mov rax, rsp
            and eax, 15
            mov dword ptr [rcx + 8], eax
            push rbx
            mov rbx, 0x123456789ABCDEF0
            mov qword ptr [rcx], rbx
            pop rbx
            xor eax, eax
            ret
        """
        self.mem.write_code(worker, assemble(worker_source, worker))
        registers = ("rbx", "rbp", "rsi", "rdi", "r12", "r13", "r14", "r15")
        sentinel = 0x1122334455667788
        # The wrapper preserves its own caller's state and verifies that a call
        # to our worker preserves all eight integer nonvolatile registers.
        wrapper_source = "\n".join(f"push {register}" for register in registers)
        wrapper_source += "\nsub rsp, 40\n"
        wrapper_source += "\n".join(f"mov {register}, {sentinel}" for register in registers)
        wrapper_source += f"\nmov rax, {worker}\ncall rax\nmov rdx, {sentinel}\n"
        for register in registers:
            wrapper_source += f"cmp {register}, rdx\njne corrupt\n"
        wrapper_source += "xor eax, eax\njmp done\ncorrupt:\nmov eax, 1\ndone:\nadd rsp, 40\n"
        wrapper_source += "\n".join(f"pop {register}" for register in reversed(registers))
        wrapper_source += "\nret"
        self.mem.write_code(wrapper, assemble(wrapper_source, wrapper))
        self.assertEqual(self.mem.run_thread(wrapper, data, timeout_ms=3000), 0)
        self.assertEqual(self.mem.u64(data), 0x123456789ABCDEF0)
        self.assertEqual(self.mem.u32(data + 8), 8)
        for address in (wrapper, worker, data):
            self.mem.free(address)

    @unittest.skipIf(Ks is None, "Keystone is required for x64 integration")
    def test_timeout_quarantine_and_reap_only_after_worker_completion(self):
        control = self.mem.alloc(4096)
        code = self.mem.alloc(4096, executable=True)
        self.mem.write_code(code, assemble("""
            waiting:
            cmp dword ptr [rcx], 1
            je finished
            pause
            jmp waiting
            finished:
            xor eax, eax
            ret
        """, code))
        with self.assertRaises(TargetTaintedError):
            self.mem.run_thread(code, control, timeout_ms=25)
        self.assertTrue(self.mem.tainted)
        self.assertFalse(self.mem.close())
        self.assertEqual(len(self.mem._threads), 1)
        self.assertIn(code, self.mem._allocations)
        self.assertIn(control, self.mem._allocations)
        with self.assertRaises(TargetTaintedError):
            self.mem.free(code)
        with self.assertRaises(TargetTaintedError):
            ProcessMemory(self.child.pid, allow_test_target=True)
        self.assertEqual(self.command(op="release", address=control), "released")
        deadline = time.monotonic() + 3
        while not self.mem.close() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.mem._threads)
        self.assertIsNone(self.mem._handle)
        self.assertIn(code, self.mem._allocations)
        self.assertTrue(self.mem.tainted)
        with self.assertRaises(TargetTaintedError):
            ProcessMemory(self.child.pid, allow_test_target=True)

    def test_default_constructor_rejects_helper(self):
        with self.assertRaises(MemoryAccessError):
            ProcessMemory(self.child.pid)


if __name__ == "__main__":
    unittest.main()
