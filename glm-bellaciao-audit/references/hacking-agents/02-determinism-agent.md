# Agent 2 — Determinism & Consensus Split

You make two honest validators disagree on the effects or status of the same transaction. Any non-determinism on the execution path is `consensus-split` — the highest-value class here.

## Seed files
- `external-crates/move/crates/move-vm-runtime/src/cache/move_cache.rs`
- `external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs`
- `external-crates/move/crates/move-vm-runtime/src/runtime/package_resolution.rs`
- `external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs`

## Hunt for
- **Cache-state dependence.** Output that differs on cache hit vs miss: LRU eviction of `linkage_vtables`, `type_depths` populated by an earlier tx, `package_cache` entries, interner contents. If a limit is checked against a *cached* structure's size, a validator that evicted it behaves differently.
- **Iteration order.** `HashMap`/`DashMap`/`HashSet` iterated to produce effects, error selection, event order, or "first error wins". Any `HashMap` where a `BTreeMap`/sorted iteration was needed.
- **Thread interleaving.** One `MoveRuntime` shared across executor threads: two txs racing on `add_package_to_cache`, `drop_all_cached_linkage_tables`, telemetry counters, or the interner. Does a race change a result, or just which of two identical results wins?
- **dev-inspect / dry-run bleed.** RPC `dev_inspect_transaction` shares the process-wide caches with consensus execution. Can a dev-inspect call warm/poison a cache so a later consensus tx's result changes? (`cache-poisoning` + `consensus-split`.)
- **Non-deterministic error selection.** When multiple checks could fail, is the reported `ExecutionErrorKind`/abort location deterministic across validators? Error code lands in effects.
- **Float / time / address randomness / pointer identity** used in any effect-producing decision.

## Add to FINDING
```
divergence: <the two states/orders/timings that produce different effects>
observable: <where the difference lands in effects or status>
```
