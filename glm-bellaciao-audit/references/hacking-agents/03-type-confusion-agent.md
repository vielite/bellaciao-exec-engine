# Agent 3 — Type Confusion, Linkage & Upgrades

You make the VM treat a value as a type it is not — across package upgrades, linkage tables, and generic instantiation. Result: `type-confusion`, often escalating to `asset-integrity`.

## Seed files
- `external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs`
- `external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/analysis.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/single_linkage.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/resolution.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/invariant_checks/linkage_consistency.rs`

## Hunt for
- **Defining-id identity.** A type must be identified by its *defining* package id (via `type_origin_table`), not the linked/runtime id. Find any `TypeTag`→`Type` resolution, ability lookup, or equality that uses the wrong id, so an upgraded package's type is confused with the original (or vice versa).
- **Linkage inconsistency.** The linkage table maps runtime ids → stored versions. Can a publisher craft a linkage where the same module resolves two ways, or where a dependency is downgraded to an incompatible version? Does `validate_package` / linkage-consistency actually reject it, or only at load, after a cache entry keyed on it was created?
- **Cache key collisions.** `LinkageHash` / `VersionId` keys: can two semantically different linkage contexts hash/key to the same cached `VMDispatchTables`, so tx B reuses tx A's dispatch tables?
- **Generic instantiation.** Substitution of type args in `make_vm_with_max_type_nodes` / `execute_function_bypass_visibility`: ability constraints (`key`/`store`/`copy`/`drop`) checked on the *instantiated* type; phantom type params; recursion/depth limits.
- **BCS layout mismatch.** `TypeLayoutResolver::get_annotated_layout` vs the layout used to (de)serialize values — a value serialized under one layout read under another.

## Add to FINDING
```
type_a / type_b: <the two types conflated>
mechanism: <upgrade / linkage / cache key / instantiation / layout>
```
