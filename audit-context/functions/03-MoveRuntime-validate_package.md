## `MoveRuntime::validate_package` in external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs (L404-L492)

All paths below are relative to repo root. `mod.rs` = `external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs`; `pr.rs` = `.../runtime/package_resolution.rs`; `val.rs` = `.../validation/mod.rs`; `link.rs` = `.../validation/verification/linkage.rs`; `lc.rs` = `.../shared/linkage_context.rs`; `dt.rs` = `.../execution/dispatch_tables.rs`; `ctx.rs` = `sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs`.

**Purpose:** The VM-side gate for Publish and Upgrade. It takes a publisher-supplied `SerializedPackage` (module bytes, original/version ids, type-origin table, linkage table), resolves every other package named in that linkage table into the process-wide package cache, deserializes + bytecode-verifies + linkage/cycle-checks the new package against those dependencies, JIT-compiles the new package *without* inserting it into the package cache, and returns both the verified AST (for the adapter to push into its per-tx store) and a one-off `MoveVM` whose dispatch tables contain the deps plus the new package (for running `init`). Without it, unverified bytecode would reach the JIT/interpreter.

---

**Inputs & Assumptions:**
- `package_store: impl ModuleResolver` — source for dependency bytes. In production it is `TransactionPackageStore` (`ctx.rs:L1136`), which serves in-tx new packages first, then the per-tx cache, then the backing store (`transaction_package_store.rs:L130-L157`). Trust: trusted for backing-store content; in-tx new packages were themselves produced by earlier publish commands of the same untrusted tx.
- `original_id: OriginalId` — the id the caller expects the package to have. Adapter passes fresh id for publish (`ctx.rs:L1325`) or predecessor's original id for upgrade (`ctx.rs:L1373`). Trust: adapter-computed.
- `pkg: SerializedPackage` — produced by the adapter via `MovePackage::into_serialized_move_package` (`ctx.rs:L1133`); module bytes are publisher-controlled (id-substituted, `ctx.rs:L1326`/`L1374`), `linkage_table` is derived from deps + a self entry (`crates/sui-types/src/move_package.rs:L607-L615`), `type_origin_table` from `MovePackage::new_initial/new_upgraded`. Trust: bytes untrusted; tables adapter-derived from untrusted dependency choices.
- `_gas_meter` — unused (L409, underscore-prefixed, never referenced in L412-L491).
- `native_extensions` — moved into the returned VM (cloned at L484).
- Implicit: `self.cache` (process-wide `MoveCache`: package_cache DashMap, interner, system_packages), `self.natives`, `self.vm_config`, `self.telemetry`.
- Preconditions:
  - Linkage table has an entry `original_id -> pkg.version_id`. Established by the adapter (`move_package.rs:L611-L614`, chained last so it overrides any dep entry at the same key). Inside the VM, only the *existence* of some key mapping to `pkg.version_id` is implied (see L449 block); that the key equals `original_id` — nothing found in the VM.
  - `pkg.version_id` is not already present in `self.cache.package_cache`. Established by nothing in this function; the adapter uses `fresh_id()` (`ctx.rs:L1325`, `L1380`). If violated, `jit_package_for_publish` returns the cached package (`pr.rs:L212-L214`).
  - `pkg.type_origin_table` is complete and truthful. The JIT consumes it verbatim (`jit/execution/translate.rs:L242-L262`, `L578-L599`, comment L578-L579 "responsibility of the adapter"). VM-side validation of its contents: nothing found.

**Outputs & Effects:**
- Returns `(verif_ast::Package, MoveVM)` on success (L487).
- State writes: dependency packages not already cached are loaded, verified, JIT'd and inserted into the process-wide `package_cache` (via `resolve_packages` → `jit_and_cache_package` → `add_package_to_cache`, `pr.rs:L110-L114`, `L266`). These inserts persist even if validation of the new package later fails (no rollback on any error path, L433-L487).
- The published package itself is NOT inserted into `package_cache` (`pr.rs:L204-L231` has no `add_package_to_cache`) and the dispatch tables are NOT inserted into `linkage_vtables` (no `add_linkage_tables_to_cache` in L404-L492; contrast `mod.rs:L383-L384`).
- Interner writes: JIT interns identifiers of the new package into the global interner (`jit/execution/translate.rs:L255-L256`, `L328`, `L341`, `L451`) — these persist regardless of outcome.
- Telemetry: one transaction record per call (`telemetry.rs:L343-L351`), timers at L420, L440-L443, L451, L489.

---

**Block-by-Block:**

```rust
// L418-L420
let vm_telemetry = self.telemetry.clone();
self.telemetry.with_transaction_telemetry(|txn_telemetry| {
    let total_timer = txn_telemetry.make_timer(...Total);
```
- **What:** Wraps the whole body in a per-call telemetry context; `with_transaction_telemetry` runs the closure then records (`telemetry.rs:L347-L350`), independent of result.
- **Assumes:** Nothing security-relevant; telemetry does not influence the result.

```rust
// L422, L488
let result = try_block! { ... };
```
- **What:** `try_block!` is an immediately-invoked closure (`shared/mod.rs:L27-L34`), so `?` exits only the block; L489 still reports the timer on every path.

```rust
// L425
let link_context = LinkageContext::new(pkg.linkage_table.clone())?;
```
- **What:** Builds the linkage context from the package's own table.
- **Establishes:** Linkage table values (VersionIds) are pairwise distinct (`lc.rs:L37-L48`). Error is `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`lc.rs:L41-L45`), i.e. an invariant-class status even though the table is derived from untrusted dep choices. The docstring (L398-L399) says no user input should produce an invariant violation; that property rests on the adapter never producing duplicate values.
- **Does not establish:** any relationship between keys and `original_id` / `pkg.version_id`.
- **Depended on by:** the cardinality argument at `val.rs:L127-L140` (injectivity comment `val.rs:L108-L109`).

```rust
// L433-L439
let pkg_dependencies = package_resolution::resolve_packages(
    package_store, txn_telemetry, &self.cache, &self.natives,
    link_context.all_package_dependencies_except(pkg.version_id)?,
)?;
```
- **What:** Requests every VersionId in the table except `pkg.version_id` (`lc.rs:L69-L79`, never errors).
- **Callee paths (`pr.rs:L84-L124`):**
  1. Cache hit per id (`pr.rs:L100-L101`): returns the cached `Arc<Package>` keyed by requested id.
  2. Cache miss: `load_and_verify_packages` (`pr.rs:L129-L155`) → `load_packages` (`pr.rs:L161-L198`): store error → `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`pr.rs:L181-L188`); any `None` → `LINKER_ERROR` (`pr.rs:L171-L178`). Returned `pkg.version_id == requested id` is only `debug_assert_eq!` (`pr.rs:L193-L195`) — in release nothing checks it; the result is keyed by `pkg.verified.version_id` (`pr.rs:L114`), i.e. the id the store reports, not the one requested.
  3. Each loaded package goes through `validate_package` (deserialize + per-module verifier + natives check, `val.rs:L87-L104`); verification/deserialization failures are remapped to `UNEXPECTED_VERIFIER_ERROR`/`UNEXPECTED_DESERIALIZATION_ERROR` by `expect_no_verification_errors` (`pr.rs:L111`, `shared/logging.rs:L14-L37`).
  4. `jit_and_cache_package` (`pr.rs:L240-L282`): cache re-check (`L250`), JIT, `add_package_to_cache` (first writer wins, `move_cache.rs:L136-L146`), then re-read with `expect` (panics if absent, `pr.rs:L279-L281`).
  - The final count check is `debug_assert!` only (`pr.rs:L119-L122`).
- **Why here:** Deps must be resolved before linkage verification of the new package.
- **Assumes:** Dependencies loaded here from `TransactionPackageStore` may include packages published earlier in the *same* transaction (`transaction_package_store.rs:L134-L137`); these enter the process-wide `package_cache` keyed by version_id with no tie to transaction commit (`pr.rs:L266`).
- **Establishes (release builds):** a `BTreeMap<VersionId, Arc<Package>>` of deps, each individually deserialized+verified+JIT'd. Cross-package linkage of deps: not here (see `pr.rs:L143-L144` FIXME; done below at `link.rs:L81-L85`).
- **Note on metering:** dependency load/verify/JIT here is not charged to `_gas_meter` (L409 unused).

```rust
// L440-L452
let valdation_timer = ...;
let verified_pkg = {
    let deps = pkg_dependencies.iter().map(|(id, pkg)| (*id, &*pkg.verified)).collect();
    validate_for_publish(&self.natives, &self.vm_config, original_id, pkg, deps, &link_context)
};
txn_telemetry.report_time(valdation_timer);
let verified_pkg = verified_pkg?;
```
- **What:** Full publish validation (`val.rs:L36-L72`), in this order:
  1. `validate_against_link_context(publish=true, ...)` (`val.rs:L122-L156`): requires `deps.len() + 1 == table.len()` (`L127-L140`) and for each dep `table[dep.original_id] == dep_key` (`L142-L154`). Given `LinkageContext::new` injectivity and that `resolve_packages` returned one entry per requested id, the `+1` forces `pkg.version_id` to be a value in the table. The key for that value is some key not equal to any dep's `original_id` (because each dep's `original_id` key is pinned to that dep's version). Whether that key equals `original_id` / `pkg.original_id`: not checked here (nothing found in the VM).
  2. `validate_package` (`val.rs:L87-L104`): `deserialization::translate::package` (module name == map key `L34-L42`; every module address == `pkg.original_id` `L44-L51`; no duplicate names `L54-L61`; non-empty `L65-L70`), then `verification::translate::package` — per-module `verify_module_with_config_unmetered` (`verification/translate.rs:L66`), script-signature check per function (`L68-L76`), natives resolvable and no native structs (`L82-L125`). `type_origin_table`, `linkage_table`, `version` are passed through unchanged (`verification/translate.rs:L51-L58`).
  3. `validated_package.original_id == original_id` else `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`val.rs:L58-L66`). Combined with step 2, every module address == `original_id`.
  4. `verify_linkage_and_cyclic_checks_for_publication` (`link.rs:L58-L96`): relocation map = each dep's `original_id -> version_id` plus `original_id -> original_id` for the new package (`link.rs:L68-L78`, the self entry uses `original_id` for the value, not `pkg.version_id`). Re-verifies every dep's linkage and cycles under this map (`L81-L85`), then the new package (`L88-L93`). Linkage uses `dependencies::verify_module` against resolved dep modules (`link.rs:L143-L180`); in-package module ids are resolved first (`L157`).
- **Establishes:** new package bytecode-verified; its modules link against the exact dep versions in the table; no cycles across new pkg + deps; deps link among themselves under this table.
- **Does not establish:** anything about `type_origin_table` contents; that `pkg.version_id != original_id` or `==` (both legal); anything about the key of the self entry.
- **Error classes:** `validate_against_link_context` and the `original_id` mismatch produce `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`val.rs:L133`, `L144`, `L60`).

```rust
// L455-L461
let published_package = package_resolution::jit_package_for_publish(
    txn_telemetry, &self.cache, &self.natives, self.cache.system_packages(), verified_pkg.clone(),
)?;
```
- **What:** JITs the new package outside the cache (`pr.rs:L204-L231`).
- **Callee paths:**
  1. Cache hit on `verified_pkg.version_id` (`pr.rs:L212-L214`): returns the *already cached* `Arc<Package>` — its `runtime` and `verified` are whatever was cached earlier under that id, not the freshly validated package. The caller then returns the fresh `verified_pkg` (L487) alongside a VM whose tables contain the cached runtime (L464-L468). No comparison between the two: nothing found.
  2. Miss: `effective_system_packages_for` filters pinned system packages to those the new package's linkage table maps to the exact pinned version, excluding self (`pr.rs:L292-L311`); `jit::translate_package` (`jit/mod.rs:L24-L33`) → `to_optimized_form` then `execution::translate::package` (`jit/execution/translate.rs:L218-L303`). JIT errors are finished with `Location::Package(version_id)` (`pr.rs:L225`). No insert into `package_cache`.
- **JIT dependence on inputs:** type descriptors take their defining id from `type_origin_table` (`jit/execution/translate.rs:L580-L599`); a missing entry yields `LOOKUP_FAILED` (`L588-L594`); a present-but-wrong entry is accepted as-is. Arena allocation is bounded by `ArenaBuilder::new_bounded(vm_config)` (`L271`).
- **Why here:** Only after full verification (L452), so the JIT only sees verified bytecode.

```rust
// L464-L468
let runtime_packages = pkg_dependencies.into_values().chain([published_package])
    .map(|pkg| (pkg.runtime.original_id, Arc::clone(&pkg.runtime)))
    .collect::<BTreeMap<OriginalId, _>>();
```
- **What:** Keys runtime packages by `runtime.original_id`. `collect` into `BTreeMap` silently keeps the last value on duplicate keys; the published package is chained last, so it would win a collision.
- **Assumes:** No dep shares `original_id` with the new package. Established by `val.rs:L142-L154` (dep's `original_id` key maps to the dep's version, and the table's `original_id` key maps to `pkg.version_id` per adapter `move_package.rs:L611-L614`) — for the VM alone, established only if the self entry's key is `original_id` (nothing found in VM). Also assumes no two deps share `original_id`: established by `val.rs:L143` + `BTreeMap` key uniqueness of the linkage table.
- **Assumes (cache-hit path):** `published_package.runtime.original_id == original_id`; on path 1 above nothing checks this.

```rust
// L470-L475
let virtual_tables = VMDispatchTables::new(self.vm_config.clone(), self.cache.interner.clone(),
    link_context.clone(), runtime_packages)?;
```
- **What:** `dt.rs:L154-L192`. Builds `defining_id_origins` and errors with `UNKNOWN_INVARIANT_VIOLATION_ERROR` if any defining id appears in two packages (`dt.rs:L164-L176`). `defining_ids` per package = set of `defining_id.address()` over its types (`dt.rs:L1079-L1085`), which for the new package come from `type_origin_table`.
- **Does not check:** that `loaded_packages` keys equal the linkage table keys (comment `dt.rs:L152` "assumes linkage has already occured").
- **Establishes:** defining-id → original-id map is a function (each defining address owned by one package in this VM).

```rust
// L478-L487
let instance = MoveVM { virtual_tables, telemetry: vm_telemetry, vm_config, interner, link_context, native_extensions: native_extensions.clone() };
Ok((verified_pkg, instance))
```
- **What:** Constructs the init VM. Not cached. `type_depths` starts empty (`dt.rs:L190`).

---

**Cross-Function Dependencies:**
- Callee `LinkageContext::new` (internal, `lc.rs:L37-L49`): injectivity of VersionIds; all paths checked.
- Callee `LinkageContext::all_package_dependencies_except` (internal, `lc.rs:L69-L79`): infallible set of values minus `pkg.version_id`.
- Callee `package_resolution::resolve_packages` (internal, `pr.rs:L84-L124`): per-dep load+verify+JIT and global cache insert. Returned-id == requested-id and count checks are debug-only (`pr.rs:L119-L122`, `L193-L195`).
- Callee `ModuleResolver::get_packages` on `TransactionPackageStore` (external-source-available, `transaction_package_store.rs:L190-L195`): serves in-tx new packages, per-tx cache, backing store.
- Callee `validate_for_publish` (internal, `val.rs:L36-L72`) → `validate_against_link_context`, `validate_package`, `verify_linkage_and_cyclic_checks_for_publication`: see block above.
- Callee `move_bytecode_verifier::verify_module_with_config_unmetered`, `script_signature::verify_module_function_signature_by_name`, `dependencies::verify_module`, `cyclic_dependencies::verify_module` (external-source-available, not read in this pass): the per-module safety properties rest on these.
- Callee `jit_package_for_publish` (internal, `pr.rs:L204-L231`): JIT without caching; cache-hit short-circuit.
- Callee `jit::translate_package` (internal, `jit/mod.rs:L24-L33`): consumes type_origin_table verbatim; interns identifiers globally.
- Callee `VMDispatchTables::new` (internal, `dt.rs:L154-L192`): defining-id uniqueness only.
- Callers: `Context::publish_and_verify_modules` (`ctx.rs:L1126-L1164`), reached from `publish_and_init_package` (`ctx.rs:L1340-L1341`) and `upgrade` (`ctx.rs:L1391-L1392`). After return the adapter runs `sui_verify_module_unmetered` on its own `modules` list (`ctx.rs:L1150-L1161`), not on the VM's verified package; for upgrade, `check_compatibility` runs after `validate_package` (`ctx.rs:L1394-L1399`). The adapter then pushes `verified_pkg` into `TransactionPackageStore` and runs `init` with the returned VM (`ctx.rs:L1183-L1197`, `L1263-L1272`), passing its own `ResolvedLinkage` (`ctx.rs:L1338`, `L1390`) — a separate linkage object from `pkg.linkage_table` used at L425.
- Shared state: `MoveCache.package_cache` (writes via deps, read in `jit_package_for_publish`), `MoveCache.system_packages` (read, L459), `IdentifierInterner` (writes via JIT), `TelemetryContext`.
- Invariant couplings: Process-wide `package_cache` is keyed only by VersionId and never evicted (ORIENTATION); this function both populates it (deps) and trusts it (cache-hit in `jit_package_for_publish`). The returned `verified_pkg` and the VM's runtime package are the same logical package only when the cache-miss path was taken.

---

**Open Questions:**
- unclear; need to inspect whether any caller can present a `pkg.version_id` already in `package_cache` (e.g. `execution_mode::System` publish with predefined ids at `ctx.rs:L1318-L1320`, or re-execution / dry-run of the same tx digest in one process after a later command JIT-cached the fresh id via `make_vm`), which would take the `pr.rs:L212-L214` path.
- unclear; need to inspect `ResolvedLinkage::update_for_publication` and whether its table always equals `pkg.linkage_table` used at L425.
- unclear; need to inspect `MovePackage::new_initial` / `new_upgraded` in `crates/sui-types/src/move_package.rs` to confirm type_origin_table completeness and defining-id correctness, since the VM validates neither.
- unclear; need to inspect whether packages pushed into `TransactionPackageStore` by an earlier (later-reverted) command can be JIT-cached into the process-wide cache via this function's dep resolution and later be observed by another tx under the same VersionId.
- unclear; need to inspect `Context::convert_linked_vm_error` for how `UNKNOWN_INVARIANT_VIOLATION_ERROR` from `lc.rs:L41`, `val.rs:L133/L144/L60` is mapped (user error vs invariant).
- unclear; need to inspect move-bytecode-verifier `dependencies::verify_module` to confirm what it checks (ability/signature compatibility of imported handles) since cross-package type safety of the init VM rests on it.
