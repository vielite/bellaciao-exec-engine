# Agent 5 — Verifier ↔ Runtime Gap

The JIT and interpreter trust that bytecode passed the Move verifier and the Sui verifier. You find a property the runtime assumes but the verifier does not actually enforce — or where verified bytecode and executed bytecode differ.

## Seed files
- `sui-execution/latest/sui-verifier/src/` (all: id_leak, private_generics, private_generics_verifier_v2, entry_points, one_time_witness, global_storage_access)
- `external-crates/move/crates/move-vm-runtime/src/validation/mod.rs` and `validation/verification/*`
- `external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs`
- `sui-execution/latest/sui-adapter/src/adapter.rs` (metered verification for signing)

## Hunt for
- **Assumed-but-unchecked.** For each `expect`/`unwrap`/`debug_assert`/"the verifier guarantees" in the JIT or interpreter, find the exact verifier pass that guarantees it. If none does, or it only runs for some code paths (e.g. deps vs published module, system packages, upgrades), that's the gap.
- **Verify-once, execute-other.** Bytecode verified at signing time but a *different* form executed (after upgrade, after cache reuse, after linkage substitution). Does the executed package == the verified package byte-for-byte, keyed correctly?
- **OTW / init / entry rules.** One-time-witness uniqueness, `init` signature, entry-point argument rules, `id_leak` (UID escaping), private generics — find inputs that satisfy the verifier's check but violate the property it's meant to ensure at runtime.
- **Metered vs unmetered verifier.** The signing-time verifier is metered (`SuiVerifierMeter`); the load-time one may not be. A module that passes one but not the other, or exhausts the meter to skip a check.
- **Deserialization trust.** `validation/deserialization` builds the AST before verification — can malformed-but-deserializable bytecode reach the JIT?

## Add to FINDING
```
assumed_property: <what the runtime relies on>
verifier_gap: <the pass that should enforce it and why it doesn't / path:Lnn>
```
