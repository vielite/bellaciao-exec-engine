# VMDispatchTables::verify_ty_args / abilities / abilities_impl

File: external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs:L406-L475
(`verify_ty_args` L408-L423, `abilities` L425-L427, `abilities_impl` L429-L475).
Paths below are relative to external-crates/move/crates unless stated otherwise. "dt.rs" = move-vm-runtime/src/execution/dispatch_tables.rs.

## Purpose

`verify_ty_args` checks that a list of runtime `Type`s satisfies a list of declared ability constraints
(one `AbilitySet` per type parameter). `abilities` / `abilities_impl` compute the runtime ability set of
a fully-instantiated `Type`, applying the Move rule that a generic datatype's abilities are its declared
abilities, reduced by what its non-phantom type arguments provide (with `key` requiring `store` on
arguments). These are the runtime ability oracle for (a) external type-argument checks on function entry
(`MoveVM::find_function`, move-vm-runtime/src/execution/vm.rs:L467-L469), (b) type-tag loading
(dt.rs:L398), (c) the adapter's per-type ability metadata (`MoveVM::type_information`, vm.rs:L254-L264,
consumed at sui-execution/latest/sui-adapter/src/static_programmable_transactions/env.rs:L521-L558), and
(d) natives via `NativeContext::type_to_abilities` (move-vm-runtime/src/natives/functions.rs:L271-L272).

## Inputs and trust

- `constraints: I` where `I: IntoIterator<Item=&AbilitySet>` with `ExactSizeIterator` (dt.rs:L408-L411).
  Sources observed:
  - `fun_ref.type_parameters()` — `&[AbilitySet]` on the JIT `Function` (jit/execution/ast.rs:L910-L912),
    derived from the function handle of publisher-supplied bytecode (jit translate L1214).
  - `datatype.type_param_constraints()` — `.constraints` of each `DatatypeTyParameter` on the resolved
    descriptor (ast.rs:L1238-L1244), copied from the defining module's datatype handle at JIT time
    (jit/execution/translate.rs:L696-L697, L741).
- `ty_args: &[Type]` — externally supplied. In `find_function` they come from the adapter (PTB type
  arguments loaded through `load_type`, env.rs:L481-L484). In `load_type_impl` they are the just-loaded
  type parameters of a `TypeTag::Struct` (dt.rs:L394-L397).
- `self` — the per-linkage `VMDispatchTables`; its `loaded_packages` map (dt.rs:L68) is the only state
  consulted (through `resolve_type`, dt.rs:L223-L243). It is not mutated by these functions (`&self`).

## Block-by-block

### verify_ty_args L413-L416: arity
`constraints.len() != ty_args.len()` returns `NUMBER_OF_TYPE_ARGUMENTS_MISMATCH` (dt.rs:L414-L415). The
length comes from `ExactSizeIterator::len`, which for the two observed sources is the exact slice length
(slice `iter()`, and `iter().map(..)` over a slice at ast.rs:L1243). After this check `zip` at L417 visits
every element of both sides.

### verify_ty_args L417-L421: per-argument subset check
For each pair, `self.abilities(ty)?` (L418) is computed and `expected_k.is_subset(..)` is tested;
`is_subset` is `(sub & sup) == sub` (move-binary-format/src/file_format.rs:L869-L875). Failure returns
`CONSTRAINT_NOT_SATISFIED` (L419). An error from `abilities` propagates via `?` (L418) with its own status.

Structural facts:
- Only the top-level `ty_args` are checked against constraints. Constraints of datatype instantiations
  nested *inside* a `ty_arg` (e.g. the `T` in `Foo<Bar<T>>`) are not re-checked here; `abilities_impl`
  computes abilities but never compares them with the nested datatype's `type_param_constraints`
  (dt.rs:L456-L472 does not reference constraints). Nested constraints are established only if the
  `Type` was produced by `load_type_impl`, which calls `verify_ty_args` at each struct nesting level
  (dt.rs:L398), or by verified bytecode.
- Phantom parameters are checked against their constraints like any other (L417 iterates all
  constraints with no phantom filter). Phantom-ness only matters in ability *computation*.
- Each call to `self.abilities` builds a fresh `TypeSize::for_type_traversal()` (dt.rs:L426); limits are
  not shared with the caller's traversal. In `load_type_impl`, `verify_ty_args` runs at every struct
  nesting level, so ability recomputation over the inner subtrees repeats at each level. Total work is
  bounded by the outer `load_type` limits (128 nodes, 256 depth; shared/constants.rs:L15, L28; dt.rs:L327).

### abilities L425-L427
Wrapper: `abilities_impl(ty, &mut TypeSize::for_type_traversal())`. `for_type_traversal` is
`TypeLimits::VM_DEFAULT.traversal()` (shared/mod.rs:L99-L101): max depth `TYPE_DEPTH_MAX = 256`, max nodes
`MAX_TYPE_INSTANTIATION_NODES = 128` (shared/constants.rs:L15, L28). These limits are fixed; they do not
follow a raised `TypeLimits` passed to `execute_function` (vm.rs:L369, L185), because `abilities` takes no
limits parameter.

### abilities_impl L430: traversal accounting
Everything runs inside `type_size.enter_type` (shared/mod.rs:L131-L142): depth+1 and node_count+1 with
`safe_add`, `check()` (depth > max → `VM_MAX_TYPE_DEPTH_REACHED`, nodes > max → `VM_MAX_TYPE_NODES_REACHED`,
shared/mod.rs:L119-L127), run the body, `check()` again, depth-1. Node count is never decremented. So a
`Type` with more than 128 nodes (counting every `Type` node visited, including phantom arguments, see
below) makes `abilities` fail with `VM_MAX_TYPE_NODES_REACHED`.

### abilities_impl L432-L439: primitives
Bool/U8/U16/U32/U64/U128/U256/Address → `AbilitySet::PRIMITIVES` = copy|drop|store (file_format.rs:L808-L809).

### abilities_impl L442: references
`Reference`/`MutableReference` → `REFERENCES` = copy|drop (file_format.rs:L811). The inner type is not
visited. The comment calls this "technically unreachable"; nothing in these functions rejects references
as type arguments. `vector<&T>` or `Foo<&T>` passed to this function yields a result (copy|drop from the
reference, intersected per polymorphic rules) rather than an error.

### abilities_impl L443: signer
`SIGNER` = drop only (file_format.rs:L813).

### abilities_impl L445-L448: TyParam
Returns `UNREACHABLE` error. So any `Type::TyParam` anywhere in the visited tree — including inside a
phantom argument, since all argument abilities are computed (L463-L466) — makes the call fail. It does not
fall through to a permissive result.

### abilities_impl L450-L454: vector
`polymorphic_abilities(VECTOR, [false], [abilities(inner)])`. `VECTOR` = copy|drop|store
(file_format.rs:L815-L816), never `key`. The single parameter is non-phantom, so the result is
`{copy,drop,store} ∩ required_by-closure(inner)`.

### abilities_impl L455: non-generic datatype
`Type::Datatype(idx)` → `*self.resolve_type(idx)?.to_ref().abilities()`. `resolve_type` (dt.rs:L223-L243)
looks up `loaded_packages[idx.package_key]` and then `pkg.vtable.types[idx.inner_pkg_key]`; missing
package or type → `VTABLE_KEY_LOOKUP_ERROR` on both paths; no other path. `abilities()` returns the
declared `abilities` field of the StructDef/EnumDef (ast.rs:L1071-L1076), which was copied from the
defining module's datatype handle (translate.rs:L662, L739).

Structural fact: this arm returns the declared abilities without checking that the datatype has zero type
parameters. If a `Type::Datatype` key names a generic datatype, the declared abilities are returned
unreduced. What makes `Type::Datatype` only name non-generic types:
- `load_type_impl` produces `Type::Datatype` only when both descriptor and tag have no type params
  (dt.rs:L391-L392).
- JIT `ArenaType::Datatype` → `Type::Datatype` (ast.rs:L1015) comes from `SignatureToken::Datatype` in
  verified bytecode; arity is checked by the bytecode signature verifier (not inspected here; see open
  questions).
- Nothing in `abilities_impl` itself.

### abilities_impl L456-L472: generic datatype instantiation
1. `resolve_type(idx)?.to_ref()` (L458) — same two error paths as above.
2. `declared_phantom_parameters` = `is_phantom` of each `type_parameters()` entry (L459-L462;
   ast.rs:L1064-L1069), lazily mapped over a slice (ExactSizeIterator).
3. `type_argument_abilities` = abilities of every `type_arg`, collected eagerly with `?` (L463-L466). All
   arguments are visited, phantom or not; any error aborts.
4. `AbilitySet::polymorphic_abilities(declared, phantoms, arg_abilities)` (L467-L471), defined at
   file_format.rs:L880-L923:
   - Length mismatch between phantom flags and arguments → `VERIFIER_INVARIANT_VIOLATION`
     (file_format.rs:L894-L899). This is the only arity check on this path; it is an error, not a
     truncating zip.
   - For each non-phantom argument (filter at L912), map its abilities through `Ability::required_by`
     and union (L913-L918): copy→{copy}, drop→{drop}, store→{store,key}, key→{} (file_format.rs:L778-L785).
     Intersect all of those with `declared` (L919-L921). With no non-phantom arguments the result is
     exactly `declared`.
   - Consequence: `key` survives only if declared and every non-phantom argument has `store`; an
     argument's own `key` contributes nothing.
   - The `AbilitySet` iterator (file_format.rs:L961-L985) only yields bits 0x1..0x8; `AbilitySet` values
     come from `from_u8`, which rejects bits outside ALL (file_format.rs:L925-L935).

Structural facts:
- The phantom flags and declared abilities come from the descriptor that `loaded_packages` holds for the
  key's original ID (dt.rs:L227-L234) — the package version selected by this VM's linkage — not
  necessarily the version that defined the type.
- Soundness of "declared abilities ∩ argument closure" relies on the defining module's fields satisfying
  `declared.requires()` under all-ability type parameters (move-bytecode-verifier/src/ability_field_requirements.rs:L36-L62, L67-L96)
  and on phantom parameters appearing only in phantom positions (not inspected here).

## Callees and what the caller depends on them for

| Callee | Kind | Depended on for |
|---|---|---|
| `TypeSize::for_type_traversal` / `enter_type` (shared/mod.rs:L99, L131) | internal | bounded recursion (256 depth, 128 nodes) with checked arithmetic |
| `resolve_type` (dt.rs:L223) | internal | mapping key→descriptor; errors on both missing-package and missing-type paths |
| `DatatypeDescriptor::abilities/type_parameters/type_param_constraints` (ast.rs:L1064-L1076, L1238-L1244) | internal | declared abilities, phantom flags and constraints copied verbatim from the module handle at JIT (translate.rs:L662, L696, L739-L741) |
| `AbilitySet::polymorphic_abilities` (file_format.rs:L880) | external-source-available | arity check (error on mismatch) and the phantom/required_by intersection |
| `AbilitySet::is_subset` (file_format.rs:L873) | external-source-available | bitwise subset |
| `VMPointer::to_ref` | internal | deref of arena pointer; relies on arena lifetime (not inspected here) |

## Callers

- `VMDispatchTables::load_type_impl` (dt.rs:L398) — per struct TypeTag level.
- `MoveVM::find_function` (vm.rs:L467-L469) — top-level ty args for external calls; `execute_function`
  (vm.rs:L382) depends on it (the comment at vm.rs:L394-L395 states this).
- `MoveVM::type_abilities` / `type_information` (vm.rs:L254-L270) → adapter
  `adapter_type_from_vm_type` (sui-adapter/.../env.rs:L520-L558), which stores the result in the adapter
  `Datatype.abilities` used by PTB typing (copy/drop/key checks in typing/translate.rs, verify/*,
  invariant_checks/type_check.rs).
- `NativeContext::type_to_abilities` (natives/functions.rs:L271-L272) → sui-move-natives test_scenario.rs:L667.

## Invariants (cited)

1. Arity of constraints vs ty_args is equal on success (dt.rs:L414-L415).
2. Every top-level ty_arg's computed abilities ⊇ its constraint on success (dt.rs:L417-L420).
3. Arity of phantom flags vs type args of an instantiation is equal on success (file_format.rs:L894-L899).
4. `abilities` never returns `key` for primitives, vector, reference or signer (dt.rs:L439-L454; constants
   file_format.rs:L808-L816).
5. `abilities` fails on any `TyParam` in the visited tree (dt.rs:L445-L448), and all type args are visited
   (dt.rs:L463-L466).
6. Traversal is bounded to depth 256 / 128 nodes regardless of caller limits (dt.rs:L426; shared/mod.rs:L99-L142).
7. Pure/deterministic: reads only immutable `loaded_packages` via HashMap lookup, no iteration over
   unordered maps (dt.rs:L227-L234).

## Assumptions and where established

- `Type::Datatype` keys name only non-generic datatypes — established by `load_type_impl` (dt.rs:L391) for
  tag-loaded types and by bytecode signature verification for JIT types; nothing in `abilities_impl`.
- Declared datatype abilities are consistent with field abilities — ability_field_requirements.rs:L30-L98.
- Nested instantiations inside a ty_arg satisfy their own constraints — established by `load_type_impl`
  (dt.rs:L398) for tag-loaded types; nothing in `verify_ty_args` / `abilities_impl`.
- Declared abilities and phantom flags seen under this linkage match those under which values were
  created: user upgrades require equal abilities (disallowed_new_abilities = ALL, compatibility.rs:L77-L83,
  L283-L293) and equal phantom flags/constraints (check_datatype_layout=true, compatibility.rs:L313-L362);
  framework upgrades may add copy/drop/store but not key (compatibility.rs:L85-L100).
- Reference types do not appear as type arguments — nothing found in these functions (dt.rs:L442 answers
  copy|drop).

## Open questions

- unclear; need to inspect move-bytecode-verifier signature checker for arity of `SignatureToken::Datatype`
  vs datatype handle type params, and for the phantom-position rule, to confirm the two verifier-established
  assumptions above.
- unclear; need to inspect whether the adapter computes `Datatype.abilities` with `input_type_resolution_vm`
  (env.rs:L481-L484) under one linkage and then executes under a different VM/linkage whose loaded package
  version for the same original ID has different (framework-upgraded) abilities.
- unclear; need to inspect whether any adapter or native path constructs `vm_runtime::Type` values that
  reach `find_function` without passing through `load_type` (which is what checks nested constraints).
- unclear; need to inspect whether a PTB type argument whose `Type` exceeds 128 nodes can be produced when
  `max_type_nodes` is raised (vm.rs:L185), in which case `find_function`'s `verify_ty_args` would fail with
  VM_MAX_TYPE_NODES_REACHED while execution limits allow it.
