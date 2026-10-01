# Agent 4 — Metering & Resource Exhaustion

You do work the gas meter does not charge for, or blow a resource limit. Superlinear uncharged work is `unmetered-work` → `validator-crash` (OOM/timeout) at scale.

## Seed files
- `external-crates/move/crates/move-vm-runtime/src/shared/gas.rs`
- `external-crates/move/crates/move-vm-runtime/src/shared/safe_ops.rs`
- `sui-execution/latest/sui-adapter/src/gas_meter.rs`
- `sui-execution/latest/sui-adapter/src/gas_charger.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/metering/translation_meter.rs`
- `external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs` (`calculate_depth_of_datatype_and_cache`)

## Hunt for
- **Charge-after-work.** Work performed *before* the gas charge for it — deserialization, JIT translation, linkage resolution, depth-formula computation, type layout building, native execution. If the charge is skipped on error paths or on cache-miss-only paths, an attacker triggers the expensive path cheaply.
- **Unbounded / superlinear.** Depth formulas, generic instantiation blow-up (type nodes × recursion), vector operations, `MakeMoveVec`, nested dynamic fields, `translation_meter` limits. Verify the limit is enforced on EVERY path that grows the structure, not just the common one.
- **First-run asymmetry.** JIT and package resolution are charged (or not) once, then cached. Is the first caller charged for work all later callers benefit from — or is nobody charged, so a whale package is JITed for free and pins memory forever?
- **Arithmetic on gas.** Overflow/underflow/saturation in gas math (`safe_ops`, `SuiGasMeter`) that lets cost round to zero or wrap. Look for `#[allow(clippy::arithmetic_side_effects)]`.
- **Rebate/smashing.** Gas smashing and rebate math in `gas_charger.rs`: can a tx end with a net negative charge, or reclaim more than it paid?

## Add to FINDING
```
work: <the operation done>
charge_site: <where it is (or isn't) metered, path:Lnn>
amplification: <input size → work/memory relation>
```
