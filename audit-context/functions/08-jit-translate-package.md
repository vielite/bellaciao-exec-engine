# JIT execution translation: `package`, `modules`, `preallocate_functions`, `function_bodies`

File abbreviations used below:
- `T` = external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs
- `ARENA` = external-crates/move/crates/move-vm-runtime/src/cache/arena.rs
- `VP` = external-crates/move/crates/move-vm-runtime/src/shared/vm_pointer.rs
- `AST` = external-crates/move/crates/move-vm-runtime/src/jit/execution/ast.rs
- `OPT` = external-crates/move/crates/move-vm-runtime/src/jit/optimization/translate.rs
- `DESER` = external-crates/move/crates/move-vm-runtime/src/validation/deserialization/translate.rs
- `PR` = external-crates/move/crates/move-vm-runtime/src/runtime/package_resolution.rs
- `DT` = external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs

---

## `package` in T (L217-L303)

**Purpose:** Top-level JIT step. Takes an optimized, already-verified package (`input::Package`) and produces the
runtime `Package` (AST L47-58): per-module arena structures, one bump arena that owns them, and a
`PackageVirtualTable` of raw `VMPointer`s into that arena. Every function, type and bytecode instruction that the
interpreter later executes for this package is produced here.

**Inputs & Assumptions:**
- `vm_config` (&VMConfig): trusted; supplies `package_arena_size` (ARENA L47-51) and `charge_ld_const_abstract_size`.
- `interner` (&IdentifierInterner): process-wide shared interner; mutated (interning) on every call.
- `natives` (&NativeFunctions): trusted native table.
- `system_packages` (&BTreeMap<OriginalId, Arc<CachedPackage>>): the effective set of pinned system packages eligible
  as direct-call targets. Semi-trusted: correctness of its filtering is the caller's job (doc T L72-76, L141-143).
- `verified_package` (input::Package): bytecode from an untrusted publisher, but already deserialized, verified, and
  put in basic-block form. Fields: `modules: BTreeMap<ModuleId, Module>`, `type_origin_table`, `linkage_table`
  (unused here; OPT L27-31 / optimization/ast.rs L24-31).
- Precondition: package must not appear in its own `system_packages`. Checked only by `debug_assert!` at T L235-238
  (compiled out in release). In release the filter is `effective_system_packages_for` at PR L303, which both
  production callers apply (PR L216, PR L254).
- Precondition: every module in `verified_package.modules` is keyed by `compiled_module.self_id()`. Established by
  DESER L54 / deserialization/ast.rs L32; keys are carried unchanged through verification (verification/translate.rs
  L48-49) and optimization (OPT L36-37).
- Precondition: every module's self address == original_id. Established by DESER L44-51.

**Outputs & Effects:**
- Returns `Package { version_id, original_id, loaded_modules, package_arena: package_arena.finish(), vtable }`
  (T L296-302). `finish()` turns `ArenaBuilder` into a frozen `Arena` that is `Send + Sync` (ARENA L93-95, L145-146).
- Side effect: interns every module name, type name, function name, and field name into the global interner
  (T L255-256, L328, L341 and inside callees). Interned strings persist on both success and error.
- On any error path, the `ArenaBuilder` and all partially built `Module`s are dropped.

**Block-by-Block:**

```rust
// L230-L240
let version_id = verified_package.version_id;
let original_id = verified_package.original_id;
debug_assert!(!system_packages.contains_key(&original_id), ...);
let (module_ids_in_pkg, package_modules): (BTreeSet<_>, Vec<_>) =
    verified_package.modules.into_iter().unzip();
```
- **What:** Splits the module map into a key set and a Vec of module values. The Vec comes out in BTreeMap key order.
- **Assumes:** No self-entry in `system_packages`. Only a debug assertion checks this (L235). In release builds the
  guarantee comes from PR L303.
- **Establishes:** `module_ids_in_pkg` equals the set of `self_id()`s of `package_modules`. Both come from the same
  map, and the keys equal `self_id()` per DESER L54 / deserialization/ast.rs L32.
- **Depended on by:** `modules`: the dependency filter at L388 and the `input_modules` lookups at L352 and L375.

```rust
// L242-L262
let type_origin_table = verified_package.type_origin_table.into_iter().map(...).collect::<PartialVMResult<_>>()?;
```
- **What:** Re-keys the `IndexMap<IntraPackageName, DefiningTypeId>` into `HashMap<IntraPackageKey, DefiningTypeId>`
  by interning module and type names.
- **Assumes:** The table is complete for every datatype defined in the package. A missing entry makes `datatypes`
  fail with LOOKUP_FAILED at T L585-594. The adapter supplies completeness and correctness (comment T L578-579). This
  function checks neither that the origin IDs are correct nor that the table has no entries for types that do not
  exist.
- **Establishes:** Keys are unique, because they come from an IndexMap and interning is injective on strings. The
  closure never returns Err, so the `?` at L262 cannot fire.

```rust
// L264-L278
let mut package_context = PackageContext { ..., package_arena: ArenaBuilder::new_bounded(vm_config),
    vtable_funs: DefinitionMap::empty(), vtable_types: DefinitionMap::empty(), ... };
modules(&mut package_context, &module_ids_in_pkg, &package_modules)?;
```
- **What:** Builds a fresh bounded arena and empty vtables, then translates all modules.
- **Establishes:** One arena per package. Its allocation limit is `package_arena_size`, or a 10 MB default
  (ARENA L37, L45-54). Every allocation goes through `try_alloc*` and maps failure to PACKAGE_ARENA_LIMIT_REACHED
  (ARENA L63-70, L79-85).
- **Depended on by:** All VMPointers created during translation point into this arena, or into a system package
  arena.

```rust
// L280-L302
let PackageContext { ..., loaded_modules, package_arena, vtable_funs, vtable_types, system_packages: _, .. } = package_context;
let vtable = PackageVirtualTable::new(vtable_funs, vtable_types);
Ok(Package { ..., package_arena: package_arena.finish(), vtable })
```
- **What:** Deconstructs the context and freezes the arena.
- **Establishes:** Once this returns, no crate API can allocate into this arena (ARENA L17-23).
- **Note:** `system_packages` is discarded (L291). The returned `Package` stores DirectCall `VMPointer<Function>`s
  that target system package arenas (produced via T L151-158), but it holds no `Arc` to those packages. Whether
  those arenas stay alive depends on something outside this function (the pinning in `MoveCache.system_packages`
  per ORIENTATION, and `PR` callers holding `system_packages`).

**Cross-Function Dependencies:**
- Callee `modules` (internal): must translate every module, fill `loaded_modules`, and extend
  `vtable_funs`/`vtable_types`. On every error path it propagates Err (see below).
- Callee `IdentifierInterner::intern_identifier` (external-source-available, cache/identifier_interner.rs): per
  ORIENTATION it panics on the memory limit. Not re-inspected here.
- Callee `ArenaBuilder::new_bounded` / `finish` (ARENA L45-54, L93-95): sets the limit and freezes the arena.
- Callee `PackageVirtualTable::new` (DT): not inspected; assumed to wrap the two maps.
- Callers: `jit::translate_package` (jit/mod.rs L32), which is reached from `jit_package_for_publish` (PR L218) and
  `jit_and_cache_package` (PR L256). Both apply `effective_system_packages_for` (PR L292-311) first. That function
  removes the self id (L303) and keeps only system packages that the linkage table maps to exactly the pinned
  version (L304-309).
- Shared state: global interner; `MoveCache.package_cache` receives the result (PR L266).

**Open Questions:**
- unclear; need to inspect `install_system_packages` and MoveCache to confirm that no system package `Arc` whose arena
  is the target of DirectCall pointers held by a cached user package can be dropped or replaced (for example on
  system package upgrade or epoch change) while the user package remains in `package_cache`.
- unclear; need to inspect whether any path other than PR L216/L254 reaches `translate::package` in release (grep found only
  the two plus unit tests).

---

## `modules` in T (L305-L436)

**Purpose:** Translates the package's modules in intra-package dependency order. The order matters for a concrete
reason. A same-package cross-module call is resolved in `try_resolve_direct_function_call` by looking up
`vtable_funs` (T L148-150), and a lookup miss is a hard FUNCTION_RESOLUTION_FAILURE (T L163-174), not a fallback to
a virtual call. The callee module's functions therefore have to be in `vtable_funs` before the caller module's
bodies are translated.

**Inputs & Assumptions:**
- `pkg_module_ids`: set of self_ids (see `package`).
- `package_modules`: the modules.
- Assumes intra-package module handles resolve to ids with address == original_id, so that
  `pkg_module_ids.contains(dep)` at L388 catches every intra-package dependency. Established by DESER L44 (every module
  address == original_id) together with the fact that a module handle's id is (address, name) (file_format.rs
  L2685-2691).

**Outputs & Effects:**
- Fills `package_context.loaded_modules` (IndexMap), which preserves insertion order and therefore DFS
  post-order. Calls `module()` exactly once per module.

**Block-by-Block:**

```rust
// L319-L325
let input_modules: BTreeMap<ModuleId, &input::Module> = package_modules.iter().map(|m| (m.compiled_module.self_id(), m)).collect();
let mut state: BTreeMap<ModuleId, State> = BTreeMap::new();
```
- **What:** Builds an id → module index and the DFS state map.
- **Establishes:** Key iteration order is deterministic (BTreeMap), so module translation order, `loaded_modules`
  order, and arena layout are a deterministic function of the input bytes.

```rust
// L327-L337
for root_id in input_modules.keys() {
    let root_key = package_context.interner.intern_ident_str(root_id.name());
    if matches!(state.get(root_id), Some(State::Visited)) { debug_assert!(...); continue; }
    let mut stack = vec![root_id.clone()];
```
- **What:** Starts an iterative DFS from each module that is not yet visited.
- **Note:** `root_key` is interned even when the module is skipped. It is used only in the debug_assert.

```rust
// L339-L373  (Visited / Visiting arms)
State::Visited => continue,
State::Visiting => { load module; insert into loaded_modules (error if already present); state = Visited }
```
- **What:** A `Visiting` pop means this is the post-order marker. All of the module's dependencies are done, so it
  is loaded.
- **Assumes:** An entry whose state is `Visiting` when popped is always that module's own marker, never a
  duplicate. This holds because (a) duplicates pushed before the marker sit below it on the stack, so they pop after
  the state is `Visited` and take the L344 skip, and (b) a push above the marker would need a descendant to depend
  on this module while it is `Visiting`, which L392-396 rejects as a cycle before pushing. It is established by the
  structure of L384-400 and L420-428.
- **Establishes:** Each module goes into `loaded_modules` at most once. A second insert is caught at L361-371.

```rust
// L374-L430  (NotVisited arm)
let unvisited_deps = immediate_dependencies().filter(in pkg && != self).filter_map(Visited→skip, Visiting→Err cycle, NotVisited→Ok).collect()?;
if unvisited_deps.is_empty() { load; insert; Visited }
else { state.insert(cur, Visiting) (error if already present); stack.push(cur); stack.extend(unvisited_deps); }
```
- **What:** Collects the dependencies still to process. It fails with UNKNOWN_INVARIANT_VIOLATION_ERROR if any
  dependency is `Visiting` (a cycle). Otherwise it either loads the module now or pushes the marker followed by the
  dependencies.
- **Assumes:** `immediate_dependencies` lists every module handle except the self handle (file_format.rs L2685-2691),
  so every module that a Call/datatype handle references appears. The self filter `*dep != &cur_id` at L388 handles
  a module that lists itself under a second handle.
- **Establishes:** A module is loaded only after all of its intra-package dependencies are `Visited`. The only
  entries in `Visiting` are ancestors in the DFS chain, because a node becomes `Visiting` only when popped and its
  marker is the lowest of its entries still pending. So a cycle error is raised only for a real back edge. The
  bytecode verifier or package validation is also expected to reject intra-package cycles earlier (ORIENTATION:
  "validation/: ... cycles").
- **Error paths:** The `input_modules.get` misses at L352-358 and L375-381 cannot be reached because
  `pkg_module_ids` == `input_modules` keys (see `package`). The "added to load queue as unvisited twice" check at
  L420-426 cannot fire, because this arm runs only when the state is NotVisited.
- **Stack growth:** Duplicates can be pushed. The stack grows by at most the number of intra-package edges plus one
  marker per module, so it is bounded by the size of the module-handle table.

**Cross-Function Dependencies:**
- Callee `module` (internal, T L441-528): translates one module. It inserts datatype descriptors into `vtable_types`
  (L467) and function pointers into `vtable_funs` (L491) before bodies are translated (L509). A failure anywhere
  inside it propagates as Err, and the partially built pieces are dropped (see the leak note under
  `preallocate_functions`).
- Callee `CompiledModule::immediate_dependencies` (external-source-available, file_format.rs L2685-2691).
- Shared state: `package_context.loaded_modules`, `vtable_funs`, `vtable_types`, arena, interner.

**Open Questions:**
- unclear; need to inspect verifier / validation `cycles` module to confirm intra-package cycles are rejected before JIT
  (if not, JIT rejects at L392-396 with an invariant-violation status rather than a verification status).

---

## `preallocate_functions` in T (L1064-L1101)

**Purpose:** Allocates every `Function` of a module into the arena, with code still empty, so their addresses are
fixed before any bytecode is translated. Stable addresses are what allow DirectCall pointers to target functions
that are translated later, including the function itself (recursion) and functions later in the same module.

**Inputs & Assumptions:**
- `module_name`: interned module name. `module`: a verified `CompiledModule`.
- Assumes all handle and signature indices used by `alloc_function` (T L1159, L1182, L1193-1196, L1208) are in
  bounds. They are unchecked indexing (`function_handle_at`, `signature_at`), so an out-of-bounds index would panic
  rather than return Err. The bounds checker in the bytecode verifier (verification/translate.rs L66) establishes
  this.

**Outputs & Effects:**
- Returns `(ArenaVec<Function>, HashMap<VirtualTableKey, VMPointer<Function>>)`.
- Each `Function` gets `index = FunctionDefinitionIndex(ndx)` (L1082, L1217), `code` and `jump_tables` set to
  `ArenaVec::empty()` (L1229-1230), and `name = VirtualTableKey(original_id, module_name, fn_name)` (L1175-1180).
  For native functions, `native = natives.resolve(self address, module name, fn name)`, which may be `None`
  (L1163-1171).

**Block-by-Block:**

```rust
// L1077-L1086
let prealloc_functions: Vec<Function> = module.function_defs().iter().enumerate()
    .map(|(ndx, fun)| { let findex = FunctionDefinitionIndex(checked_as!(ndx, TableIndex)?); alloc_function(...) })
    .collect()?;
let loaded_functions = package_context.arena_vec(prealloc_functions.into_iter())?;
```
- **What:** Builds the Functions on the heap, then moves them into one arena slice.
- **Establishes:** The Functions sit at fixed arena addresses. The bump arena never moves a slice, and `ArenaVec`
  wraps it as a `ManuallyDrop<Vec>` whose element storage stays put when the `ArenaVec` value moves (ARENA L59-71).
- **Assumes:** `ndx` fits in u16 (checked_as at L1082). Every `Function` field is either arena-owned or `Copy`,
  except `native: Option<Arc<..>>` (AST L165, natives/functions.rs L48).

```rust
// L1087-L1099
let fun_map = unique_map(loaded_functions.iter().map(|fun| (fun.name.clone(), VMPointer::from_ref(fun))))
    .map_err(|key| partial_vm_error!(UNKNOWN_INVARIANT_VIOLATION_ERROR, "Duplicate function key ..."))?;
```
- **What:** Creates a raw `VMPointer` to each arena Function, derived from a shared `&Function` (VP L27-29), and
  rejects duplicate names within the module (shared/mod.rs L40-52).
- **Establishes:** Function names are unique within the module. `DefinitionMap::extend` at DT L1107-1121 (via
  `insert_vtable_functions`, T L102-114) then rejects duplicates across the package.
- **Depended on by:** `module` L491 dereferences every pointer (`ptr.name`, T L109) to build `vtable_funs`.
  `try_resolve_direct_function_call` (T L150) later hands out copies of these pointers as `DirectCall` targets while
  `function_bodies` runs.

**Structural facts for the next phase:**
- The `VMPointer` contract says "`T` here must not be mutated after initial creation" (VP L12-13). Pointers to these
  Functions are created here (L1090) and stored in `vtable_funs` (L491). `function_bodies` then mutates the same
  Functions through `ArenaVec::iter_mut` (T L1126, ARENA L111-113), writing `fun.code` and `fun.jump_tables`
  (T L1142-1143). The code comment at T L484-488 states this ordering is intentional. The pointers are not
  dereferenced during the mutation window (see `function_bodies`). The contract at VP L12-13 is still not upheld
  literally, and the raw pointers were derived from shared references (L1090 `iter()`) that are then followed by a
  `&mut` over the same memory.
- Native Arc retention on error paths. `ArenaVec` never runs element destructors (ARENA L25-28). Only
  `Module::drop` drains `functions` to release the native `Arc`s (AST L126-134). On any error after L1086 and before
  a `Module` is built at T L512-527, the `Arc<NativeFunction>` clones inside the arena Functions are never dropped.
  That covers `unique_map` failure (L1092), `insert_vtable_functions` failure (L491), `function_instantiations`
  failure (L493), and `function_bodies` failure (L509). The Bump memory itself is freed when the `ArenaBuilder`
  drops.
- A native `def` whose native does not resolve yields `native: None, def_is_native: true` (L1163-1171). The check
  at verification/translate.rs L95-107 resolves using the function handle's module-handle address and name, while
  this code uses `module.self_id()`. The two are the same for a function definition's own handle, which always
  refers to the self module (verifier invariant, not re-checked here).

**Cross-Function Dependencies:**
- Callee `alloc_function` (internal, T L1152-1233). It establishes `locals_len = params + locals`, with overflow
  checked (L1191-1193). Natives get `locals_len` 0 and empty locals (L1205). Every type goes through
  `make_arena_type`, which applies the `TypeSize::for_type_traversal` limit (T L1712). It resolves natives but does
  not validate them.
- Callee `ArenaBuilder::alloc_vec` (ARENA L59-71): the only failure is PACKAGE_ARENA_LIMIT_REACHED.
- Callee `unique_map` (shared/mod.rs L40-52): returns Err with the first duplicate key.
- Caller: `module` T L490.

---

## `function_bodies` in T (L1103-L1150)

**Purpose:** Fills in each pre-allocated Function's `code` and `jump_tables` by translating its optimized basic
blocks into arena `Bytecode`. Calls are resolved to `DirectCall(VMPointer<Function>)` or `VirtualCall(key)`, and
every index is replaced with a VMPointer into this module's definitions.

**Inputs & Assumptions:**
- `module` (&input::Module): the compiled module plus `functions: BTreeMap<FunctionDefinitionIndex, input::Function>`
  (optimization/ast.rs L35-39).
- `definitions`: pointer tables into this module's arena definitions (built at T L495-507).
- `functions`: the ArenaVec from `preallocate_functions`.
- Assumes `optimized_fns` has an entry for every `fun.index`. Established by OPT L57-65, which enumerates
  `function_defs()` in the same order with the same u16 conversion that T L1077-1082 uses.
- Assumes `opt_code.is_some()` exactly when the definition has a code unit. Established by OPT L76-78 (`None` iff
  `fun.code` is None) and `is_native` == `code.is_none()` (file_format.rs L557-559).

**Outputs & Effects:**
- Writes `fun.code` and `fun.jump_tables` into arena Functions (L1142-1143) and returns the same ArenaVec.
- Allocates bytecode, jump tables, and u128/u256 boxes into the arena (T L1249-1265, L1484-1485).

**Block-by-Block:**

```rust
// L1116-L1124
let mut functions = functions;
let mut module_context = FunctionContext { package_context, module, definitions };
let mut optimized_fns = optimized_fns.clone();
```
- **What:** Moves the ArenaVec into a mutable binding and clones the entire optimized function map (all basic
  blocks) so entries can be `remove`d.
- **Note:** The clone doubles the peak heap memory of this module's optimized code for the duration of the call. The
  clone is a heap allocation, so the arena limit does not bound it.

```rust
// L1126-L1145
for fun in functions.iter_mut() {
    let Some(opt_fun) = optimized_fns.remove(&fun.index) else { return Err(UNKNOWN_INVARIANT_VIOLATION_ERROR) };
    let input::Function { ndx: _, code: opt_code } = opt_fun;
    if let Some(opt_code) = opt_code {
        let (code, jump_tables) = code(&mut module_context, opt_code.jump_tables, opt_code.code)?;
        fun.code = code; fun.jump_tables = jump_tables;
    }
}
```
- **What:** For each Function, takes its optimized form and, if it has code, translates the code and writes it in.
- **Assumes:** No VMPointer<Function> into this module is dereferenced while `fun: &mut Function` is live. Checked
  paths: `code` → `bytecode` → `call` → `try_resolve_direct_function_call` does `vtable_funs.get(..)` and
  `ptr_clone()` (T L150, L164). It copies the raw pointer without dereferencing the target Function. For
  system-package targets it dereferences `sys_pkg.runtime.vtable` (T L154-158), which lives in a different, frozen
  arena. `CallGeneric` reads `definitions.function_instantiations` pointers with `ptr_clone` (T L1470-1474). Those
  FunctionInstantiation records hold a `CallType` built earlier by `call` (T L964), and no Function is dereferenced
  here. Nothing enforces this beyond the current shape of `bytecode()`, so the assumption is established by nothing
  found beyond code structure.
- **Assumes:** Assigning `fun.code = code` drops the previous `ArenaVec::empty()`. That drop is a no-op (ManuallyDrop,
  ARENA L28, L116-118), so neither arena nor heap memory is freed incorrectly.
- **Establishes:** On success, every non-native Function has non-empty translated `code`, provided the optimized
  block map was non-empty. Natives keep empty code (L1139 skip).
- **Not checked:** After the loop, `optimized_fns` is not checked to be empty (L1147). Extra optimized entries with
  no matching definition are ignored silently. This is unreachable given OPT L57-65.
- **Not checked:** No consistency check that `fun.def_is_native` == `opt_code.is_none()`. It relies on OPT L76-78.
- **Error path:** If `code` returns Err partway through, earlier Functions already have code written and later ones
  have empty code. The whole package translation then fails, and `functions` is dropped without running element
  destructors (native Arc retention, as described above).

**Cross-Function Dependencies:**
- Callee `code` (internal, T L1236-1269). It flattens the blocks and renumbers branch targets, erroring on an unknown
  label (T L1314-1334, L1340-1352). Jump tables are allocated before the bytecode so `VariantSwitch` can point at
  them (T L1249-1258). Every index lookup in `bytecode` uses `safe_get`, which returns Err rather than panicking
  (T L1470-1671). `get_vec_type` requires exactly one type in the signature (T L194-207). The block size u16 overflow
  is checked (T L1291-1299).
- Callee `call` (internal, T L1677-1698). It builds a `VirtualTableKey` from the handle's module address and names.
  If the address == original_id, it requires the target in `vtable_funs` (Err otherwise). If the address is in
  the effective `system_packages`, it requires the target in that package's vtable (Err otherwise). Anything else
  becomes `Virtual`. This function performs no check of visibility, signature compatibility, or type-parameter
  arity against the direct target; that is left to earlier validation/linkage.
- Caller: `module` T L509.
- Shared state: arena, `vtable_funs` (read), system package vtables (read), interner (via `call`).

**Open Questions:**
- unclear; need to inspect validation linkage checks (validation/ cross-package linkage) to confirm a direct call into a
  pinned system package is checked for signature/visibility compatibility against *that pinned version* for packages
  loaded from chain (not only at publish time), since `call` (T L1693-1695) binds the raw pointer without checks.
- unclear; need to inspect interpreter handling of `def_is_native == true && native == None` (reachable only if
  verification-time `check_natives` and JIT-time `natives.resolve` disagree).
