# PackageContext::try_resolve_direct_function_call + package_resolution::effective_system_packages_for

Files:
- T = external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs
- PR = external-crates/move/crates/move-vm-runtime/src/runtime/package_resolution.rs
- RM = external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs
- MC = external-crates/move/crates/move-vm-runtime/src/cache/move_cache.rs
- DT = external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs
- EV = external-crates/move/crates/move-vm-runtime/src/execution/interpreter/eval.rs
- VM = external-crates/move/crates/move-vm-runtime/src/validation/mod.rs
- LK = external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs

Functions: `try_resolve_direct_function_call` T:L144-L175; `effective_system_packages_for` PR:L292-L311.

## 1. Purpose

At JIT time, every `Call`/`CallGeneric` function handle in a package is turned into either
`CallType::Direct(VMPointer<Function>)` or `CallType::Virtual(VirtualTableKey)` (T:L1692-L1696).
A direct call is a raw arena pointer that the interpreter dereferences with no further lookup
(EV:L287, EV:L929). A virtual call is resolved per-execution through the `VMDispatchTables`
built for the transaction's `LinkageContext` (EV:L930, DT:L201-L221).

`try_resolve_direct_function_call` makes that decision. `effective_system_packages_for` builds
the set of "pinned system packages" that the decision is allowed to direct-call into.

## 2. Production configuration fact

Sui's adapter constructs the runtime with `MoveRuntime::new` (sui-execution/latest/sui-adapter/src/adapter.rs:L49),
which passes `SystemPackages::empty()` (RM:L46). `install_system_packages` returns immediately on
an empty set (RM:L102-L104), so `MoveCache.system_packages` stays the empty map created at MC:L73.
`effective_system_packages_for` then returns an empty map at PR:L296-L297 and the
system-package branch of `try_resolve_direct_function_call` (T:L151) never matches.
The only callers of `new_with_system_packages` found are unit tests
(unit_tests/system_packages_tests.rs:L64, L423) and language-benchmarks (move_vm.rs:L111).
In the current Sui configuration, the only reachable direct-call case is the same-package case (T:L148-L150).
The system-package analysis below applies to any host that passes a non-empty `SystemPackages`.

## 3. Inputs, state, and callers

### try_resolve_direct_function_call (T:L144)
- `&self: PackageContext` — relevant fields: `original_id` (T:L61), `version_id` (T:L60),
  `vtable_funs` (T:L69, in-progress map of this package's functions), `system_packages`
  (T:L76, borrowed filtered map), `interner` (T:L55).
- `vtable_entry: &VirtualTableKey` built in `call` (T:L1682-L1689) from the calling module's
  function handle: address = `module_id_for_handle(module_handle).address()` (T:L1687), module
  name and member name interned (T:L1683-L1688).
- Only caller: `call` (T:L1677-L1698). `call` itself is used by `function_instantiations`
  (T:L964) for generic-call handles and by `bytecode` for `Call` (T:L1461).

### effective_system_packages_for (PR:L292)
- `system_packages`: candidate pinned set (`OriginalId -> Arc<Package>`).
- `verified_pkg`: the package about to be JIT'd; uses `original_id` (PR:L299) and
  `linkage_table()` (PR:L300; accessor at validation/verification/ast.rs:L64 returning the
  `linkage_table` field populated from the `SerializedPackage`, deserialization/ast.rs:L35).
- Callers: `jit_package_for_publish` (PR:L216) and `jit_and_cache_package` (PR:L254). Both pass
  the result straight to `jit::translate_package` (PR:L218-L224, PR:L256-L262), which forwards to
  `translate::package` (jit/mod.rs:L32). No other production caller of `translate_package`
  exists; unit tests call it with an empty map (unit_tests/jit_tests.rs:L61, L150).
- Candidate-set sources: `resolve_packages` passes `cache.system_packages()` (PR:L109, PR:L113);
  `validate_package` (publish) passes `self.cache.system_packages()` (RM:L459).
  During `install_system_packages` the same `resolve_packages` path is used, one package per call
  (RM:L182-L191), so each install sees the siblings installed before it (RM:L173-L178).

## 4. Block-by-block

### effective_system_packages_for
- PR:L296-L298: empty candidate set -> empty result. This is the production path (section 2).
- PR:L299-L300: read self original id and the package's own linkage table.
- PR:L303: drop any candidate whose key equals the package's own original id. This is the
  release-mode enforcement of the property that `translate::package` only `debug_assert!`s
  (T:L235-L238).
- PR:L304-L309: keep a candidate only if the package's linkage table has an entry for its
  `OriginalId` and that entry equals `sys_pkg.runtime.version_id`. Missing entry or a different
  version -> excluded.
- PR:L306: returns `Arc::clone` of the candidate, so the effective map holds its own strong
  references for the duration of the translation (the map is a local at PR:L216/L254 and is
  dropped when the JIT function returns).

What this filter compares: the key in the candidate map (`OriginalId`, inserted from
`pkg.runtime.original_id` at MC:L89) and the version (`runtime.version_id`). It compares against
the *package's own stored linkage table*, not against the `LinkageContext` that a later
execution will use (see section 6, A3).

### try_resolve_direct_function_call
- T:L148-L150: if the key's address equals `self.original_id`, look up `vtable_funs` by
  `IntraPackageKey` (module name + member name). `vtable_funs` is populated per module by
  `insert_vtable_functions` (T:L102-L114) at T:L491, after `preallocate_functions` (T:L490) and
  before both `function_instantiations` (T:L493) and `function_bodies` (T:L509). Modules are
  processed in intra-package dependency order by the DFS in `modules` (T:L324-L433, dependencies
  filtered to in-package ids at T:L388). So for any same-package callee in the current module or
  in a module that the current module lists as an immediate dependency, the entry exists before
  the call is translated.
- T:L151-L158: else, if the key's address is in `self.system_packages`, look up
  `sys_pkg.runtime.vtable.functions` by `IntraPackageKey`. That vtable is the fully built
  `PackageVirtualTable` of an already-JIT'd package (T:L294 for how vtables are built).
- T:L159-L161: otherwise `Ok(None)` -> virtual call.
- T:L163-L164: hit -> `ptr_clone` of the stored `VMPointer<Function>`.
- T:L165-L173: miss in the chosen map -> `Err(FUNCTION_RESOLUTION_FAILURE)`. The error text
  prints `self.version_id` (the package being translated), not the target package. The error
  fails the whole package translation: `call` propagates with `?` (T:L1693), and the JIT caller
  wraps it with `Location::Package(version_id)` (PR:L225, PR:L263).

Membership in either map is the *only* gate. Nothing in this function consults visibility,
signature, or the function's defining module beyond the (module, name) key; `DefinitionMap::get`
is a plain `HashMap::get` (DT:L1124-L1126).

## 5. Invariants

I1. A package never direct-resolves into a system-package entry keyed by its own original id:
filtered at PR:L303; same-package keys are also caught first by the branch order at T:L148 before
T:L151.

I2. A system-package direct call is produced only when the package's own `linkage_table` maps the
target `OriginalId` to exactly the pinned package's `runtime.version_id` (PR:L304-L309, consumed
at T:L151).

I3. The pinned set is immutable after runtime construction: `add_system_package` requires
`Arc::get_mut` on the inner map (MC:L90-L100), and it is called only from
`install_system_packages` (RM:L196), which itself requires unique ownership of the cache
`Arc` (RM:L160-L171). `MoveCache::clone` shares the same `Arc` (MC:L307).

I4. Pinned packages are identity-linked (`original_id == version_id`) and all their types
originate from themselves: filter at RM:L109-L139, applied to the serialized input before
loading.

I5. Every same-package call target that is reachable in the DFS order exists in `vtable_funs`
before bodies/instantiations are translated (T:L490-L493, T:L509, DFS at T:L384-L433).

I6. A direct-call pointer is dereferenced at runtime without any linkage or existence check
(EV:L287, EV:L929); virtual calls go through `loaded_packages` of the per-linkage dispatch
tables (DT:L205-L213).

I7. The JIT output for a `VersionId` is computed once and then shared: `jit_and_cache_package`
returns any existing cache entry before computing `effective` (PR:L250-L252), and inserts
first-writer-wins (MC:L136-L149). `jit_package_for_publish` similarly returns a cached entry if
present (PR:L212-L214) but does not insert.

## 6. Assumptions

A1. Pinned `Arc<Package>` outlives every user package whose arena contains a `DirectCall` into
it. The user `jit::execution::ast::Package` stores only `VMPointer`s into the pinned arena; the
`Arc` clones in the effective map (PR:L306) are dropped at the end of translation. Owners of the
pinned `Arc` after construction: `MoveCache.system_packages` (MC:L51) and the `package_cache`
entry inserted during install (PR:L266). `MoveVM` holds `VMDispatchTables` (execution/vm.rs:L52),
which holds `Arc`s only to the packages in its linkage context (DT:L68, populated at
RM:L373-L382), and does not hold an `Arc<MoveCache>` (execution/vm.rs:L50-L63).
Established by: MoveCache ownership (MC:L51 comment, shared/system_packages.rs:L10-L11). Nothing
found that ties the user package's lifetime to the pinned package's lifetime structurally when a
`MoveVM` or `VMDispatchTables` outlives every `MoveCache` clone and its linkage context does not
include the pinned version.

A2. `sys_pkg.runtime.vtable.functions` is complete for the pinned package (all functions of all
modules). Established by `translate::package` building the vtable from `vtable_funs` after all
modules (T:L278, T:L294), and install only registering successful JITs (RM:L192-L209).

A3. The execution-time `LinkageContext` maps each pinned `OriginalId` to the same version as the
package's stored `linkage_table`. The direct-call decision uses the package's stored table
(PR:L300-L305), and the resulting JIT output is cached by `VersionId` and reused across every
linkage context (I7). Execution-time checks compare the linkage context against the set of
resolved packages (VM:L122-L155) and verify cross-module linkage using a relocation map derived
from the resolved packages (LK:L33-L48, LK:L143-L180). Nothing found that compares a loaded
package's own `linkage_table` against the execution `LinkageContext`. When the two differ for a
pinned id, the DirectCall targets the pinned function while signature/visibility linkage checks
(LK:L177) ran against the version in the linkage context. For publish, the link context is built
from the package's own linkage table (RM:L425), so the two agree on that path.

A4. The package's stored `linkage_table` is well-formed and was produced by a trusted
component. For packages loaded from the store it is taken from the `SerializedPackage`
returned by the adapter's `ModuleResolver` (deserialization/ast.rs:L35; PR:L161-L189). Nothing
in `effective_system_packages_for` validates it.

A5. Bytecode verification (visibility, signature compatibility) between the caller and the
pinned callee has been performed. In `try_resolve_direct_function_call`, nothing checks this
(T:L151-L158). Established by `dependencies::verify_module` in `verify_package_valid_linkage`
(LK:L177) during `validate_for_vm_execution` (VM:L76-L82) and `validate_for_publish` (VM:L70),
against whichever version the relocation map names (see A3). For packages resolved only through
`resolve_package`/`resolve_and_cache_package` (RM:L250-L265) and never through
`load_and_cache_vtables`, JIT with direct calls happens without that linkage check
(PR:L143-L144 FIXME; `load_and_verify_packages` does not do linkage checks per PR:L128).

A6. The `IntraPackageKey` (module name, member name) uniquely identifies a function inside a
package: `DefinitionMap::extend` rejects duplicate keys (DT:L1107-L1121) and
`preallocate_functions` uses `unique_map` (T:L1087-L1099).

A7. Same-package call handles address the package by its original id. Branch T:L148 compares
the handle address to `self.original_id`; for an upgraded package, the bytecode self-address
must equal the original id for self-calls to take this branch. Nothing in this function checks
it; a handle whose address is neither the original id nor a pinned id falls through to a
virtual call (T:L159-L161).

A8. The candidate map passed to `translate_package` in any future caller is already filtered;
`translate::package` only `debug_assert!`s self-exclusion (T:L235-L238) and performs no version
filtering. Established by: both current callers apply `effective_system_packages_for`
(PR:L216, PR:L254). Nothing else found.

## 7. Behavior differences between direct and virtual paths

- Failure timing: a missing function under a pinned id fails JIT of the whole calling package
  (T:L165-L173, PR:L263); the same missing function under the virtual path fails only when the
  call instruction executes (DT:L215-L219). Which path is taken depends on whether the node's
  runtime was constructed with a non-empty `SystemPackages` (section 2) and on the per-package
  linkage filter (PR:L304-L309).
- Error code differs: FUNCTION_RESOLUTION_FAILURE (T:L166) vs VTABLE_KEY_LOOKUP_ERROR (DT:L207,
  DT:L216).
- Target identity: direct -> pinned arena regardless of linkage context (EV:L929); virtual ->
  `loaded_packages[original_id]` for the current linkage context (DT:L205).

## 8. Calls

- `VirtualTableKey::package_key` (DT:L1152-L1154) — returns the stored address; no validation.
- `VirtualTableKey::intra_package_key` (DT:L1147-L1149) — returns stored (module, member) key.
- `DefinitionMap::get` (DT:L1124-L1126) — plain HashMap lookup.
- `BTreeMap::get` on `system_packages` (T:L151).
- `VMPointer::ptr_clone` (T:L164) — copies the raw pointer; validity rests on A1.
- `IdentifierInterner::resolve_ident` (T:L169) — error path only; the key was interned in `call`
  (T:L1683-L1688), so resolution uses an interned key.
- `verification::ast::Package::linkage_table` (validation/verification/ast.rs:L64) — accessor.

## 9. Open questions

- unclear; need to inspect whether any Sui host (other than tests/benchmarks) plans to call
  `new_with_system_packages`, and whether Sui system packages (0x1, 0x2, 0x3), whose VersionId
  stays equal to OriginalId across framework upgrades, would satisfy the filter at PR:L305 after
  an on-chain upgrade while the pinned copy is the pre-upgrade bytecode.
- unclear; need to inspect sui-adapter linkage analysis to determine whether an execution
  `LinkageContext` can map a pinned OriginalId to a version other than the one in a package's
  stored `linkage_table` (A3).
- unclear; need to inspect whether a `MoveVM`/`VMDispatchTables` can outlive all `MoveCache`
  clones in any host (A1), e.g. the per-tx `executable_vm_cache` in sui-adapter context.rs vs the
  lifetime of `Arc<MoveRuntime>`.
- unclear; need to inspect whether a package JIT'd with direct calls through
  `resolve_and_cache_package` (no linkage check) can have its functions executed without a later
  `load_and_cache_vtables` + `validate_for_vm_execution` for a linkage context containing it (A5).
- unclear; need to inspect `jit_package_for_publish`'s early return at PR:L212-L214: whether a
  cached package at the same VersionId as a package being published can differ in content.
