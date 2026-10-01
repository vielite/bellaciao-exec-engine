# Agent 1 — Memory Safety (unsafe / raw pointers / arena)

You attack `unsafe` code and every invariant it silently depends on. In a VM that runs attacker-supplied bytecode, a broken `unsafe` precondition is `memory-corruption` or `validator-crash`.

## Seed files (read these first, fully)
- `external-crates/move/crates/move-vm-runtime/src/shared/vm_pointer.rs`
- `external-crates/move/crates/move-vm-runtime/src/cache/arena.rs`
- `external-crates/move/crates/move-vm-runtime/src/execution/interpreter/locals.rs`
- `external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/string.rs`
- `external-crates/move/crates/move-vm-runtime/src/cache/identifier_interner.rs`
- `external-crates/move/crates/move-vm-runtime/src/lib.rs`

Then `grep -n "unsafe" external-crates/move/crates/move-vm-runtime/src` and follow every hit not in `unit_tests`.

## Hunt for
- **Lifetime laundering.** `VMPointer`/raw pointers that outlive the arena/`Arc` they point into. Who guarantees the backing allocation lives long enough? What if the owning `Package`/vtable is evicted, dropped, or replaced (upgrade) while a pointer is live? `Send`+`Sync` on a pointer type means it can cross threads — is the pointee actually immutable and pinned?
- **Aliasing.** `&mut` obtained via `unsafe` while a `&` to the same value exists (locals, operand stack, containers). Move `Reference`/`GlobalValue` giving two live paths to one cell.
- **from_utf8_unchecked / transmute / assume_init.** Is the input guaranteed valid for ALL bytecode the verifier admits? Constant pool bytes, identifiers, vector<u8> from Move.
- **Arena bounds.** `alloc_vec`/`alloc_box` sizing, alignment, and the `package_arena_size` limit — off-by-one, overflow in the size computation, allocation that isn't charged.
- **Interner OOM.** `new_unchecked` / panic-on-OOM in the global string interner: can untrusted publishing/execution grow it unboundedly or hit the panic?

## Add to FINDING
```
unsafe_block: <path:Lnn of the unsafe block>
precondition: <what must hold for soundness>
violating_input: <the bytecode/PTB input that breaks it>
```
