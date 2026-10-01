# Target — Sui "bella-ciao" Move VM

Repo root is your filesystem root for tools: every path you pass to `read_file`, `grep`, `list_dir`, `find_files` is **repo-relative** (e.g. `external-crates/move/crates/move-vm-runtime/src/cache/arena.rs`).

## Scope

| Alias | Path | What |
|---|---|---|
| `VM` | `external-crates/move/crates/move-vm-runtime/src` | the new VM: validation, JIT, caches, interpreter, values, natives table |
| `SPT` | `sui-execution/latest/sui-adapter/src/static_programmable_transactions` | PTB linkage, loading, typing/verify, metering, execution context |
| `AD` | `sui-execution/latest/sui-adapter/src` | execution engine, gas meter/charger, temporary store, data store |
| `NA` | `sui-execution/latest/sui-move-natives/src` | Sui natives: object runtime, dynamic fields, transfer, events, crypto |
| `VF` | `sui-execution/latest/sui-verifier/src` | Sui-specific bytecode verifier passes |
| glue | `sui-execution/src` | executor/verifier selection (execution version 4 = `latest`) |

Out of scope: `unit_tests/`, `dev_utils/`, `execution/tracing/`, `historical-versions/`, `test_scenario.rs`, anything gated to tests/tracing only. You may read them for context.

## Why bugs here matter

Every validator executes the same ordered transactions with this code. Transaction senders, gas sponsors and **package publishers (arbitrary bytecode, arbitrary dependency/linkage choices)** are untrusted. The adapter crate root denies `clippy::arithmetic_side_effects`, `indexing_slicing`, `cast_possible_truncation` — code that opts out (`#[allow(...)]`) or lives in the VM crate without those lints deserves extra suspicion. No `catch_unwind` was found on the execution path at the audited commit, so a reachable `panic!`/`unwrap`/`expect`/index-out-of-bounds/overflow-in-debug is a potential validator crash — verify this per path before claiming it.

Impact classes (use these tags in `impact:`):

- `consensus-split` — two honest validators can produce different effects/status for the same tx (non-determinism, cache-state dependence, thread timing, iteration order, uninitialized/garbage reads)
- `memory-corruption` — UB reachable from untrusted bytecode or PTB input (`unsafe` arena/pointer/string/locals code, dangling `VMPointer`, aliasing)
- `type-confusion` — a value is treated as a different type/package/ability set than it really has (defining-id / type-origin / linkage mistakes, generic instantiation, `TypeTag`→`Type`)
- `asset-integrity` — minting/duplicating/destroying coins or objects, bypassing ownership/`key`/`store`/`drop` rules, SUI conservation break
- `validator-crash` — panic/abort/OOM of a validator or fullnode from a normal tx, publish, or dev-inspect/dry-run
- `unmetered-work` — superlinear CPU/memory not charged to gas (JIT, linkage, verification, deserialization, type layouts, natives)
- `cache-poisoning` — process-wide caches (package cache, vtable LRU, interner, type depth cache) holding state that later changes another tx's outcome
- `other` — explain

## Security invariants to break

1. **Determinism**: output of a tx is a pure function of (inputs, on-chain state, protocol config). Cache hits vs misses, thread interleavings, and prior dev-inspect calls must not change it.
2. **Type identity**: a runtime type is identified by its *defining* package id + module + name + type args, independent of which upgraded version is linked.
3. **Verifier-then-trust**: the JIT/interpreter rely on bytecode having passed the Move verifier and Sui verifier. Find where the runtime assumes something the verifier does not actually check, or where verified and executed bytecode differ (linkage, upgrades, cached packages).
4. **Metering**: every unit of work proportional to attacker input is charged before or while it is done, and limits (type nodes, value depth, stack, vector length, arena size, interner memory) hold on every path.
5. **Memory safety**: every `unsafe` block's precondition holds for all inputs reachable from untrusted bytecode.
6. **Object semantics**: IDs are unique, ownership changes only through transfer natives, wrapped/deleted/child objects are tracked, SUI is conserved, gas smashing/rebates are consistent.

## Orientation

The full orientation map (actors, entrypoints, shared state, modules) follows this section when available.
