## `verify_package_valid_linkage` and `verify_linkage_and_cyclic_checks_for_publication` in external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs (L143-L180, L58-L96)

File abbreviations used below:
- `linkage.rs` = external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs
- `deps.rs` = external-crates/move/crates/move-bytecode-verifier/src/dependencies.rs
- `cyc.rs` = external-crates/move/crates/move-bytecode-verifier/src/cyclic_dependencies.rs
- `vmod.rs` = external-crates/move/crates/move-vm-runtime/src/validation/mod.rs
- `rt.rs` = external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs
- `pr.rs` = external-crates/move/crates/move-vm-runtime/src/runtime/package_resolution.rs
- `ff.rs` = external-crates/move/crates/move-binary-format/src/file_format.rs
- `dser.rs` = external-crates/move/crates/move-vm-runtime/src/validation/deserialization/translate.rs

**Purpose:** These are the only cross-package checks in the new VM that an imported handle (datatype handle, function handle, module handle) in one module matches the definition in the specific dependency version selected by the linkage context. Per-module bytecode verification (`verification::translate::module`, verification/translate.rs:L66) sees one module only; the JIT and dispatch tables consume modules afterwards on the premise that every imported function/type handle resolves, has the declared arity/signature/abilities, and is visible to the importer. `verify_linkage_and_cyclic_checks_for_publication` additionally applies the check (and the cycle check) to a package that is not yet in the cache, before JIT and before `init` runs. `verify_package_valid_linkage` is also called by the execution-time sibling `verify_linkage_and_cyclic_checks` (linkage.rs:L46).

---

**Inputs & Assumptions:**

`verify_linkage_and_cyclic_checks_for_publication` (linkage.rs:L58-L61):
- `package_to_publish: &Package` — publisher bytes after deserialization + per-module verification (vmod.rs:L56). Trust: untrusted content, but has passed `validate_package` (deserialize + `verify_module_with_config_unmetered` + native check).
- `cached_packages: &BTreeMap<VersionId, &Package>` — dependencies resolved from the linkage table excluding the published version id (rt.rs:L433-L439, L445-L448). Content is on-chain bytecode (untrusted authors) but verified per-module on load (pr.rs:L151). Set membership comes from the publisher-supplied / adapter-built `pkg.linkage_table` (rt.rs:L425).

`verify_package_valid_linkage` (linkage.rs:L143-L147):
- `package: &[&Module]` — all modules of one package, produced by `Package::as_modules()` which iterates `BTreeMap<ModuleId, Module>` values (verification/ast.rs:L44-L46). Order = ModuleId order.
- `cached_packages` — as above; keys are version ids.
- `relocation_map: &HashMap<OriginalId, VersionId>` — built by the caller (linkage.rs:L33-L39 or L68-L78).

Preconditions and where each is established:
1. Every module in a `Package` has `self_id().address() == package.original_id`. Established by dser.rs:L44 (MISMATCHED_MODULE_IDS_IN_PACKAGE) for every package that goes through `validate_package` (vmod.rs:L98), which covers both loaded dependencies (pr.rs:L151) and the published package (vmod.rs:L56).
2. `Package.modules` is keyed by the module's own `self_id()`. Established by deserialization/ast.rs:L32 (key = `m.self_id()`), carried through verification/translate.rs:L48-L49.
3. `cached_packages` key equals `v.version_id`. Only `debug_assert!` at linkage.rs:L36 / L71 (compiled out in release). By construction, `resolve_packages` inserts freshly loaded packages with key `pkg.verified.version_id` (pr.rs:L114); cached hits use the requested id as key (pr.rs:L101) and rely on the cache having been populated under the same id (pr.rs:L266). The store returning a package whose `version_id` differs from the requested id is only `debug_assert_eq!`'d (pr.rs:L194).
4. At most one cached package per `original_id` (otherwise the HashMap at L33-L39 / L68-L73 keeps only the last). Established by `validate_against_link_context` (vmod.rs:L142-L153): each package's `original_id` must map to its own key in the linkage table; a `BTreeMap<OriginalId, VersionId>` cannot map one original id to two version ids. Called before these functions on both paths (vmod.rs:L54, L80).
5. Module handles, address identifiers and identifiers within each module are free of duplicates, so `ModuleHandle` value equality coincides with `ModuleId` equality. Established by `DuplicationChecker::verify_module` (move-bytecode-verifier/src/verifier.rs:L112; check_duplication.rs:L58-L108), run for every module in `validate_package`. Bounds are established by `BoundsChecker` (verifier.rs:L106). `dependencies::verify_module` indexes handles/signatures without re-checking bounds (deps.rs:L77, L130, L266, L479) and relies on this.
6. For publication: the linkage table entry that is not among `cached_packages` is the to-publish package keyed by its own `original_id`. `validate_against_link_context` checks only cardinality `packages.len()+1` (vmod.rs:L127-L132) and the dependency entries (vmod.rs:L142-L153); `all_package_dependencies_except(pkg.version_id)` (rt.rs:L438; shared/linkage_context.rs:L69-L79) removes whichever entry has value `pkg.version_id`. Nothing found in the VM that checks that this entry's key equals `package_to_publish.original_id`. The `original_id` given by the adapter is compared to the package's own original id (vmod.rs:L58-L66), not to the linkage table key.

---

**Outputs & Effects:**
- Both return `VMResult<()>`. No state writes; the functions only read `cached_packages` and the modules. `#[instrument(... ret)]` on L101/L142 emits trace spans only.
- Success of `verify_package_valid_linkage` means: for every module `m` in `package`, every `ModuleId` in `m.immediate_dependencies()` resolved to a `CompiledModule` (bundle first, then relocation+cache), and `dependencies::verify_module(m, deps)` returned Ok (linkage.rs:L152-L178).
- Success of the publication function means all cached packages and the to-publish package each passed `verify_package_valid_linkage` and `verify_package_no_cyclic_relationships` under a relocation map that additionally maps `package_to_publish.original_id -> package_to_publish.original_id` (linkage.rs:L74-L77).
- Errors: MISSING_DEPENDENCY with `Location::Undefined` (L161-L163) or `Location::Package(version_id)` (L165-L172); anything from `dependencies::verify_module` finished at `Location::Module(m.self_id())` (deps.rs:L161-L162): MISSING_DEPENDENCY, TYPE_MISMATCH, LOOKUP_FAILED, CALLED_SCRIPT_VISIBLE_FROM_NON_SCRIPT_VISIBLE; CYCLIC_MODULE_DEPENDENCY from cyc.rs:L61. No `Err` path in these two linkage.rs functions leaves partial state.

---

**Block-by-Block:**

### `verify_linkage_and_cyclic_checks_for_publication`

```rust
// L68-L78
let relocation_map: HashMap<OriginalId, VersionId> = cached_packages
    .iter()
    .map(|(k, v)| { debug_assert!(k == &v.version_id); (v.original_id, v.version_id) })
    .chain(std::iter::once((package_to_publish.original_id, package_to_publish.original_id)))
    .collect();
```
- **What:** Builds original-id -> version-id relocation from the dependency set, then appends a self entry for the package being published whose *value* is the original id (not `package_to_publish.version_id`).
- **Why here:** Needed before any lookup; the self entry lets lookups at the publisher's own address succeed at the relocation step (L118-L120, L160) instead of failing with a missing key.
- **Assumes:** Precondition 3 (key == version_id; debug-only check at L71) and precondition 4 (unique original ids). The chained element is last, so `HashMap::from_iter` overwrites any dependency entry with the same original id (std insert semantics); whether such a dependency can exist depends on precondition 6.
- **Establishes:** `relocation_map[package_to_publish.original_id] == package_to_publish.original_id`. For a fresh publish `version_id == original_id` and this is the true identity; for an upgrade (`version_id != original_id`) the map points to the *original* version id, which is a key in `cached_packages` only if the v1 package is among the resolved dependencies.
- **Depended on by:** Every non-bundled lookup at the publisher's own address in L83, L84, L92, L93. For the to-publish package's own modules, the bundle map is consulted first (L157, L115) so the self entry is only reached for module ids at the publisher address that are not in the bundle; those resolve to `cached_packages.get(original_id)`.

```rust
// L81-L85
for package in cached_packages.values() {
    let package_modules = package.as_modules().into_iter().collect::<Vec<_>>();
    verify_package_valid_linkage(&package_modules, cached_packages, &relocation_map)?;
    verify_package_no_cyclic_relationships(&package_modules, cached_packages, &relocation_map)?;
}
```
- **What:** Re-verifies linkage and cycles for every dependency package in this linkage context.
- **Why here:** Dependencies were verified per-module when loaded (pr.rs:L151) but never against *this* publisher-chosen linkage (pr.rs:L143-L144 FIXME notes packages are not checked against their own defined linkages). Doing it before the new package means a failure is attributed to a dependency first.
- **Assumes:** Nothing in any dependency legitimately references the to-publish original id; if one does, the lookup goes to `cached_packages.get(original_id)` via the self entry (L160, L165), which for a fresh publish yields MISSING_DEPENDENCY.
- **Establishes:** All dependency modules link against the versions this linkage table selects. Cost is proportional to the total size of all dependency packages and runs on every publish; no gas or meter is consulted in this function (the `_gas_meter` in rt.rs:L409 is unused).
- **Depended on by:** VMDispatchTables construction for the one-off publish VM (rt.rs:L464-L475), which is built from these dependencies without passing through `validate_for_vm_execution`.

```rust
// L88-L93
let package_modules = package_to_publish.as_modules().into_iter().collect::<Vec<_>>();
verify_package_valid_linkage(&package_modules, cached_packages, &relocation_map)?;
verify_package_no_cyclic_relationships(&package_modules, cached_packages, &relocation_map)?;
```
- **What:** Checks the new package's modules against sibling modules (bundle) and the selected dependency versions, then checks cycles.
- **Why here:** The package is not in `cached_packages`, so the generic function cannot be used; the bundle-first lookup inside both callees supplies its own modules.
- **Establishes:** On Ok, every imported handle in the new package matches the selected dependency version (see callee analysis below), and no cycle through each *start* module examined by the cycle walker was found (see cycle-walker coverage note below).
- **Depended on by:** `jit_package_for_publish` (rt.rs:L455-L461) and the dispatch tables (rt.rs:L470-L475) used to run `init`.

### `verify_package_valid_linkage`

```rust
// L148-L151
let package_module_map = package.iter().map(|m| (m.value.self_id(), m)).collect::<BTreeMap<_, _>>();
```
- **What:** Indexes the package's own modules by `self_id`.
- **Establishes:** Intra-package references resolve to the bundled module, never to a cached version of the same package.
- **Depended on by:** L157.

```rust
// L152-L176
for m in package {
    let imm_deps = m.value.immediate_dependencies();
    let module_deps = imm_deps.iter().map(|module_id| {
        if let Some(m) = package_module_map.get(module_id) { Ok(&m.value) }
        else {
            let Some(version_id) = relocation_map.get(module_id.address()) else { return Err(MISSING_DEPENDENCY @ Undefined) };
            let package = cached_packages.get(version_id).ok_or_else(|| MISSING_DEPENDENCY @ Package(version_id))?;
            let module = package.modules.get(&module_id.to_owned()).ok_or_else(|| MISSING_DEPENDENCY @ Package(version_id))?;
            Ok(&module.value)
        }
    }).collect::<VMResult<Vec<&CompiledModule>>>()?;
```
- **What:** For each non-self module handle in `m`, finds the concrete `CompiledModule` in the chosen version, failing closed on each of the three lookup misses.
- **Why here:** `dependencies::verify_module` needs exactly the dependency modules; it panics (not errors) if one referenced by a function handle is missing (deps.rs:L133 `.unwrap()`), so completeness must be guaranteed here.
- **Assumes:** `immediate_dependencies()` returns every module handle except the self handle (ff.rs:L2685-L2692; filter is by `ModuleHandle` value, not index). Combined with precondition 5, every function handle whose `module != self_module_idx` (deps.rs:L125) names a module that is in `imm_deps`. The lookup key is the handle's `ModuleId` whose address is an original id; cached `Package.modules` keys have address == that package's original id (preconditions 1, 2), and `relocation_map` maps original id to the version whose `original_id` equals it (L37 / L72), so a hit returns a module whose `self_id()` equals the requested `module_id`.
- **Establishes:** `module_deps` contains, for every id in `imm_deps`, one module with that `self_id`. Every one of the three failure branches returns before `dependencies::verify_module` is called.
- **Depended on by:** deps.rs:L51-L55 (dependency_map keyed by `d.self_id()`), deps.rs:L133 unwrap, deps.rs:L182, L204, L247.

```rust
// L177
dependencies::verify_module(&m.value, module_deps)?;
```
- **What:** Runs the cross-module handle-vs-definition checks (detailed below).
- **Establishes:** see callee section.

---

**Cross-Function Dependencies:**

- Callee `dependencies::verify_module` (external-source-available, deps.rs:L157-L175). What the caller depends on it to establish, path by path:
  - Context construction (deps.rs:L44-L154), runs before any check:
    - `dependency_map` keyed by `d.self_id()`, excluding any dep equal to self (L51-L55).
    - For each dependency module: `datatype_id_to_handle_map` gets every struct def and enum def (L76-L91); `func_id_to_handle_map` gets a function only if `Public`, or `Friend` with the importer's `self_id` in the dependency's `immediate_friends()` (L100-L109). Private functions (including private `entry`) are never inserted (L103).
    - For every foreign function handle, `context.dependency_map.get(&dep_module_id).unwrap()` (L133). This is a panic, not an error, if the handle's module is not in `dependency_map`. Linkage.rs guarantees presence via L152-L176 given precondition 5; nothing else guards it.
    - Function handles whose target is not a defined function in the dependency are skipped here (L135-L138) and are caught later by `verify_imported_functions`.
  - `verify_imported_modules` (L177-L192): every non-self module handle's id must be in `dependency_map`, else MISSING_DEPENDENCY. Given linkage.rs L152-L176, always satisfied when reached.
  - `verify_imported_structs` (L194-L235): for every foreign datatype handle, the (module, name) must be a defined struct or enum in the linked dependency (else LOOKUP_FAILED, L225-L231); declared abilities ⊆ defined abilities (L212, L324-L329); same type-parameter count, local phantom ⇒ defined phantom, defined constraints ⊆ local constraints (L213-L216, L356-L395). Not compared: field layout, variant set, whether the type is struct vs enum (the map holds both kinds under one key space, L79-L90).
  - `verify_imported_functions` (L237-L317): for every foreign function handle, (module, name) must be in `func_id_to_handle_map` (i.e., defined, and public or friend-visible), else LOOKUP_FAILED (L307-L313); type-parameter count equal and defined constraints ⊆ local constraints (L255-L264, L333-L352); parameter and return signatures equal token-by-token (L266-L305, L397-L470). Datatype identity in `compare_structs` (L472-L495) is `(module_id_for_handle, name)` from each module's own handle tables; the module id address is the original id, so identity is by original id + module + name, not by defining id or version. `is_entry` of the dependency function is not compared for V5+ modules (only used for the pre-V5 script set, L145-L150).
  - `verify_all_script_visibility_usage` (L497-L523): no-op for module version >= V5 (L57-L61, L499-L501).
  - All paths return `PartialVMResult`; mapped to `Location::Module(self_id)` (L162). The only non-error failure mode is the `unwrap` at L133.
- Callee `verify_package_no_cyclic_relationships` (internal, linkage.rs:L102-L138) -> `cyclic_dependencies::verify_module` (external-source-available, cyc.rs:L13-L66):
  - Closure resolution (L114-L129): bundle first (L115), else relocation (L118-L120) then cached package (L121-L123); any miss is MISSING_DEPENDENCY. Uses the same resolution order as `verify_package_valid_linkage`.
  - `detect_cycles` (cyc.rs:L32-L55) reports a cycle only when the DFS reaches the *start* module `self_id` (L41-L43, L59-L61). Modules already in `visited` are not expanded again (L45-L46). A cycle among modules reachable from the start module but not containing it returns `Ok(false)`.
  - The walker then removes every visited module from `to_visit_modules` (linkage.rs:L132-L134), so those modules are never used as a start module. Start order is `pop_last` over `BTreeMap<ModuleId, _>` (L113), i.e. descending ModuleId. Consequence (structural): a cycle among modules {B, C} of the package is reported only if one of B, C is popped before any other package module whose DFS reaches B or C. The unit tests cover a cycle containing every module (unit_tests/loader_tests.rs:L1223-L1239) and a cycle plus a disconnected module "A" (L1241-L1259) where the cycle members sort after "A" and are popped first; no test covers a start module that sorts after the cycle members and reaches into the cycle. No other CYCLIC_MODULE_DEPENDENCY producer exists in move-vm-runtime, move-bytecode-verifier or sui-execution/latest (grep: only cyc.rs:L61).
  - Recursion in `detect_cycles` has no explicit depth bound; depth is bounded by the number of distinct modules reachable in the linkage (each module visited once, cyc.rs:L45).
- Callee `CompiledModule::immediate_dependencies` (external-source-available, ff.rs:L2685-L2692): all module handles except those equal (by value) to the self handle.
- Callee `Package::as_modules` (internal, verification/ast.rs:L44-L46): BTreeMap values, sorted by ModuleId.
- Callers:
  - `validate_for_publish` (vmod.rs:L36-L72) calls the publication function at L70, after `validate_against_link_context(true, ...)` (L54), `validate_package` (L56), and the original-id equality check (L58-L66). Reached from `MoveRuntime::validate_package` (rt.rs:L404-L492), which the adapter's publish/upgrade path calls. The caller assumes that on Ok the package links against its dependencies and is acyclic, and proceeds to JIT (rt.rs:L455) and build dispatch tables (rt.rs:L470).
  - `verify_linkage_and_cyclic_checks` (linkage.rs:L30-L51) calls `verify_package_valid_linkage` at L46; reached from `validate_for_vm_execution` (vmod.rs:L76-L82) <- `load_and_cache_vtables` (rt.rs:L372), whose result is cached per `LinkageHash` (rt.rs:L383-L384).
- Shared state: reads only. `cached_packages` values are `&*pkg.verified` from the process-wide `MoveCache.package_cache` (rt.rs:L445-L448, pr.rs:L100). Package entries in the cache are immutable `Arc`s; no locking in this function.
- Invariant couplings:
  - The dispatch tables and JIT resolve cross-package calls by (original id -> linked version) the same way as `relocation_map`; this check validates handles only against the version chosen by the relocation map at check time. For execution, validation is keyed per `LinkageHash` (rt.rs:L383-L384), so each distinct linkage table is re-validated before its vtables are cached.
  - Type identity checked here is (original-id address, module, name) (deps.rs:L490). Defining-id / type-origin mapping is not consulted here; it is established elsewhere (jit datatype descriptors from `type_origin_table`, per ORIENTATION.md).
  - For publication, the dispatch tables used for `init` (rt.rs:L464-L475) are keyed by `pkg.runtime.original_id` with the published package chained last, so a dependency sharing the publisher's original id would be overwritten there too, mirroring L74-L77.

---

**Open Questions:**
- unclear; need to inspect the sui-adapter publish/upgrade path (sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs, `publish_and_init_package` / `upgrade`) to confirm how `pkg.linkage_table` is built, and whether its entry for the new package is always `original_id -> version_id` (precondition 6 has no VM-side check).
- unclear; for an upgrade, can the resolved dependency set contain a package whose `version_id == package_to_publish.original_id` (i.e., v1 of the package being upgraded) so that the self relocation entry (L74-L77) resolves non-bundled references at the publisher's address to v1 modules? Need to inspect adapter linkage construction for upgrades.
- unclear; whether any other layer (adapter publish path, sui-verifier, client-independent metered verifier in sui-execution/src/latest.rs) rejects intra-package module cycles independently of `verify_package_no_cyclic_relationships`, given the start-module coverage property of cyc.rs:L41-L46 combined with linkage.rs:L132-L134. Need to inspect sui-execution/latest/sui-adapter/src/adapter.rs and execution_engine.rs publish handling.
- unclear; what downstream code (JIT translate, dispatch tables) assumes about datatype kind (struct vs enum) of an imported handle, since deps.rs keys structs and enums in one map (L79-L90) and `compare_structs` does not distinguish them. Need to inspect jit/optimization and execution/dispatch_tables.rs handling of imported datatype handles.
- unclear; whether `cached_package_at` hits (pr.rs:L100-L101) can return a package whose `version_id` differs from the requested key; if so L37/L72 would insert a relocation value that is not a key of `cached_packages`. Need to inspect cache/move_cache.rs `add_package_to_cache` / `cached_package_at` keying.
- unclear; total unmetered work of L81-L85 per publish (all dependency packages re-verified, deps.rs Context builds maps over all defs of each dependency) versus protocol limits on number/size of dependencies. Need to inspect adapter-side limits on linkage table size.
