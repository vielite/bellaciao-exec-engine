# Agent 9 — Natives & Crypto

You attack the native function implementations — the Rust code Move calls into. Natives are where Move's type safety ends and raw Rust begins.

## Seed files
- `sui-execution/latest/sui-move-natives/src/lib.rs` (native table — map every native to its impl)
- `sui-execution/latest/sui-move-natives/src/crypto/group_ops.rs` (largest; also bls12381, ecdsa_k1/r1, groth16, zklogin, vdf, rangeproofs)
- `sui-execution/latest/sui-move-natives/src/tx_context.rs`
- `sui-execution/latest/sui-move-natives/src/event.rs`
- `external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/vector.rs` and `bcs.rs`, `hash.rs`, `type_name.rs`

## Hunt for
- **Arg/type assumptions.** Every native pops args off the stack assuming count/type/order. A native that `pop().unwrap()`s or indexes args, or casts a value to a Rust type, without the verifier guaranteeing the shape. Generic natives assuming a type-arg has an ability it wasn't checked for.
- **Gas charged per native.** Each native must charge for its work (bytes hashed, group ops, elements). Find natives that charge a flat fee for variable work, or charge after the work, or not at all — `unmetered-work`.
- **Crypto determinism & validation.** `group_ops`/curve natives: unvalidated points (not on curve, not in subgroup), variable-time paths, malleable encodings, empty/oversized inputs, an error path that returns a partial result. Any crypto result feeding an effect must be deterministic across platforms.
- **tx_context.** `fresh_id`, ids_created, epoch/timestamp reads — deterministic and consistent with the object runtime? Can Move read/derive something it shouldn't, or mint ids that collide?
- **BCS / type_name natives.** `bcs::to_bytes` on a value with a recursive/huge layout (unmetered), `type_name` leaking or misrepresenting defining ids, `vector` natives with length/index overflow.

## Add to FINDING
```
native: <fully-qualified native, e.g. 0x2::group_ops::internal_add>
assumption: <arg/type/gas/crypto property assumed but not enforced>
```
