# jit::execution::translate::{datatypes, structs, enums}

File: external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs
Lines: `datatypes` L562-L643 (inner fns `resolve_member_name` L572-L576, `defining_id` L580-L599), `structs` L645-L710, `enums` L712-L806.
Base abbreviation: `T` = the file above.

## 1. Purpose

These three functions turn the struct and enum definitions of one verified `CompiledModule` into arena-resident runtime definitions (`StructDef`, `EnumDef`, `VariantDef`), and build one `DatatypeDescriptor` per definition. The descriptor is the vtable entry for the type. It carries:
- `name` (interned member name),
- `defining_id: ModuleIdKey` = (address from the package's type origin table, module name),
- `original_id: ModuleIdKey` = (module self address, module name),
- `datatype_info: ArenaBox<Datatype>` = a pointer back to the StructDef/EnumDef.

(`DatatypeDescriptor::new`, ast.rs:L1050-L1062.)

Downstream, the descriptor's `defining_id` is the only place the VM stores a type's defining address. It is read by:
- `PackageVirtualTable::new`, which collects the set `defining_ids` (dispatch_tables.rs:L1079-L1086);
- `VMDispatchTables::new`, which builds `defining_id_origins: DefiningTypeId -> OriginalId` and errors when two loaded packages share a defining id (dispatch_tables.rs:L164-L178);
- `load_type_impl` (TypeTag -> Type). It maps the tag address through `defining_id_origins` (L352-L359) and requires `datatype.original_id.address() == package_key` (L374) and `datatype.defining_id.address() == tag.address` (L384);
- type -> tag / layout (dispatch_tables.rs:L496-L507, L648-L649), which emit `defining_id` as the tag address.

So the address a Sui object or coin type carries in its `StructTag` is the value these functions write into `defining_id`.

## 2. Inputs and context

`datatypes(context: &mut PackageContext, version_id: &VersionId, module_name: &IdentifierKey, module: &CompiledModule)`:
- Called only from `module()` at T:L458. `module_name` is `mkey`, which is `intern_ident_str(self_id.name())` (T:L451).
- `context.type_origin_table: HashMap<IntraPackageKey, DefiningTypeId>` is built once per package in `package()` at T:L242-L262. It interns each `IntraPackageName{module_name, type_name}` from `verified_package.type_origin_table` (an `IndexMap<IntraPackageName, DefiningTypeId>`, jit/optimization/ast.rs:L28).
- `version_id` is used only in `dbg_println!` (T:L582 parameter `_version_id`, T:L595).

### Provenance of `type_origin_table` (traced to the source)

- The VM never checks the table's content for user packages. Deserialization copies it through unchanged (validation/deserialization/ast.rs:L34), as do verification (validation/verification/translate.rs:L36, L55) and optimization (jit/optimization/translate.rs:L31, L43). None of the validation files grepped (validation/**) reads `type_origin_table` except to forward it or to `dbg_println` it (validation/mod.rs:L51).
- System packages only: `install_system_packages` filters out any package where some entry has a defining id different from `original_id` (runtime/mod.rs:L121-L139). The check logs and skips the package. It does not return an error.
- Sui host side:
  - `MovePackage::into_serialized_move_package` turns `self.type_origin_table: Vec<TypeOrigin>` into the `IndexMap` (crates/sui-types/src/move_package.rs:L586-L598). If two entries have the same (module, type), `collect` into an `IndexMap` silently keeps the later value. No duplicate check was found there.
  - Publish (`new_initial`): `build_initial_type_origin_table` sets every defined struct and enum to `m.self_id().address()` (move_package.rs:L805-L835).
  - Upgrade (`new_upgraded`): `build_upgraded_type_origin_table` reuses the predecessor's entry for the same (module, name) key and otherwise uses the new `storage_id`. It errors if the predecessor has entries left over (move_package.rs:L837-L892). The key does not include datatype kind, so a struct and an enum with the same name map to the same entry.
  - The adapter builds the package through these constructors before VM validation (sui-adapter/.../execution/context.rs:L1331, L1383, then `into_serialized_move_package` at L1133).
  - For packages loaded from storage, the table is whatever the stored `MovePackage` object holds (transaction_package_store.rs:L147-L169). The backing store is trusted per ORIENTATION.

The publisher controls module bytes, and through them which (module, type) names exist. The publisher does not supply defining addresses directly. The comment at T:L578-L579 puts completeness and correctness of the table on the adapter.

## 3. Block-by-block

### datatypes

- **T:L572-L576 `resolve_member_name`.** Resolves the interned member name back to an `Identifier` (panics on an unknown key per its "[SAFETY]" notes at dispatch_tables.rs:L1130; the key was interned at T:L658/L729 in the same call, so it exists).
- **T:L580-L599 `defining_id`.**
  - Looks up `name.intra_package_key()`, which is (module_name, member_name) with no address, in `context.type_origin_table` (T:L585-L587).
  - If the entry is missing, it returns `LOOKUP_FAILED` (T:L588-L594). There is no fallback to `original_id` or `version_id`.
  - If present, it returns `ModuleIdKey::from_parts(*defining_address, module_id)`, where `module_id` is the *vtable key's* module name, i.e. this module's own name (T:L597-L598).
  - It does not check `defining_address` against anything: not `version_id`, not `original_id`, not the linkage table.
- **T:L601-L602.** `original_address = *module.self_id().address()`. This is the module's self address, not `context.original_id`. The two are equal because deserialization requires `module.address() == pkg.original_id` (validation/deserialization/translate.rs:L44-L51). This function does not re-check it.
- **T:L604-L605.** Builds the StructDefs and EnumDefs, keyed by `original_address`.
- **T:L607.** `module_original_id = (original_address, module_name)`.
- **T:L609-L621 (structs) and T:L623-L634 (enums).** For each definition:
  - resolve the name;
  - compute `defining_id` (the `?` propagates LOOKUP_FAILED and aborts the whole package translation);
  - `arena_box(Datatype::Struct|Enum(VMPointer::from_ref(def)))` (T:L616, L629). The pointer targets an element of the arena-backed `ArenaVec` returned by `structs`/`enums`, so the address is stable. The arena slice is never reallocated (arena.rs:L59-L71).
  - re-intern the name (T:L617, L630). This produces the same key as the original intern, so the round trip adds nothing.
- **T:L636-L640.** Collects the struct descriptors followed by the enum descriptors into an arena vec. Order: all structs, then all enums, in definition-index order.
- **Return (T:L642).** Returns `(structs, enums, descriptors)`. The caller inserts the descriptors into `vtable_types` via `insert_vtable_datatypes` (T:L467, T:L116-L128). `DefinitionMap::extend` errors on a duplicate `IntraPackageKey` (dispatch_tables.rs:L1107-L1121). So a struct and an enum with the same name in one module, or a repeated name, fail at T:L467, after `datatypes` has returned.

### structs (T:L645-L710)

For each `struct_def` in `module.struct_defs()`:
- `datatype_handle_at(struct_def.struct_handle)` (T:L655). This indexes the table; bounds come from the bytecode verifier (not re-checked here).
- `def_vtable_key = (original_id, module_name, member_name)` (T:L659-L660).
- `abilities = struct_handle.abilities` (T:L662). Copied straight from the handle, which is the declared abilities of a type defined in this module.
- Native structs are rejected with `UNKNOWN_INVARIANT_VIOLATION_ERROR` (T:L670-L675). The later `match` arm `StructFieldInformation::Native => vec![]` (T:L684) cannot be reached.
- Field types go through `make_arena_type` (T:L679). Each datatype reference becomes a `VirtualTableKey` built from the referenced handle's *module-handle address*, which is an original/runtime address (T:L1742-L1775). The type depth/size is bounded by `TypeSize::for_type_traversal` (T:L1712).
- Field names are interned and arena-allocated (T:L683-L694). The intermediate `Vec<Identifier>` is heap-allocated and dropped normally. Only the `IdentifierKey` values go into the arena.
- `type_parameters` is copied from the handle (T:L696-L697). `DatatypeTyParameter` (constraints + is_phantom) holds no heap data.
- Result: `context.arena_vec(struct_defs)` (T:L709). Any arena-limit failure returns `PACKAGE_ARENA_LIMIT_REACHED` (arena.rs:L69).

### enums (T:L712-L806)

Two passes, so that each variant can point back to its enum:
1. **T:L722-L756.** Build each `EnumDef` with `variants: ArenaVec::empty()`, and fix them in an arena vec. `variant_count = checked_as!(len, u16)` (T:L743).
2. **T:L760-L803.** Zip `module.enum_defs()` with `enum_defs.iter_mut()`. For each enum:
   - take `VMPointer::from_ref(enum_)` (T:L761) into the arena slot;
   - build its variants. `variant_tag = checked_as!(idx, u16)` (T:L767). Fields go through `make_arena_type`, and names are interned. Each `VariantDef.enum_def` is the back-pointer (T:L793).
   - assign `enum_.variants = variants` (T:L802). The overwritten `ArenaVec::empty()` has no allocation, and `ArenaVec` is `ManuallyDrop` (arena.rs:L28, L116-L118), so nothing is freed.
- `debug_assert!(module.enum_defs().len() == enum_defs.len())` (T:L758) runs in debug builds only. The two lengths are equal by construction, since `enum_defs` was built by mapping over `module.enum_defs()` at T:L722-L755.
- Nothing here requires an enum to have at least one variant. That comes from the bytecode verifier, which was not inspected here.

## 4. Invariants established

- I1. Every descriptor has `original_id.address == module.self_id().address()` (T:L601-L607, L614, L628). Combined with deserialization/translate.rs:L44, this equals the package's `original_id`.
- I2. `defining_id.name == original_id.name == module's own name` (T:L597-L598 uses the vtable key's module name; T:L607). The defining module is assumed to have the same name as the current module.
- I3. Every struct or enum defined in the module has a type-origin entry, or translation of the whole package fails with LOOKUP_FAILED (T:L588-L594, propagated by `?` at T:L613/L627, T:L458).
- I4. `datatype_info` points to the StructDef/EnumDef produced in the same call, and both live in the same package arena (T:L616, L629). Pointer stability rests on bumpalo slices never moving (arena.rs:L59-L71).
- I5. `VariantDef.enum_def` points to its own `EnumDef` in the arena (T:L761, L793). `variant_tag` equals the variant's index in the definition (T:L766-L767).
- I6. Native struct definitions do not make it into a translated package (T:L670-L675).
- I7. Descriptor order is: structs in `struct_defs` order, then enums in `enum_defs` order (T:L636-L639).

## 5. Assumptions (what they rely on, and who establishes it)

- A1. `type_origin_table` maps each (module, type) in this package to the correct defining package id: the package version that first introduced the type. Established by `build_initial_type_origin_table` / `build_upgraded_type_origin_table` (sui-types move_package.rs:L805-L892). Inside the VM: nothing found for user packages. System packages get only `defining_id == original_id`, as a log-and-skip filter (runtime/mod.rs:L121-L139).
- A2. Every defining address is an id in this package's own lineage, i.e. it maps back through linkage to `original_id`. This function checks nothing. The only VM-level consequence is at vtable build: `defining_id_origins` rejects a defining id that is claimed by two loaded packages (dispatch_tables.rs:L168-L173). A defining id that no other loaded package claims is accepted with this package as its origin. Established by: nothing found in the VM. Host side: move_package.rs:L853/L868 either copies the predecessor's entry or uses `storage_id`.
- A3. Type-origin keys and module-defined names agree as strings: module name from `m.name()` and type name from the handle identifier (move_package.rs:L813-L814, L848-L849). Both sides use the same global interner (T:L255-L256 vs T:L451/L658), so equal strings produce equal keys. Extra table entries with no matching definition are ignored. No check was found that the table holds *only* defined types.
- A4. `module.self_id().address() == context.original_id`. Established by validation/deserialization/translate.rs:L44-L51 on the validate path. Not re-checked in `datatypes`.
- A5. Handle and identifier indices (`struct_handle`, `enum_handle`, `name`, field `name`) are in bounds. `datatype_handle_at` / `identifier_at` index without checks, so this is established by the bytecode verifier upstream (not inspected here; open question).
- A6. Names are unique across structs and enums in a module. Not checked in these functions. Enforced after the fact by `DefinitionMap::extend` at T:L467 (dispatch_tables.rs:L1113), and presumably by the bytecode verifier's duplication checker (not inspected).
- A7. No duplicate (module, type) entries reach the VM with conflicting values. The VM-side `IndexMap` cannot hold duplicate keys. The host `Vec<TypeOrigin> -> IndexMap` conversion keeps the last value without error (move_package.rs:L586-L598). Whether stored packages can contain duplicate `TypeOrigin` rows: nothing found.
- A8. `abilities` and `type_parameters` from the defining module's handle are authoritative. This function copies them (T:L662, L697, L739, L741). Consistency with previous versions (upgrade compatibility) is established outside the VM (not inspected here).

## 6. Calls

| Callee | Kind | What the caller relies on |
|---|---|---|
| `IdentifierInterner::intern_ident_str` / `intern_identifier` / `resolve_ident` | internal (cache/identifier_interner.rs) | Deterministic key per string. Resolve panics on unknown keys; the keys used were interned earlier in the same call. OOM panics are per ORIENTATION (not inspected here). |
| `CompiledModule::{struct_defs, enum_defs, datatype_handle_at, identifier_at, module_handle_at, self_id}` | external-source-available (move-binary-format) | In-bounds indices, which rest on the verifier. |
| `make_arena_type` (T:L1707-L1779) | internal | Field types converted to vtable keys over the *module-handle address* (original ids). Depth/size limits via TypeSize. Arena allocation. |
| `PackageContext::arena_vec` / `arena_box` -> `ArenaBuilder::alloc_vec/alloc_box` (arena.rs:L59-L86) | internal, unsafe | Stable addresses. `PACKAGE_ARENA_LIMIT_REACHED` on limit. Contents are not dropped. |
| `VirtualTableKey::from_parts` (dispatch_tables.rs:L1131-L1144) | internal | Plain construction, no validation. |
| `ModuleIdKey::from_parts` (ast.rs:L1030-L1032) | internal | Plain construction, no validation. |
| `DatatypeDescriptor::new` (ast.rs:L1050-L1062) | internal | Plain construction. |
| `checked_as!(_, u16)` | internal macro | Errors if the variant count or index exceeds u16. |
| `VMPointer::from_ref` | internal, unsafe | Raw pointer. Validity rests on the arena outliving every user of the pointer (not inspected here). |

## 7. Callers and shared state

- Callers: `module()` T:L458 <- `modules()` T:L360/L405 <- `package()` T:L278 <- `jit::translate_package` (jit/mod.rs:L32) <- package resolution/caching in the runtime.
- Shared state written:
  - the package arena (`context.package_arena`);
  - the global `IdentifierInterner` (intern calls at T:L617, L630, L658, L692, L729, L770, L784).
- The outputs later populate `context.vtable_types` (T:L467), which becomes `Package.runtime.vtable` in the process-wide `MoveCache.package_cache`. That entry is never evicted, per ORIENTATION. So a descriptor's `defining_id` stays fixed for the life of the process, for every VMDispatchTables that includes this VersionId.

## 8. Observations for the next phase (structural facts)

- The `version_id` parameter plays no part in the result (T:L582, L595). Nothing ties a new type's defining id to the package being translated.
- The defining module name is copied from the current module (T:L597), not taken from the table. The table's key already includes the module name, so each entry is intra-module.
- Because the key has no kind, a struct and an enum can share one type-origin entry (move_package.rs:L853, L868 share `existing_table`). Kind consistency across upgrades rests on upgrade compatibility checks, which were not inspected here.
- The adapter resolves type tags using the MovePackage's own `type_origin_table()` (sui-adapter env.rs:L626; cached_package_store.rs:L105-L111), independently of the VM descriptors. Both sides read the same stored table. The VM then cross-checks the tag's address against the descriptor's `defining_id` (dispatch_tables.rs:L384).

## 9. Open questions

- Unclear; need to inspect the bytecode verifier (duplication checker, bounds checker) to confirm that A5 and A6 and "enum has at least one variant" are established for all paths that reach `translate::package`, including system-package install.
- Unclear; need to inspect whether any on-chain or genesis `MovePackage` can hold a `type_origin_table` with duplicate (module, type) rows, or with entries whose package id is outside its lineage (A2, A7). The VM accepts both silently.
- Unclear; need to inspect upgrade compatibility (sui-adapter upgrade checks) for whether an upgrade can turn a struct into an enum, or the reverse, with the same name. Such a change would inherit the predecessor's defining id (move_package.rs:L853/L868).
- Unclear; need to inspect `resolve_packages` / `jit_and_cache_package` for whether the `SerializedPackage` used for JIT on a cache miss always comes from the same `into_serialized_move_package` path. That determines whether A1 holds for all cached packages.
