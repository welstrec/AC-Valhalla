"""Small, finite Windows x64 workers; no Cheat Engine or Lua interpreter.

All worker entry points use the Windows thread ABI (RCX is the data pointer).
The first data qword is status (0=no result, 1=returned, 2=precondition failed),
and the second qword is a result. Every call has 32 bytes of shadow space and
RSP is 16-byte aligned. Workers preserve every nonvolatile register they use.
"""
from __future__ import annotations

import struct


def assemble(source: str, address: int = 0) -> bytes:
    from keystone import Ks, KS_ARCH_X86, KS_MODE_64
    encoded, _ = Ks(KS_ARCH_X86, KS_MODE_64).asm(source, address)
    if not encoded:
        raise ValueError("El ensamblador no produjo instrucciones")
    return bytes(encoded)


def inventory_worker(call1: int, call2: int, index_global: int) -> str:
    return f"""
    push rbx
    sub rsp, 0x20
    mov rbx, rcx
    mov qword ptr [rbx], 2
    mov rax, {call1}
    call rax
    test rax, rax
    jz inv_done
    mov rcx, [rax+0x15f8]
    test rcx, rcx
    jz inv_done
    mov rax, {call2}
    call rax
    test rax, rax
    jz inv_done
    mov rcx, [rax+0x70]
    test rcx, rcx
    jz inv_done
    mov rax, [rcx]
    test rax, rax
    jz inv_done
    mov rax, [rax+0xa0]
    test rax, rax
    jz inv_done
    call rax
    test rax, rax
    jz inv_done
    mov rdx, {index_global}
    mov rdx, [rdx]
    test rdx, rdx
    jz inv_done
    movzx ecx, byte ptr [rdx+0x41]
    mov rax, [rax]
    test rax, rax
    jz inv_done
    mov rax, [rax+rcx*8]
    test rax, rax
    jz inv_done
    sub rax, 0x324
    mov [rbx+8], rax
    mov qword ptr [rbx], 1
    inv_done:
    xor eax, eax
    add rsp, 0x20
    pop rbx
    ret
    """


def grant_worker(call: int, world_global: int, create: int, destroy: int,
                 hash_id: int, quantity: int) -> str:
    # Three pushes align RSP. 0x40 includes shadow space and arguments 5..7.
    return f"""
    push rbx
    push rsi
    push rdi
    sub rsp, 0x40
    mov rbx, rcx
    mov qword ptr [rbx], 2
    mov rax, {create}
    call rax
    mov rax, {world_global}
    mov rsi, [rax]
    test rsi, rsi
    jz grant_destroy
    add rsi, 0x480
    mov rdi, [rsi]
    mov qword ptr [rsi], 2
    mov qword ptr [rsp+0x30], 2
    mov qword ptr [rsp+0x28], 1
    mov qword ptr [rsp+0x20], 3
    mov r9d, 2
    xor r8d, r8d
    mov edx, {quantity}
    mov rcx, {hash_id}
    mov rax, {call}
    call rax
    mov [rbx+8], rax
    mov [rsi], rdi
    mov qword ptr [rbx], 1
    grant_destroy:
    mov rax, {destroy}
    call rax
    xor eax, eax
    add rsp, 0x40
    pop rdi
    pop rsi
    pop rbx
    ret
    """


def boost_worker(call: int, owner_global: int, create: int, destroy: int,
                 multiplier: float) -> str:
    bits = struct.unpack("<I", struct.pack("<f", multiplier))[0]
    calls = []
    for kind in (0, 1):
        calls.append(f"""
        mov rax, {owner_global}
        mov rcx, [rax]
        test rcx, rcx
        jz boost_destroy
        add rcx, 0x3f0
        mov qword ptr [rsp+0x20], 0
        mov qword ptr [rsp+0x28], 1
        xor r9d, r9d
        mov eax, {bits}
        movd xmm2, eax
        mov edx, {kind}
        mov rax, {call}
        call rax
        """)
    return f"""
    push rbx
    sub rsp, 0x30
    mov rbx, rcx
    mov qword ptr [rbx], 2
    mov rax, {create}
    call rax
    {''.join(calls)}
    mov qword ptr [rbx], 1
    boost_destroy:
    mov rax, {destroy}
    call rax
    xor eax, eax
    add rsp, 0x30
    pop rbx
    ret
    """


def map_worker(environment_global: int, get_world: int, get_module: int,
               service: int, get_item: int, reveal: int, release: int,
               hash_id: int) -> str:
    return f"""
    push rbx
    push rsi
    push rdi
    sub rsp, 0x20
    mov rbx, rcx
    mov qword ptr [rbx], 2
    mov rax, {environment_global}
    cmp qword ptr [rax], 0
    je map_done
    mov rax, {get_world}
    call rax
    test rax, rax
    jz map_done
    mov rax, {get_module}
    call rax
    test rax, rax
    jz map_done
    mov r9, rax
    xor r8d, r8d
    mov rdx, {hash_id}
    mov rcx, {service}
    mov rax, {get_item}
    call rax
    test rax, rax
    jz map_done
    mov rsi, rax
    mov rcx, [rax+8]
    shl rcx, 0x20
    sar rcx, 0x3f
    and rcx, [rax]
    jz map_release
    mov rax, {reveal}
    call rax
    mov qword ptr [rbx], 1
    map_release:
    mov rcx, rsi
    mov rax, {release}
    call rax
    map_done:
    xor eax, eax
    add rsp, 0x20
    pop rdi
    pop rsi
    pop rbx
    ret
    """
