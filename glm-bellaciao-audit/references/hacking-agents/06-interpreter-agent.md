# Agent 6 — Interpreter & Value Model

You attack the bytecode interpreter loop and the value representation: stack discipline, reference semantics, containers, equality, (de)serialization.

## Seed files
- `external-crates/move/crates/move-vm-runtime/src/execution/interpreter/eval.rs`
- `external-crates/move/crates/move-vm-runtime/src/execution/interpreter/state.rs`
- `external-crates/move/crates/move-vm-runtime/src/execution/values/values_impl.rs` (large — page through the reference, container, vector, equality, and BCS sections)
- `external-crates/move/crates/move-vm-runtime/src/execution/vm.rs`

## Hunt for
- **Stack/frame limits.** `CallStack::push_call`, operand-stack push, `MachineHeap::allocate_stack_frame`: recursion depth, operand count, frame size limits — enforced on every opcode that grows them? Reentrant/self-recursive Move calls.
- **Reference safety at runtime.** Borrow/return of references into locals and containers; `GlobalValue`; taking a mutable ref while another ref is live; returning a ref to a freed frame's local. The verifier checks borrows statically — does any dynamic path (natives, generic dispatch) escape that?
- **Container aliasing & copy.** `Vector`/`Struct` containers with shared vs owned semantics; `copy` on a large/nested value not charged or producing aliasing; equality that recurses without a depth bound.
- **BCS (de)serialize.** `simple_deserialize` / constant loading: bytes → value with a type the bytes don't match; length prefixes vs actual data; recursion depth; integer width mismatches; trailing bytes.
- **Arithmetic opcodes.** Add/sub/mul/div/mod/shl on u8..u256: overflow abort semantics must be deterministic; shift by ≥ width; div/mod by zero.
- **Native dispatch.** `call_native_with_args`: arg count/type assumptions, results pushed matching the declared signature.

## Add to FINDING
```
opcode_or_op: <the interpreter operation>
input_shape: <the value/type/count that breaks it>
```
