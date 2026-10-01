# `validation::validate_for_publish` and `validation::validate_against_link_context`

File: `external-crates/move/crates/move-vm-runtime/src/validation/mod.rs` (repo root `/home/vielite/hackenProof/sui`, commit fd2e2a0fc6).
Citations without a file prefix refer to `validation/mod.rs`. Other files are abbreviated:
- `runtime/mod.rs` = `move-vm-runtime/src/runtime/mod.rs`
- `pkg_res.rs` = `move-vm-runtime/src/runtime/package_resolution.rs`
- `linkage.rs` = `move-vm-runtime/src/validation/verification/linkage.rs`
- `linkage_context.rs` = `move-vm-runtime/src/shared/linkage_context.rs`
- `deser.rs` = `move-vm-runtime/src/validation/deserialization/translate.rs`, `deser_ast.rs` = `.../deserialization/ast.rs`
- `verif.rs` = `move-vm-runtime/src/validation/verification/translate.rs`
- `move_cache.rs` = `move-vm-runtime/src/cache/move_cache.rs`
- `context.rs` = `sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs`
- `move_package.rs` = `crates/sui-types/src/move_package.rs`

---

## `validate_against_link_context` in validation/mod.rs (L122-L156)

**Purpose:** Checks that a set of already-resolved, already-verified packages (keyed by `VersionId`) corresponds to the entries of a `LinkageContext` linkage table (`OriginalId -> VersionId`). It is a set-level consistency check between "what the linkage context says" and "what the resolver actually returned". It is shared between the publish path (`publish = true`, L54) and the execution/vtable path (`publish = false`, L80). Without it, later stages (`verify_linkage_and_cyclic_checks*`, `VMDispatchTables::new`) would operate on a package set that could omit entries from, or include packages absent from, the linkage context.

**Inputs & Assumptions:**
- `publish` (bool): selects expected cardinality. Trust: trusted (hard-coded literal at both call sites, L54 and L80).
- `packages` (`&BTreeMap<VersionId, &verification::ast::Package>`): resolved packages. On publish, the dependencies of the package being published, excluding the package itself (runtime/mod.rs:L438, L445-448). On execution, all packages in the linkage table (runtime/mod.rs:L360-371). Contents come from the trusted backing store / per-tx package store (bytecode itself is publisher-controlled), passed through `resolve_packages`.
- `link_context` (`&LinkageContext`): on publish built from the publisher's `SerializedPackage.linkage_table` (runtime/mod.rs:L425); on execution supplied by the adapter via `make_vm`. Table content derives from untrusted publisher/sender choices, shaped by adapter code (see Callers).
- Implicit: `LinkageContext` has passed `LinkageContext::new`, which rejects duplicate version IDs (linkage_context.rs:L37-L48). The type has a private field (linkage_context.rs:L25) and `new` is the only non-test constructor in that file; `add_entry` / `add_type_arg_addresses_reflexive` exist only under `cfg(any(debug_assertions, feature = "testing"))` (linkage_context.rs:L86-L125), and `add_entry` does not check value uniqueness (it checks whether `version_id` is a *key*, L96).
- Precondition: for every `(k, p)` in `packages`, `k == p.version_id`. Established by: `resolve_packages` keys cached entries by the requested id (pkg_res.rs:L100-L101) and the cache is keyed by `verified_pkg.version_id` at its sole insert site (pkg_res.rs:L247, L266; move_cache.rs:L126-L150); freshly loaded entries are keyed by `pkg.verified.version_id` (pkg_res.rs:L114). So `k == p.version_id` holds on both paths of `resolve_packages`. This function does not itself check it (L142-L143 uses the key, not `pkg.version_id`).
- Precondition: the key set of `packages` equals the set of version IDs requested from the resolver. Established only by `debug_assert!` in `resolve_packages` (pkg_res.rs:L119-L122) and in `load_packages` (pkg_res.rs:L192-L195). In release builds: nothing found beyond trust in the store returning the requested `version_id` for each request.

**Outputs & Effects:**
- Returns `Ok(())` or `Err(VMError)` with status `UNKNOWN_INVARIANT_VIOLATION_ERROR` (L133-L139, L144-L152).
- No state writes, no external calls other than `BTreeMap` reads on `link_context.linkage_table()` (linkage_context.rs:L51-L53).
- Postcondition on `Ok`, `publish = false`: `|packages| == |table|` and every package's `(original_id, key)` pair is present in the table. Because `packages` keys are distinct, the n package pairs are n distinct table entries (distinct values; `BTreeMap` keys unique), so with equal cardinality every table entry is covered: bijection between table entries and resolved packages.
- Postcondition on `Ok`, `publish = true`: `|table| == |packages| + 1` and every package's `(original_id, key)` pair is in the table. Hence exactly one table entry `(K, V')` is not matched by any resolved package. This function establishes nothing about `K` or `V'`.

**Block-by-Block:**

```rust
// L127-L131
let expected_len = if publish {
    packages.len().saturating_add(1)
} else {
    packages.len()
};
```
- **What:** Computes the expected table size; on publish one extra entry is expected for the package being published.
- **Why here:** Cardinality first; it is O(1) and rejects before per-entry lookups.
- **Assumes:** On publish, the extra entry is the published package's self entry `(original_id -> version_id)`. Nothing in this function or in `validate_for_publish` checks the identity of that extra entry (see Invariant couplings).
- **Establishes:** nothing by itself. `saturating_add` cannot wrap; reaching `usize::MAX` packages is not reachable in memory.
- **Depended on by:** L132.

```rust
// L132-L140
if expected_len != link_context.linkage_table().len() { return Err(... .finish(Location::Undefined)); }
```
- **What:** Rejects when table size differs from expected.
- **Why here:** Before the mapping loop; the loop alone only proves `packages ⊆ table` and cannot detect extra table entries.
- **Assumes:** `packages` has one entry per distinct requested version id (see precondition above). If the resolver returned fewer distinct keys than requested (e.g., two requests resolved to the same `version_id`), cardinality would be off by that amount and could coincidentally equal `expected_len` only on the publish path when the request set already excluded a table value, or on execution when paired with an extraneous key; in release builds nothing in the VM excludes a mismatched resolver result besides the L143 mapping check.
- **Establishes:** `|table| == |packages| + [publish]`.
- **Depended on by:** the bijection argument in the doc comment (L106-L121).

```rust
// L142-L154
for (version_id, pkg) in packages {
    if link_context.linkage_table().get(&pkg.original_id) != Some(version_id) { return Err(...); }
}
```
- **What:** For each resolved package, the table maps its `original_id` to its map key.
- **Why here:** After cardinality, so together they establish coverage.
- **Assumes:** map key equals `pkg.version_id` (established by `resolve_packages`, above). `pkg.original_id` is the verified package's original id, which `deserialization::translate::package` has checked equals every module's self address (deser.rs:L44-L51).
- **Establishes:** (a) no two resolved packages share an `original_id` (a map key cannot have two values, and keys are distinct); (b) every resolved package's version is the one linked for its original id.
- **Depended on by:** `verify_linkage_and_cyclic_checks*` build a `HashMap<OriginalId, VersionId>` relocation map from `packages` (linkage.rs:L33-L39, L68-L78); (a) guarantees no silent overwrite among dependency entries in that map. `VMDispatchTables::new` collects runtime packages into a `BTreeMap<OriginalId, _>` (runtime/mod.rs:L373-L376, L464-L468); (a) guarantees no silent overwrite there either.

**Cross-Function Dependencies:**
- Callee `LinkageContext::linkage_table` (internal): plain getter (linkage_context.rs:L51-L53).
- Callee `partial_vm_error!` / `.finish` (external-source-available, move-binary-format): error construction only.
- Callers: `validate_for_publish` (L54) and `validate_for_vm_execution` (L80). Neither caller inspects the result beyond `?`.
- Shared state: none read or written directly. Inputs reference `MoveCache.package_cache` entries via `Arc` (runtime/mod.rs:L370, L447).
- Invariant couplings: the injectivity half of the bijection lives in `LinkageContext::new` (linkage_context.rs:L37-L48). Any construction path that bypasses `new` (test-only `add_entry`, linkage_context.rs:L91-L104) removes that half.

**Open Questions:**
- unclear; need to inspect whether any non-test code constructs `LinkageContext` via `Clone`/deserialization from a table not passed through `new` (e.g., adapter-side `ExecutableLinkage` -> `LinkageContext` conversion).
- unclear; need to inspect `ModuleResolver::get_packages` implementations in sui-adapter (`TransactionPackageStore`, `CachedPackageStore`) for whether they can return a `SerializedPackage` whose `version_id` differs from the requested id; the VM guards this only with `debug_assert_eq!` (pkg_res.rs:L194).

---

## `validate_for_publish` in validation/mod.rs (L36-L72)

**Purpose:** Full VM-side validation of a package being published or upgraded: consistency of the resolved dependency set with the linkage context, deserialization + per-module bytecode verification of the new package, equality of the package's declared original id with the caller-supplied one, and cross-package linkage/cycle checks of the new package against its dependencies. Returns the verified AST that `MoveRuntime::validate_package` then JIT-compiles (runtime/mod.rs:L455-L461) and hands back to the adapter.

**Inputs & Assumptions:**
- `natives` (`&NativeFunctions`): trusted, runtime-owned (runtime/mod.rs:L449).
- `vm_config` (`&VMConfig`): trusted (protocol config).
- `original_id` (`OriginalId`): caller-supplied expected runtime id. In the adapter: fresh id for publish (context.rs:L1318-L1328), predecessor's `original_package_id()` for upgrade (context.rs:L1373). Trust: derived by trusted adapter code.
- `package` (`SerializedPackage`): module bytes (untrusted publisher bytecode), `version_id`, `original_id`, `linkage_table`, `type_origin_table`, `version`. Built by `MovePackage::into_serialized_move_package` (move_package.rs:L573-L619) at context.rs:L1133.
- `dependencies` (`BTreeMap<VersionId, &verification::ast::Package>`): output of `resolve_packages` over `link_context.all_package_dependencies_except(pkg.version_id)` (runtime/mod.rs:L433-L439, L445-L448). Already verified and already inserted into the process-wide cache (pkg_res.rs:L113, L266).
- `link_context` (`&LinkageContext`): `LinkageContext::new(pkg.linkage_table.clone())` (runtime/mod.rs:L425), i.e., the same table as `package.linkage_table`.
- Preconditions and what establishes each:
  - `link_context.linkage_table() == package.linkage_table`: established by the caller at runtime/mod.rs:L425; not checked here.
  - The table contains the self entry `package.original_id -> package.version_id`: established in the adapter by `into_serialized_move_package`, which chains `(original_package_id, self.id)` last into the collected `BTreeMap` (move_package.rs:L607-L615). Inside the VM: nothing found (see Invariant couplings).
  - `package.version_id` not in `dependencies`: established by `all_package_dependencies_except` filtering (linkage_context.rs:L69-L79), given resolver keys equal requested ids (pkg_res.rs debug_asserts only).

**Outputs & Effects:**
- Returns `Ok(verification::ast::Package)` whose `original_id == original_id` (L58) and whose `version_id`, `linkage_table`, `type_origin_table`, `version` are copied verbatim from `package` (deser_ast.rs:L30-L37, verif.rs:L32-L58).
- Errors: invariant violations from L54 and L58-L66; deserialization/verification errors from L56; `MISSING_DEPENDENCY` / linker / cyclic errors from L70.
- No state writes of its own. All callees reached here are pure with respect to shared state (`validate_package` builds values; linkage checks only read). Dependencies were written into `MoveCache.package_cache` by the caller before this function runs (pkg_res.rs:L113) and remain cached on any error path here.
- Work performed is unmetered: the caller's gas meter is unused (`_gas_meter`, runtime/mod.rs:L409), and module verification is `verify_module_with_config_unmetered` (verif.rs:L65-L66).

**Block-by-Block:**

```rust
// L44-L52
tracing::trace!(...); dbg_println!(...);
```
- **What:** Logging. `dbg_println!` prints `package.linkage_table`, not `link_context`.
- No effect on control flow.

```rust
// L54
validate_against_link_context(/* publish */ true, &dependencies, link_context)?;
```
- **What:** Checks `|table| == |deps| + 1` and every dep's `(original_id, version_id)` is in the table.
- **Why here:** Before the expensive deserialization/verification at L56, so an inconsistent resolved set rejects cheaply.
- **Assumes:** The single unmatched table entry is the published package's self entry. Not checked here: the key of that entry equals `original_id` / `package.original_id`, and its value equals `package.version_id`. The value half follows from the caller's `all_package_dependencies_except(pkg.version_id)` (runtime/mod.rs:L438) plus resolver key fidelity: if the table did not contain `package.version_id`, all n values would be requested and resolved, giving `n != n+1` at L132. The key half: nothing found in the VM.
- **Establishes:** No dependency shares an `original_id` with another dependency (see validate_against_link_context L142-L154). Given the self entry keyed by `package.original_id` (adapter-established), also that no dependency has `original_id == package.original_id` (its entry would have to map that key to its own version id, which differs from `package.version_id` because that id was excluded from the request).
- **Depended on by:** L70's relocation map construction (linkage.rs:L68-L78).

```rust
// L56
let validated_package = validate_package(natives, vm_config, package)?;
```
- **What:** Deserializes each module (deser.rs:L29-L31), checks map name == module self name (deser.rs:L34-L42), module address == `package.original_id` (deser.rs:L44-L51), no duplicate module ids (deser.rs:L54-L61), non-empty (deser.rs:L65-L70); then for each module runs the unmetered bytecode verifier (verif.rs:L66), per-function script signature check (verif.rs:L68-L76), and native resolution / native-struct rejection (verif.rs:L77, L82-L126).
- **Why here:** After the cheap linkage cardinality check, before the original-id equality check and the linkage check that needs module handles.
- **Assumes:** nothing about `dependencies`; this step is intra-package only (mod.rs:L85 comment; verif.rs has no dependency input).
- **Establishes:** every module in `validated_package` has self address `package.original_id` (deser.rs:L44); `validated_package.{original_id, version_id, linkage_table}` equal the `SerializedPackage` fields (deser.rs:L26, L72-L76; deser_ast.rs:L30-L36; verif.rs:L32-L58). All paths through both translate functions that return `Ok` pass every module through the checks (loops at deser.rs:L29 and verif.rs:L48 have no early `Ok` exit).
- **Depended on by:** L58 (original_id), L70 (modules, original_id), caller JIT (runtime/mod.rs:L455-L461).

```rust
// L58-L66
if validated_package.original_id != original_id { return Err(UNKNOWN_INVARIANT_VIOLATION_ERROR ... Location::Package(validated_package.version_id)); }
```
- **What:** The package's self-declared `original_id` (the `SerializedPackage` field, via deser.rs:L26) must equal the caller-supplied `original_id`.
- **Why here:** After L56 so the id is taken from the translated package. The value compared is the same `SerializedPackage.original_id` field that was available before L56; ordering means verification cost is incurred before this rejection.
- **Assumes:** nothing further.
- **Establishes:** all module self addresses == caller's `original_id` (combining with deser.rs:L44). Does not relate `original_id` to any linkage table key.
- **Depended on by:** L70 (relocation map self entry uses `package_to_publish.original_id`, linkage.rs:L74-L77); caller's `runtime_packages` map keyed by `runtime.original_id` (runtime/mod.rs:L464-L468).

```rust
// L70-L71
verify_linkage_and_cyclic_checks_for_publication(&validated_package, &dependencies)?;
Ok(validated_package)
```
- **What:** Builds `relocation_map: HashMap<OriginalId, VersionId>` from each dep `(original_id, version_id)` plus `(package.original_id -> package.original_id)` (linkage.rs:L68-L78); runs `verify_package_valid_linkage` and `verify_package_no_cyclic_relationships` over every dependency package (linkage.rs:L81-L85) and then over the new package (linkage.rs:L88-L93).
- **Why here:** Last, because it needs verified modules of both sides.
- **Assumes:**
  - `k == v.version_id` for deps: `debug_assert!` only inside the callee (linkage.rs:L71); established upstream by `resolve_packages` (see above).
  - The self entry in the relocation map is `original_id -> original_id` (linkage.rs:L76), not `original_id -> package.version_id` as in the linkage table. Lookups against the self address only occur for module ids not found in the package's own module map (linkage.rs:L157-L158 and L115-L116 check the bundle first); such a lookup resolves `cached_packages.get(original_id)` (linkage.rs:L165, L121-L122), which hits only if some dependency has `version_id == original_id`.
  - `HashMap` collection from an iterator overwrites on duplicate keys, so the chained self entry replaces any dependency entry keyed by the same `original_id`. Whether such a dependency can reach here depends on the self-entry precondition above.
  - Dependency packages' modules are resolved only through `relocation_map`, which is derived from `dependencies`, not from `link_context`. Their equivalence rests on L54.
- **Establishes (callee):** each module's immediate dependencies (not in its own package) resolve through relocation map to a module in `dependencies`, and `dependencies::verify_module` passes against those (linkage.rs:L152-L178); no cycle among modules reachable via the same resolution (linkage.rs:L102-L137).
- **Depended on by:** caller's JIT and dispatch table construction (runtime/mod.rs:L455-L475), and the adapter's Sui verifier / init execution (context.rs:L1149 onward).

**Cross-Function Dependencies:**
- Callee `validate_against_link_context` (internal, analyzed above): relied on to prove the resolved dep set is exactly the table minus one entry, and that deps have distinct original ids. Does not identify the extra entry.
- Callee `validate_package` (internal, L87-L104) -> `deserialization::translate::package` (deser.rs:L20-L77) and `verification::translate::package` (verif.rs:L27-L59): relied on for intra-package well-formedness and for module-address == `package.original_id`. Every error path returns before producing a package; no partial output.
- Callee `move_bytecode_verifier::verify_module_with_config_unmetered`, `script_signature::verify_module_function_signature_by_name`, `CompiledModule::deserialize_with_config` (external-source-available, not read for this analysis): relied on for bytecode/signature well-formedness and bounded deserialization.
- Callee `verify_linkage_and_cyclic_checks_for_publication` (internal, linkage.rs:L58-L96): relied on for cross-package handle compatibility and acyclicity. Uses a relocation map whose self entry differs from the linkage table's self entry (linkage.rs:L74-L77).
- Callee `move_bytecode_verifier::{dependencies, cyclic_dependencies}::verify_module` (external-source-available, not read): relied on for the actual compatibility/cycle logic.
- Callers: only `MoveRuntime::validate_package` (runtime/mod.rs:L449). Its callers in the adapter: `Context::publish_and_verify_modules` (context.rs:L1126-L1147), reached from `publish_and_init_package` (context.rs:L1341) and `upgrade` (context.rs:L1392). The adapter maps VM errors via `convert_linked_vm_error` (context.rs:L1147). `MoveRuntime::validate_package`'s doc states no user input should cause an invariant violation (runtime/mod.rs:L398-L399); L54 and L58 return `UNKNOWN_INVARIANT_VIOLATION_ERROR`, so the design treats those as unreachable given adapter construction.
- Shared state: `MoveCache.package_cache` is populated by the caller's `resolve_packages` before this function (pkg_res.rs:L113, L266) and read afterwards by `jit_package_for_publish`, which returns an existing cache entry at `version_id` instead of the newly verified package if one exists (pkg_res.rs:L211-L214).
- Invariant couplings:
  - Bijection between linkage table and loaded package set = `LinkageContext::new` injectivity (linkage_context.rs:L37-L48) + cardinality (L132) + mapping (L143) + resolver key fidelity (pkg_res.rs:L100-L114, release-unchecked count at L119-L122).
  - Identity of the "+1" entry: value half via runtime/mod.rs:L438; key half (`== original_id`) only via adapter `into_serialized_move_package` (move_package.rs:L611-L614). Inside `validate_for_publish` nothing compares `link_context.linkage_table().get(&original_id)` with `package.version_id`.
  - `original_id` param == `package.original_id` (L58) and module addresses == `package.original_id` (deser.rs:L44) jointly pin module addresses to the caller's id.
  - `validated_package.linkage_table` (used later by `effective_system_packages_for`, pkg_res.rs:L300-L307, and by the JIT) equals `link_context`'s table only by caller construction (runtime/mod.rs:L425).

**Open Questions:**
- unclear; need to inspect whether a `SerializedPackage` reaching `MoveRuntime::validate_package` can come from any path other than `MovePackage::into_serialized_move_package` (e.g., test harnesses, genesis, `Mode::packages_are_predefined` at context.rs:L1318-L1320), since the self-entry key is established only there.
- unclear; need to inspect `ResolvedLinkage::update_for_publication` and `MovePackage::new_initial` / `new_upgraded` (move_package.rs:L265, L289) to confirm `package.id()` equals `original_id` for fresh publishes and that `original_package_id()` (move_package.rs:L524-L533) equals the `original_id` argument on both publish and upgrade.
- unclear; need to inspect `dependencies::verify_module` and `cyclic_dependencies::verify_module` to determine whether the self relocation entry `original_id -> original_id` (linkage.rs:L76) is ever consulted for a module id at the self address that is not in the package, and what `cached_packages.get(original_id)` returns when a dependency's `version_id == original_id` (the v1 of an upgraded package).
- unclear; need to inspect whether the publishing package's `version_id` can already be present in `MoveCache.package_cache` (e.g., dev-inspect/dry-run reusing a fresh id), in which case `jit_package_for_publish` (pkg_res.rs:L212-L213) returns the cached package rather than the one validated here.
- unclear; need to inspect `LinkageContext` conversions in the adapter (`ExecutableLinkage`) for construction paths other than `LinkageContext::new`.
