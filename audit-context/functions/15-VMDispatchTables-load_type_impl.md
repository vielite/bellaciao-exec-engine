## `VMDispatchTables::new` (L154-L192) and `VMDispatchTables::load_type_impl` (L331-L404) in external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs

All line numbers without a file prefix refer to dispatch_tables.rs. Other files are cited as `path:Lnn`, with `rt/` = `external-crates/move/crates/move-vm-runtime/src/`.

**Purpose:**
- `new` builds the per-linkage dispatch table: it wraps the loaded runtime packages (keyed by original id) and derives `defining_id_origins`, a map from every defining id carried by any type in any loaded package to that package's original id. This map is the only thing that turns a defining-id-based `TypeTag` address into a package in the loaded set.
- `load_type_impl` converts an externally supplied `TypeTag` (defining-id based) into a VM `Type`. Datatype identity in the output is a `VirtualTableKey` of (original id, module, name) (L296-L300, L392/L399). The function checks that the tag's address is the type's actual defining id, that arity and ability constraints hold, and it enforces depth/node limits. Without it, a tag could name a type under the wrong version id, and the VM would build types that fail their declared constraints.

---

**Inputs & Assumptions (`new`):**
- `vm_config`, `interner` (Arc): trusted process state.
- `link_context: LinkageContext`: the linkage table (OriginalId -> VersionId). Semi-trusted. It is derived from the adapter/package objects. `LinkageContext::new` enforces that version ids are unique (rt/shared/linkage_context.rs:L37-L49).
- `loaded_packages: BTreeMap<OriginalId, Arc<Package>>`: JIT'd runtime packages. The caller builds this map with `.collect()` keyed on `pkg.runtime.original_id` (rt/runtime/mod.rs:L373-L376, L464-L468). A `collect` into a BTreeMap drops earlier entries that share a key without reporting it. Uniqueness of original ids therefore comes from the caller, not from `new`.
- Precondition "linkage has already occurred" (doc comment, L152). `new` itself checks nothing about linkage. It only checks that defining ids are disjoint.

**Inputs & Assumptions (`load_type_impl`):**
- `type_tag: &TypeTag`: untrusted. It comes from PTB type arguments, input object types and on-chain types (sui-adapter env.rs:L474-L484, L648-L649; execution/context.rs:L787, L983). The adapter converts `TypeInput` addresses to defining ids through the package's `type_origin_table` (env.rs:L605-L644). Raw `TypeTag` callers (`load_type_tag`, `load_type_from_struct`, env.rs:L314-L324) pass the tag through unchanged.
- `type_size: &mut TypeSize`: traversal budget. From `load_type` it is always `TypeSize::for_type_traversal()` (L327), which is depth 256 and 128 nodes (rt/shared/constants.rs:L15, L28; rt/shared/mod.rs:L99-L101).
- Implicit state read: `self.defining_id_origins`, `self.loaded_packages`, `self.interner` (read-only lookups via `get_ident_str`).

**Outputs & Effects:**
- `new`: returns `VMDispatchTables` with an empty `type_depths` QCache (L190). No external writes. The caller `load_and_cache_vtables` inserts a clone into the process-wide LRU (rt/runtime/mod.rs:L383-L384).
- `load_type_impl`: returns a `Type` tree. Primitive tags map 1:1. `Vector` recurses. `Struct` becomes `Type::Datatype(key)` or `Type::DatatypeInstantiation((key, args))`, where `key.package_key` is the original id. It has no state writes. `get_ident_str` does not intern new strings (rt/cache/identifier_interner.rs:L69-L71). The `type_size` counters are mutated.

---

**Block-by-Block (`new`):**

```rust
// L164-L178
let defining_id_origins = {
    let mut defining_id_map = BTreeMap::new();
    for (addr, pkg) in &loaded_packages {
        for defining_id in &pkg.vtable.defining_ids {
            if let Some(prev) = defining_id_map.insert(*defining_id, *addr) {
                return Err(... UNKNOWN_INVARIANT_VIOLATION_ERROR ...);
```
- **What:** For each loaded package, in BTreeMap order, it inserts each defining id of that package's types, mapped to the package's key. It fails if any defining id is already mapped.
- **Why here:** This is the only construction point of `defining_id_origins`. Every later `load_type` lookup at L354 depends on it.
- **Assumes:** `pkg.vtable.defining_ids` holds exactly the defining ids of the types defined in `pkg`. `PackageVirtualTable::new` builds it from `types.values().defining_id.address()` (L1079-L1086), and `types` holds only this package's own struct and enum definitions (rt/jit/execution/translate.rs:L116-L128, L601-L643). Each descriptor's defining id comes from the adapter-provided `type_origin_table` (translate.rs:L580-L599, L242-L262). A missing entry fails the JIT (translate.rs:L588-L594). Nothing in the VM checks that a defining id in the table belongs to the lineage of `pkg.original_id`. The protocol builds the table: the initial table uses the module self address (crates/sui-types/src/move_package.rs:L805-L835), and an upgrade reuses the predecessor's entries or `storage_id` (move_package.rs:L837-L876).
- **Establishes:** `defining_id_origins` is a function, meaning each defining id maps to exactly one original id. Iteration is over a BTreeMap, so the first collision reported is deterministic.
- **Depended on by:** L353-L359 (tag address -> package key).

The key `addr` is the BTreeMap key, which callers set to `pkg.runtime.original_id` (runtime/mod.rs:L375, L467). The `Package.original_id` of the runtime package equals the verified package's `original_id` (translate.rs:L231, L298).

The duplicate check covers only defining ids across the packages that survived the `collect` in the caller. If two packages shared an original id, one of them is already gone before `new` runs, and its defining ids are never inserted.

```rust
// L180-L191
Arc::new(...) ... type_depths: QCache::new(TYPE_DEPTH_LRU_SIZE)
```
- **What:** Wraps the maps in Arcs and creates a fresh, empty depth cache.
- **Establishes:** `type_depths.is_empty()` at construction. `make_vm` re-checks this, together with `link_context` equality, on every cache hit (rt/runtime/mod.rs:L317). `#[derive(Clone)]` at L64 clones the QCache contents. The copy cached at runtime/mod.rs:L384 is taken before any use, so it is empty.

**Block-by-Block (`load_type_impl`):**

```rust
// L336
type_size.enter_type(|type_size| { ... })
```
- **What:** Adds 1 to depth and 1 to node count, then checks both limits. It runs the body, checks again, and restores depth (rt/shared/mod.rs:L131-L142). The node count is never decremented.
- **Establishes:** No more than 256 nested tag levels and no more than 128 total tag nodes per `load_type` call, since every tag node, primitive ones included, passes through `enter_type`. Arithmetic uses `safe_add`/`safe_sub` (mod.rs:L135-L140).
- **Note:** If the body returns an error, the second `check()` and the depth restore still run (mod.rs:L138-L141), and the error from `f` is returned. If the post-`check()` fails, that error replaces `result`.

```rust
// L338-L349
TypeTag::Bool => Type::Bool, ... TypeTag::Signer => Type::Signer,
TypeTag::Vector(tt) => Type::Vector(Box::new(self.load_type_impl(tt, type_size)?)),
```
- **What:** Primitive mapping. `Signer` is accepted without restriction at this layer. `Vector` recurses with the shared budget.
- **Assumes:** Callers that must reject `signer` (for example as a PTB type argument) do so themselves. Nothing found in this function.

```rust
// L351-L359
let defining_id = struct_tag.address;
let package_key = *self.defining_id_origins.get(&defining_id).ok_or_else(|| EXTERNAL_RESOLUTION_REQUEST_ERROR)?;
```
- **What:** Maps the tag address, which is treated as a defining id, to the original id of the package that owns it.
- **Assumes:** The tag uses defining ids (doc L324-L325; vm.rs:L284-L285). If a tag uses an original id, it still resolves when the original id is also the defining id of some type in that package, which is the case for every type first introduced in v1 (move_package.rs:L815, L853). In that case the defining-id equality check at L384 decides the result.
- **Establishes:** `package_key` is a key of `loaded_packages` whenever the caller's `loaded_packages` produced this entry. `new` only inserts keys of `loaded_packages` (L166-L168).

```rust
// L361-L370
let Some((datatype, key)) = self.try_resolve_type_for_external(package_key, &struct_tag.module, &struct_tag.name) else { ... };
```
- **What:** Builds a `VirtualTableKey` (original id, module key, name key). It returns `None` if the module or name string has never been interned (L294-L295), if the package is missing (L283), or if the type is missing from `vtable.types` (L284).
- **Establishes:** `datatype` is a descriptor defined in the package whose original id is `package_key`, stored under (module, name). `key` is the VirtualTableKey with `package_key = package_key`. This original-id-based key is what makes the type identical across versions.
- **Note:** All failure paths collapse into one `EXTERNAL_RESOLUTION_REQUEST_ERROR` (L366-L369). The error message includes the full tag via `Display`.

```rust
// L372-L381
if datatype.original_id.address() != &package_key { UNKNOWN_INVARIANT_VIOLATION_ERROR }
```
- **What:** Checks that the descriptor's original module address equals the package key.
- **Assumes/Establishes:** By construction, `datatype.original_id` is `ModuleIdKey(module.self_id().address(), module_name)` (translate.rs:L601-L614, L628). Module addresses equal `pkg.original_id` (rt/validation/deserialization/translate.rs:L44). So on this path the check is a consistency assertion. It fires only if `loaded_packages` keys and package contents disagree.

```rust
// L382-L390
if datatype.defining_id.address() != &defining_id { UNKNOWN_INVARIANT_VIOLATION_ERROR }
```
- **What:** Requires the tag's address to equal the defining id recorded for this specific type.
- **Why here:** `defining_id_origins` is keyed per package, not per type (L1079-L1086). A tag that names a real defining id of package O together with a type whose defining id is a different version of O passes L354 and L361, and it is rejected only here.
- **Establishes:** For any accepted `Struct` tag, `tag.address == descriptor.defining_id.address`. Each (original, module, name) therefore has exactly one accepted tag address.
- **Note:** The mismatch is reported as `UNKNOWN_INVARIANT_VIOLATION_ERROR`, not as a resolution error, even though an untrusted tag (for example `v2_id::m::TypeFromV1`) reaches it. `MoveVM::load_type` passes every error through `convert_to_external_resolution_error` (vm.rs:L289-L294). What that conversion does with invariant-violation status codes is an open question.
- **Note:** The module-name part of `defining_id` is not compared with `struct_tag.module`. `defining_id`'s module key is the same `module_name` key that the lookup used (translate.rs:L597-L598), so they are equal by construction.

```rust
// L391-L400
if datatype.type_parameters().is_empty() && struct_tag.type_params.is_empty() {
    Type::Datatype(key)
} else {
    for ty_param in &struct_tag.type_params { type_params.push(self.load_type_impl(ty_param, type_size)?); }
    self.verify_ty_args(datatype.type_param_constraints(), &type_params)?;
    Type::DatatypeInstantiation(Box::new((key, type_params)))
}
```
- **What:** If both sides have zero parameters, the result is a non-generic type. Otherwise every argument is loaded recursively with the shared budget, and then the arity and ability constraints are verified.
- **Establishes:** The arity check runs on every non-(0,0) combination, because `verify_ty_args` compares `constraints.len()` with `ty_args.len()` (L413-L416). For each argument, the declared constraint set is a subset of the argument's abilities (L417-L421). Phantom-ness does not relax the constraint check. Phantom handling happens only in `abilities_impl` through `polymorphic_abilities` (L459-L471).
- **Note:** Arguments are all loaded before the arity check, so a tag with too many arguments consumes budget on each of them first. The budget still caps this at 128 nodes.
- **Note:** `verify_ty_args` calls `abilities(ty)` (L418), which starts a fresh `TypeSize::for_type_traversal()` per argument (L426). Ability computation is therefore not charged against the load's budget. It re-walks each argument subtree at every enclosing level, so the work is O(nodes × depth), bounded by 128 × 128 node visits plus a `resolve_type` per datatype node (L455, L458).

---

**Cross-Function Dependencies:**

- Callee `PackageVirtualTable::new` (internal, L1074-L1092), reached indirectly through `pkg.vtable.defining_ids`. `new` depends on it to supply the complete set of defining ids of the package's own types. It collects `types.values()` with no filtering. It runs on every path because it is the only constructor used at translate.rs:L294.
- Callee JIT `datatypes`/`defining_id` (internal, translate.rs:L562-L643). Each descriptor gets `original_id = (module self address, module name)` and `defining_id = (type_origin_table[module,name], module name)`. A missing origin entry is an error (L588-L594). There is no check that the origin address is a version of `original_id`.
- Callee `try_resolve_type_for_external` (internal, L276-L286) and `try_to_virtual_table_key` (L288-L301). These give an `Option`, with `None` for an unknown identifier string, a missing package, or a missing type. They do not intern (identifier_interner.rs:L69-L71).
- Callee `IdentifierInterner::get_ident_str` (internal). This is a lock-free read of the global rodeo. It returns `None` for strings never interned by any package in the process. Whether a string is present depends on what other packages this process has loaded, but that only turns into "not found", because a key for an absent string cannot match any vtable entry either.
- Callee `verify_ty_args` / `abilities` / `abilities_impl` (internal, L408-L475). These provide arity and constraint satisfaction. `abilities_impl` returns an error for `TyParam` (L445-L448), which cannot be produced from a TypeTag. For `DatatypeInstantiation` it calls `resolve_type` (L458), which cannot fail for keys produced here because the key came from the same vtable.
- Callee `AbilitySet::polymorphic_abilities` (external-source-available, move-binary-format/src/file_format.rs:L880-L900). It returns an error if the phantom-flag count differs from the argument count. For types built by `load_type_impl` the counts match, because `verify_ty_args` checked the arity at each nested level first.
- Callee `TypeSize::enter_type` / `check` (internal, rt/shared/mod.rs:L119-L142). Depth and node limits, as described above.
- Callers of `new`: `MoveRuntime::load_and_cache_vtables` (rt/runtime/mod.rs:L352-L386). It first runs `validate_for_vm_execution`, which checks a bijection between the linkage table and the packages (rt/validation/mod.rs:L122-L156), so original ids are unique before the `collect` at runtime/mod.rs:L373-L376. The other caller is `MoveRuntime::validate_package` for publish/upgrade (runtime/mod.rs:L464-L475). There the published package is chained last into the `collect` keyed by original id. If a dependency had the same original id, the published package would silently replace it. The dependencies exclude `pkg.version_id` (linkage_context.rs:L69-L79). `validate_against_link_context` with `publish=true` checks the count (+1) and that each dependency maps under its own original id (validation/mod.rs:L127-L154). It does not check that the linkage entry for `pkg.version_id` has the key `original_id`. `into_serialized_move_package` sets `original_package_id -> self.id` in the linkage table (move_package.rs:L607-L615).
- Callers of `load_type`/`load_type_impl`: `MoveVM::load_type`, `runtime_type_layout`, `annotated_type_layout` (rt/execution/vm.rs:L287-L330), the natives `type_tag_to_type_layout` (rt/natives/functions.rs:L256-L260), and the adapter `Env::load_vm_type_from_type_tag`, which uses `input_type_resolution_vm` (env.rs:L456-L486), plus execution/context.rs:L787, L983. Adapter callers treat the returned `Type`'s original-id key as the type's identity when they compare object and coin types. Whether the adapter compares further by tag or by `Type` is outside this function.
- Shared state: `defining_id_origins`, `loaded_packages` and `link_context` are immutable after `new`. They are shared across VMs with the same linkage through the LRU (`MoveCache.linkage_vtables`). `type_depths` is per-instance and not read by these functions.
- Invariant couplings: type identity equals the VirtualTableKey with the original id. Serialization back to a tag uses `defining_id` (`type_to_type_tag`, L913-L919) or `original_id` (L921-L927). A round trip tag -> Type -> defining tag is the identity for accepted tags, because L384 forces the tag address to equal `descriptor.defining_id`, and the name comes from the same descriptor (L647-L652).

---

**Open Questions:**
- unclear; need to inspect how `MoveVM::convert_to_external_resolution_error` (rt/execution/vm.rs) handles `UNKNOWN_INVARIANT_VIOLATION_ERROR`. An untrusted tag reaches the L384 mismatch path (defining id of version vN, type defined at another version), so whether this becomes a user error or an invariant violation depends on that function.
- unclear; need to inspect whether any VM-side or adapter-side check ensures, for the publish path (runtime/mod.rs:L464-L468), that the linkage table maps `original_id` to `pkg.version_id`. `validate_against_link_context(publish=true)` does not compare that key (validation/mod.rs:L127-L154), and the `collect` would silently drop a colliding dependency.
- unclear; need to inspect whether the adapter rejects `signer` and other disallowed tags before calling `load_type`, since L346 accepts `Signer` unconditionally.
- unclear; need to inspect whether system packages installed as pinned direct-call targets (MoveCache.system_packages) can be absent from `loaded_packages` while their types appear in a tag. `load_type_impl` resolves only through `defining_id_origins` built from `loaded_packages`.
- unclear; need to inspect whether any `type_origin_table` from a genesis or system-package upgrade path (not `build_*_type_origin_table`) can list a defining id outside the package lineage. `new` would accept such an id as long as no other loaded package claims it.
