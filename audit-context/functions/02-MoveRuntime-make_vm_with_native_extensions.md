## `MoveRuntime::make_vm_with_native_extensions` (L288-L339) and `MoveRuntime::load_and_cache_vtables` (L351-L386) in external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs

All line numbers without a file prefix refer to `runtime/mod.rs`. Other files are abbreviated:
`lc` = shared/linkage_context.rs, `mc` = cache/move_cache.rs, `pr` = runtime/package_resolution.rs,
`val` = validation/mod.rs, `lnk` = validation/verification/linkage.rs, `dt` = execution/dispatch_tables.rs,
`tps` = sui-execution/latest/sui-adapter/src/data_store/transaction_package_store.rs,
`ctx` = sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs.

**Purpose:** Produce a `MoveVM` for one linkage context (`OriginalId -> VersionId` table). The VM's
`VMDispatchTables` determine which runtime package (and so which code) a call or type key under a given
`OriginalId` resolves to. `make_vm_with_native_extensions` either reuses a process-wide cached
`VMDispatchTables` keyed by `LinkageHash` or builds one via `load_and_cache_vtables`, which resolves every
package in the linkage, checks the resolved set against the linkage context and cross-package linkage and
cycles, builds the dispatch tables, and inserts them into the process-wide LRU. Without the check in
`load_and_cache_vtables`, a VM could be built over a package set that does not match the linkage table or whose
modules do not link against one another.

---

**Inputs & Assumptions:**

`make_vm_with_native_extensions`:
- `package_store: impl ModuleResolver` — in Sui, `&TransactionPackageStore` (`ctx:L171-L176`). It returns
  committed packages from the backing store and packages published earlier in the same transaction
  (`tps:L130-L157`, `tps:L190-L195`). Trust: trusted store; the package bytes come from untrusted publishers.
- `link_context: LinkageContext` — built by the adapter from linkage analysis
  (`sui-adapter/.../linkage/resolved_linkage.rs:L44`). Its only constructor, `LinkageContext::new`, requires
  version IDs to be unique, so the map is injective (`lc:L37-L49`). The fields are private (`lc:L25`). The only
  other mutator is `add_entry`, which is gated to `debug_assertions`/`testing` (`lc:L86-L104`); it checks
  `contains_key(version_id)` but not injectivity. Trust: derived from untrusted PTB and package dependency data
  through trusted adapter logic.
- `native_extensions: NativeExtensions<'extensions>` — cloned into the VM (L331). Trust: trusted (adapter).
- Implicit: `self.cache` (process-wide `MoveCache`: `package_cache` DashMap, `linkage_vtables` sync QCache,
  `interner`, `system_packages`), `self.vm_config`, `self.natives`, `self.telemetry`.

`load_and_cache_vtables`:
- `package_store: &impl ModuleResolver`, `link_context: &LinkageContext`, `linkage_hash: &LinkageHash`. The
  caller passes `linkage_hash` computed from the same `link_context` (L300, L307, L321). This function does not
  re-check that the hash matches the context.
- `txn_telemetry`: per-call telemetry. It is not load-bearing.

**Preconditions and what establishes them:**
- P1: `link_context` is injective on values. Established by `LinkageContext::new` (`lc:L37-L49`). No other
  production constructor exists (the grep found `LinkageContext::new(` and the debug/testing-only `add_entry`).
- P2: The package bytes returned for a `VersionId` are the same for every `ModuleResolver` used with this runtime,
  for the lifetime of the runtime. Both the per-`VersionId` package cache (`pr:L100`, `pr:L250`) and the
  per-linkage vtable cache (L302-L304) skip `package_store` on a hit. Establishing code: nothing found in this
  module. See Open Questions about framework packages at a fixed `VersionId`, and about packages published
  in a transaction that aborts.
- P3: A cached `VMDispatchTables` stored under key K was built from a context equal to K. Established by
  L377-L384: `link_context.clone()` goes into the vtables and `linkage_hash` goes into the key, and both come
  from the same caller context (L300, L307).

---

**Outputs & Effects:**
- Returns `MoveVM { virtual_tables, vm_config, interner, link_context, native_extensions, telemetry }` (L326-L333)
  or a `VMError`.
- Shared-state writes, all process-wide:
  - `package_cache` inserts for every package in the linkage that was not already cached (`pr:L113`, `pr:L266`).
    These happen before `validate_for_vm_execution` runs (L361-L372). A package that fails cross-package
    linkage stays cached as an individually verified and JIT-compiled package.
  - `linkage_vtables.get_or_insert_with(linkage_hash, vtables)` (L383-L384, `mc:L168-L181`) runs only after
    validation (L372) and construction (L377-L382) have succeeded.
  - `linkage_vtables.clear()` (L319, `mc:L184-L186`) runs on the sanity-check path. It removes every linkage's
    entry, not only this linkage's.
  - Telemetry counters (`telemetry.rs:L343-L351`).
- Postcondition on success: `virtual_tables.link_context == link_context` and `virtual_tables.type_depths` is
  empty. On the miss path this holds because the tables are fresh from `VMDispatchTables::new` (`dt:L182-L190`).
  On the hit path it holds because of the check at L317.

---

**Block-by-Block:**

```rust
// L296-L297, L336-L338
self.telemetry.with_transaction_telemetry(|txn_telemetry| {
    let total_timer = txn_telemetry.make_timer(TimerKind::Total);
    ... txn_telemetry.report_time(total_timer); instance })
```
- **What:** Wraps the work in a per-call telemetry context, which is folded into thread-local counters
  afterwards (`telemetry.rs:L343-L351`).
- **Assumes:** None. The timer is not read by the logic.

```rust
// L299-L300
let instance = try_block! {
    let linkage_hash = link_context.to_linkage_hash();
```
- **What:** `try_block!` is an immediately invoked closure (`shared/mod.rs:L27-L34`), so `?` exits only the
  block and the timer at L336 still runs. `to_linkage_hash` deep-clones the whole `BTreeMap` (`lc:L81-L83`).
- **Establishes:** The key is the entire linkage table. `LinkageHash` equality and hashing are derived over the
  map (`lc:L31-L32`), so two contexts share a cache entry only if their tables are equal.

```rust
// L302-L309
let mut virtual_tables = if let Some(vtables) = self.cache.cached_linkage_tables_at(&linkage_hash) {
    vtables
} else {
    self.load_and_cache_vtables(&package_store, txn_telemetry, &link_context, &linkage_hash)?
};
```
- **What:** Looks up the process-wide LRU. `quick_cache::sync::Cache::get` returns a clone of the stored value
  (`mc:L190-L197`, comment `mc:L194-L195`). `VMDispatchTables` derives `Clone` (`dt:L64`). Its fields are `Arc`s
  (`dt:L66-L72`) except `type_depths`, an owned `quick_cache::unsync::Cache` (`dt:L39`, `dt:L82`).
- **Hit path:** No package resolution, no `validate_for_vm_execution`, and no reading of `package_store`.
  Correctness depends on (a) the entry having been validated when it was built, which holds because insertion
  happens only at L383-L384 after L372 and L377-L382 succeed, and (b) P2.
- **Miss path:** `load_and_cache_vtables` (see below).
- **Race:** Two threads can miss the same key at once. Each builds its own tables. `get_or_insert_with` keeps
  the first one inserted (`mc:L174-L179`). The second thread returns its own freshly built copy (L385), not the
  cached one. Both copies hold the same `Arc<Package>` values, because `resolve_packages` always returns entries
  read back from `package_cache` (`pr:L100`, `pr:L279`), and `add_package_to_cache` keeps the first insertion
  (`mc:L136-L149`).

```rust
// L317-L323
if !virtual_tables.type_depths.is_empty() || link_context != *virtual_tables.link_context {
    error!(...);
    self.cache.drop_all_cached_linkage_tables();
    virtual_tables = self.load_and_cache_vtables(&package_store, txn_telemetry, &link_context, &linkage_hash)?;
}
```
- **What:** A defensive check. If the tables have a non-empty `type_depths` or a different linkage context, it
  clears the whole process-wide vtable LRU and rebuilds.
- **Reachability of each disjunct:**
  - `type_depths` non-empty: execution fills `type_depths` through `&mut self` on the VM's own copy
    (`dt:L534-L566`). The stored LRU value is never handed out by reference, because `get` clones (`mc:L196`).
    The inserted value is `vtables.clone()` of a freshly built table (L384, `dt:L190`). So this disjunct is true
    only if `unsync::Cache::clone` shares state between clones, or if some other code writes to stored entries.
    No other writer exists: the grep found only L384 as a caller of `add_linkage_tables_to_cache`. Whether the
    clone is deep is recorded as an open question.
  - `link_context != *vtables.link_context`: after a hit, the key equals `link_context` (the full map, `lc:L82`),
    and by P3 the stored tables' context equals the key. On the miss path both come from the same argument. So
    this disjunct is false unless P3 is broken.
- **Effects when triggered:** The clear at L319 is process-wide and runs while other threads may be using the
  cache. The rebuild at L320 goes through the full miss path, and its result is not checked again. That is
  consistent, because a fresh `VMDispatchTables::new` always has empty `type_depths` (`dt:L190`). Other threads'
  existing `MoveVM`s are unaffected because they hold owned clones.
- **Note:** On the miss path (L306), the same check runs on tables that were just built. It cannot fire there,
  because `new` produced them (`dt:L182-L190`).

```rust
// L326-L334
let instance = MoveVM { virtual_tables, vm_config: self.vm_config.clone(), interner: self.cache.interner.clone(),
                        link_context, native_extensions: native_extensions.clone(), telemetry: self.telemetry.clone() };
Ok(instance)
```
- **What:** Assembles the VM. `link_context` (the argument) is stored separately from
  `virtual_tables.link_context`. They are equal on success because of L317.
- **Establishes:** Each `MoveVM` owns its `type_depths` cache, so later depth caching does not leak across VMs.

### `load_and_cache_vtables`

```rust
// L360
let all_packages = link_context.all_packages()?;
```
- **What:** The set of `VersionId` values in the linkage table (`lc:L60-L66`). It never fails. Because of P1,
  `|all_packages| == |linkage_table|`.

```rust
// L361-L367
let packages = package_resolution::resolve_packages(package_store, txn_telemetry, &self.cache, &self.natives, all_packages)?;
```
- **What:** Returns `BTreeMap<VersionId, Arc<Package>>`. Paths through `resolve_packages` (`pr:L84-L124`):
  1. **Cached:** `cache.cached_package_at(pkg_id)` finds an entry, which is inserted under the requested key
     (`pr:L100-L101`).
  2. **Uncached:** `load_and_verify_packages` → `load_packages`:
     - `store.get_packages` returns `Err` → `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`pr:L181-L188`).
     - Any `None` entry → `LINKER_ERROR` for that address (`pr:L171-L178`).
     - `pkg.version_id == requested id` is only a `debug_assert_eq` (`pr:L193-L195`). Release builds do not
       check it.
     - Each package goes through `validate_package` (deserialization and per-package verification,
       `val:L87-L104`). Deserialization and verification errors are remapped to
       `UNEXPECTED_{DESERIALIZATION,VERIFIER}_ERROR` (`pr:L111`, `logging.rs:L14-L39`).
     - `jit_and_cache_package` (`pr:L240-L282`) JIT-compiles the package and inserts it under
       `verified_pkg.version_id` (`pr:L247`, `pr:L266`). It then reads the entry back with `expect`, which
       panics if the entry is missing (`pr:L279-L281`). If another thread inserted first, the thread's own
       compilation is discarded and the cached entry is returned (`pr:L250-L252`, `pr:L270-L272`).
     - The result is keyed by `pkg.verified.version_id`, which is the package's own declared ID, not the
       requested ID (`pr:L114`).
  3. The count equality between requested and returned packages is only a `debug_assert!` (`pr:L119-L122`).
- **What the caller depends on it for:** Each returned entry is individually deserialized, verified, and
  JIT-compiled. Nothing in `resolve_packages` guarantees in release builds that the returned keys equal the
  requested set. That is deferred to L372.
- **Linkage used for JIT:** The JIT uses the package's own `linkage_table` to choose direct-call targets among
  pinned system packages (`pr:L292-L311`). The linkage context passed here plays no part. The compiled package
  is cached per `VersionId` (`pr:L266`) and reused under every linkage context. In Sui's production runtime,
  `new_move_runtime` calls `MoveRuntime::new` (`sui-adapter/src/adapter.rs:L49`) with
  `SystemPackages::empty()` (L46), so the effective system-package set is empty (`pr:L296-L298`).
- **FIXME at `pr:L143-L144`:** "should all packages loaded this way be linkage-checked against their defined
  linkages as well?" A package's own declared linkage table is not compared with `link_context` anywhere on
  this path.

```rust
// L368-L372
let validation_packages = packages.iter().map(|(id, pkg)| (*id, &*pkg.verified)).collect();
validate_for_vm_execution(validation_packages, link_context)?;
```
- **What:** `validate_for_vm_execution` (`val:L76-L82`) makes two checks:
  1. `validate_against_link_context(false, ..)` (`val:L122-L156`):
     - `packages.len() == linkage_table.len()`, else `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`val:L127-L140`).
     - For each `(key, pkg)`: `linkage_table.get(&pkg.original_id) == Some(key)`, else
       `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`val:L142-L154`).
     - Together with P1, this gives a bijection between resolved packages and linkage entries. It also means no
       two resolved packages share an `original_id`, which matters for the `BTreeMap` built at L373-L376.
     - The check compares against the map key (`version_id`), not `pkg.version_id`. The key equals
       `pkg.verified.version_id` on the load path (`pr:L114`). On the cached path the key is the requested ID,
       and the entry was inserted under that same `verified.version_id` (`pr:L247`, `pr:L266`;
       `add_package_to_cache` has no other caller). So key == `pkg.version_id` on every path.
       `verify_linkage_and_cyclic_checks` repeats this only as a `debug_assert!` (`lnk:L36`).
  2. `verify_linkage_and_cyclic_checks` (`lnk:L30-L51`):
     - Builds `relocation_map: original_id -> version_id` from the packages themselves (`lnk:L33-L39`). After
       check 1, this map equals the linkage table.
     - For each package, `verify_package_valid_linkage` (`lnk:L143-L180`) resolves every immediate dependency,
       first within the package, then through `relocation_map` → `cached_packages` → `modules`.
       `MISSING_DEPENDENCY` is returned if any step fails. It then runs `dependencies::verify_module` against the
       resolved modules (`lnk:L177`).
     - For each package, `verify_package_no_cyclic_relationships` (`lnk:L102-L138`) runs the cycle check with
       the same resolution.
     - All failure paths return `Err`. No path skips a package: both loops cover every entry (`lnk:L44-L48`).
- **Why here:** It runs after resolution, so every package is available, and before tables are built or cached,
  so only validated tables reach the LRU.
- **Establishes:**
  - I1: The resolved set is exactly the linkage table.
  - I2: Every module's immediate dependencies exist in the linkage-selected versions and pass
    `dependencies::verify_module`.
  - I3: There are no cross-module cycles.
- **Does not establish:**
  - That the root package or any other package is closed under the linkage in a transitive sense beyond
    immediate dependencies. Immediate-dependency checks applied to every package in the set cover this, as long
    as every package in the set is checked, which it is.
  - That type origin, defining ID, or `type_origin_table` data agrees across packages. Only the uniqueness
    check in `VMDispatchTables::new` below touches this.

```rust
// L373-L376
let runtime_packages = packages.into_values().map(|pkg| (pkg.runtime.original_id, Arc::clone(&pkg.runtime))).collect::<BTreeMap<..>>();
```
- **What:** Re-keys the packages by `runtime.original_id`. A duplicate key would silently drop a package.
  Duplicates are excluded by check 1 above only if `pkg.runtime.original_id == pkg.verified.original_id`. That
  equality is set up inside the JIT, which is not read here (open question).

```rust
// L377-L382
let vtables = VMDispatchTables::new(self.vm_config.clone(), self.cache.interner.clone(), link_context.clone(), runtime_packages)?;
```
- **What:** `dt:L154-L192`. Builds `defining_id_origins` from each package's `vtable.defining_ids`. If one
  defining ID appears in two packages it returns `UNKNOWN_INVARIANT_VIOLATION_ERROR` (`dt:L164-L178`). It then
  wraps the maps in `Arc`s and creates an empty `type_depths` of capacity `TYPE_DEPTH_LRU_SIZE` = 16384
  (`dt:L190`, `constants.rs:L32`).
- **Establishes:** I4: `defining_id_origins` maps each defining ID to exactly one loaded package.
- **Note:** `defining_ids` holds "all defining IDs on any types mentioned in the package" (`dt:L93-L94`), while
  the `VMDispatchTables` field describes "defining IDs on any types defined in the package" (`dt:L69-L71`). The
  uniqueness check applies to whatever the JIT puts into `vtable.defining_ids` (open question).

```rust
// L383-L385
self.cache.add_linkage_tables_to_cache(linkage_hash.clone(), vtables.clone());
Ok(vtables)
```
- **What:** Inserts only if no entry exists yet (`mc:L174-L179`); the boolean result is ignored. The LRU
  capacity is `VIRTUAL_DISPATCH_TABLE_CACHE_SIZE` = 1,000,000 entries (`constants.rs:L38`, `mc:L72`), with
  quick_cache's default weighter.
- **Establishes:** Every LRU entry has passed L372 and L377-L382 for the context equal to its key.

---

**Cross-Function Dependencies:**
- `LinkageContext::to_linkage_hash` / `all_packages` (internal, `lc:L60-L83`): the full-table key and the full
  value set. Neither can fail.
- `MoveCache::cached_linkage_tables_at` (internal, `mc:L190-L197`): returns a clone. The caller relies on
  receiving a private `type_depths`.
- `package_resolution::resolve_packages` (internal, `pr:L84-L124`): per-package deserialization, verification,
  and JIT. It fills the process-wide `package_cache` before cross-package validation runs. In release builds it
  guarantees neither key/ID agreement nor the returned count; `validate_for_vm_execution` enforces both.
- `ModuleResolver::get_packages` (external from the runtime's point of view; source available for Sui at
  `tps:L190-L195`): trusted. Sui's implementation serves packages published in the current transaction before
  the backing store (`tps:L134-L137`).
- `validate_for_vm_execution` (internal, `val:L76-L82`): establishes I1-I3. It runs only on the miss path.
- `VMDispatchTables::new` (internal, `dt:L154-L192`): establishes I4 and empty `type_depths`.
- `MoveCache::add_linkage_tables_to_cache` / `drop_all_cached_linkage_tables` (internal, `mc:L168-L186`).
- Callers:
  - `MoveRuntime::make_vm` (L275-L286).
  - The Sui adapter's `with_vm!` macro (`ctx:L164-L186`). It keeps its own per-transaction
    `executable_vm_cache` keyed by `LinkageHash` and reuses the `MoveVM`, including its `type_depths`, within a
    transaction.
  - Test and CLI harnesses.
- Shared state: `MoveCache.linkage_vtables` is written only here (L319, L384). `MoveCache.package_cache` is
  written by `jit_and_cache_package` (`pr:L266`), which is also reached from `resolve_and_cache_package`
  (L250-L265), `install_system_packages` (L184), and `validate_package` (L433).
- Invariant couplings: Execution-time resolution (`dt::get_package`, `dt:L194-L199`) depends on
  `loaded_packages` being exactly the linkage set (I1). Type resolution by defining ID depends on I4.

---

**Open Questions:**
- Unclear whether `quick_cache::unsync::Cache::clone` (quick_cache 0.6.18, `Cargo.lock`) is a deep copy. The
  crate source was not available locally. If clones share storage, L317 fires on later hits after any VM has
  computed depths.
- Unclear whether the JIT always sets `runtime.original_id == verified.original_id` and
  `runtime.version_id == verified.version_id`. L373-L376 rely on this for uniqueness. Need to inspect
  `jit::translate_package`.
- Unclear what the JIT puts into `vtable.defining_ids`: defined types only, or all mentioned types (`dt:L69` vs
  `dt:L93`). If mentioned types are included, `VMDispatchTables::new` rejects linkages where two packages
  mention the same foreign type. Need to inspect jit/optimization/translate.
- P2 (immutability per `VersionId`): unclear how Sui handles framework packages at a fixed ID (0x1, 0x2, ...)
  after an upgrade. Such an upgrade gives new bytes for the same `VersionId`. `advance_epoch` builds a fresh
  runtime for `process_system_packages` (`execution_engine.rs:L2025-L2029`). Need to inspect whether the
  long-lived `Executor` runtime (`sui-execution/src/latest.rs:L53`) is recreated per epoch or per protocol
  version.
- Packages published in a transaction (`tps:L130-L137`) reach `resolve_packages` and are inserted into the
  process-wide `package_cache` and `linkage_vtables` whether or not the transaction commits. The same applies to
  dev-inspect and dry-run, which share the runtime (see ORIENTATION). Unclear whether any flow can later bind
  different bytes to the same `VersionId`. Need to inspect object-ID derivation for publish and upgrade, and
  dry-run versus execution of the same digest.
- The package's own `linkage_table` is not compared with `link_context` (FIXME at `pr:L143-L144`). Unclear
  whether anything in the adapter ensures that the linkage chosen for a call is compatible with the linkage each
  package was published against, beyond the immediate-dependency `dependencies::verify_module` check (I2). Need
  to inspect `dependencies::verify_module` and the adapter's linkage analysis.
- Unclear whether `module.value.self_id().address()` always equals the package's `original_id`. The module
  lookups at `lnk:L148-L151` and `lnk:L160-L172` depend on it. Need to inspect `deserialization::translate::package`.
- Any linkage-table size limit is enforced outside these functions. `to_linkage_hash`, the equality at L317, and
  resolution are all linear in the table size. Need to inspect the adapter's linkage analysis for a bound.
