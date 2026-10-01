# 17 — VMDispatchTables::calculate_depth_of_datatype_and_cache, DepthFormula::{normalize, subst, solve}, eval::check_depth_of_type_impl

Files (repo-relative):
- D = external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs
- E = external-crates/move/crates/move-vm-runtime/src/execution/interpreter/eval.rs
- S = external-crates/move/crates/move-vm-runtime/src/shared/mod.rs
- H = external-crates/move/crates/move-vm-runtime/src/execution/interpreter/helpers.rs
- A = external-crates/move/crates/move-vm-runtime/src/jit/execution/ast.rs
- R = external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs

## 1. Purpose

At value-construction bytecodes (Pack, PackGeneric, VecPack, PackVariant, PackVariantGeneric — E:L559, L571, L753, L849, L862), the interpreter calls `check_depth_of_type` (E:L1197), which rejects the instruction with `VM_MAX_VALUE_DEPTH_REACHED` if the maximum nesting depth of a value of the given runtime `Type` exceeds `max_value_nest_depth` (protocol `max_move_value_depth`, sui-adapter adapter.rs:L61). For datatypes it asks the dispatch tables for a `DepthFormula` — `max(T_i + C_i, ..., C_base)` (D:L129-L139) — computed from the datatype's field declarations and memoised in a per-VM LRU (`type_depths`, D:L82, capacity `TYPE_DEPTH_LRU_SIZE = 16_384`, constants.rs:L32).

## 2. Entry points and callers

- `check_depth_of_type(run_context, ty)` E:L1197-L1206. If `max_value_nest_depth` is `None` returns `Ok(1)` with no check (E:L1198-L1204). Otherwise calls `check_depth_of_type_impl(ty, 0, max_depth)`.
- Callers (all in `step` of E): Pack L559 (type = `struct_ptr.datatype()` = `Type::Datatype(def_vtable_key)`, A:L956-L958), PackGeneric L571 (type from `instantiate_struct_type`, H:L57-L64), VecPack L753 (type = element type from `instantiate_single_type(ty_ptr)`, H:L45-L55), PackVariant L849 (A:L972-L974), PackVariantGeneric L862 (H:L66-L74).
- In every caller the depth check runs before the gas charge (e.g. E:L559 then L560; L571 then L572; L753 then L754).
- `VMDispatchTables::calculate_depth_of_type` (D:L524-L532) is the only public entry into the formula computation; callers are E:L1241, E:L1254 and a unit test (loader_tests.rs:L216). Each call builds a fresh `TypeSize::from_vm_config_for_value_depth` (D:L530; S:L104-L116): `max_depth = max_value_nest_depth.unwrap_or(VALUE_DEPTH_MAX)`, `max_nodes = max_type_to_layout_nodes.unwrap_or(HISTORICAL_MAX_TYPE_TO_LAYOUT_NODES)`.
- `run_context.vtables` is `&mut VMDispatchTables` (E:L53), so the cache is mutated during execution.

## 3. calculate_depth_of_datatype_and_cache (D:L534-L569)

Walk:
1. L539 `type_size.check()` — checks current depth/node counters against limits (S:L119-L127). Does not increment depth.
2. L541-L543 cache lookup on `VirtualTableKey` (original package id + module + name, D:L107-L110). Hit: returns a clone of the cached formula. No node counting, no traversal on a hit.
3. L545 `resolve_type(datatype_name)` (D:L223-L243): looks up `loaded_packages[package_key].vtable.types[inner_pkg_key]`; missing package or type → `VTABLE_KEY_LOOKUP_ERROR`. Returns a `VMPointer<DatatypeDescriptor>`, dereferenced with `to_ref()`.
4. L546-L559: for an enum, flat-maps all fields of all variants; for a struct, all fields; each field through `calculate_depth_of_type_and_cache`. `collect::<PartialVMResult<Vec<_>>>()?` short-circuits on first error.
5. L560 `normalize` over the field formulas; L562 `add(1)` for the datatype itself.
6. L565-L566 inserts into `type_depths` unconditionally (overwrite allowed, comment L563-L564).
7. L567 `type_size.check()` again; L568 return.

Error-path state: if step 4 errors partway, formulas of inner datatypes whose computation completed were already inserted at L565 by the recursive calls; the outer datatype is not inserted. The cache therefore retains partial progress from failed computations.

Zero fields / zero variants: `normalize(vec![])` gives `{terms: [], constant: 0}` (D:L999-L1018), then +1 → constant 1.

### calculate_depth_of_type_and_cache (D:L571-L621)
Every node is wrapped in `type_size.enter_type` (D:L576), which increments `depth` and `node_count` with `safe_add`, checks, runs the body, checks again, then decrements depth (S:L131-L142). Node count is never decremented (S:L130). On error in the body, the post-check at S:L139 still runs; depth is not restored on the error path, but the error propagates out of the whole computation and the `TypeSize` is per-call (D:L530), so it is not reused.

- Primitives → `constant(1)` (L578-L586).
- Vector / Reference / MutableReference → inner + 1 (L589-L596). References are treated like vectors (comment L587-L588).
- TyParam(i) → `{terms: [(i,0)], constant: 0}` (L597; D:L989-L994).
- Datatype(key) → recursive datatype formula (L598-L603). `debug_assert!(terms.is_empty())` at L601 is the only check that a non-generic reference yields a closed formula; it is compiled out in release.
- DatatypeInstantiation(key, args) → formulas of each arg keyed by index (`checked_as!(idx, TypeParameterIndex)`, L610) into a BTreeMap, then the datatype's formula, then `subst` (L604-L618). Args are traversed before the datatype.

Recursion bound: each `calculate_depth_of_type_and_cache` frame increments `TypeSize.depth` (S:L135) and fails once `depth > max_depth` (S:L120-L121). The intervening `calculate_depth_of_datatype_and_cache` frames do not increment depth but each is entered only from a `calculate_depth_of_type_and_cache` frame (L600, L615) or once from the root (D:L528). Rust recursion is thus bounded by roughly 2·max_depth frames independent of whether the datatype graph is acyclic; a cycle (if one existed) terminates with `VM_MAX_TYPE_DEPTH_REACHED`. Total work per uncached call is bounded by `max_nodes` (S:L123-L124), since every ArenaType node visited increments node_count.

Traversal depth vs value depth: `TypeSize.depth` counts ArenaType nodes on the current path, including type-argument nodes of `DatatypeInstantiation` (L611 enters each arg), which contribute nothing to value depth unless the parameter appears in a field. It does not count datatype boundaries themselves. So `TypeSize.depth` and formula depth differ in both directions; a type can fail with `VM_MAX_TYPE_DEPTH_REACHED` from the traversal while its value depth is small (e.g. deeply nested type args to a datatype that ignores its parameter).

Cache-state dependence: because a hit (L541) skips traversal entirely, whether a given `calculate_depth_of_type` call returns `VM_MAX_TYPE_DEPTH_REACHED`/`VM_MAX_TYPE_NODES_REACHED` depends on which datatypes were already cached in this VM, and at what traversal depth they were first computed. Computing datatype X first at depth 0 caches it; a later reference to X from deep inside another type then hits the cache and never reaches the depth limit, whereas the reverse order errors. The outcome is a function of the sequence of prior depth computations on this `VMDispatchTables` instance and of LRU eviction.

Cache lifetime: `type_depths` is created empty in `VMDispatchTables::new` (D:L190). `make_vm_with_native_extensions` checks that the vtables taken from the process-wide linkage cache have an empty `type_depths` and reloads otherwise (R:L317-L323); the doc comment D:L77-L81 says the cache is per VM instantiation. The VM (and its cache) is reused within a transaction via the adapter's per-tx `executable_vm_cache` (ORIENTATION), so the cache accumulates across PTB commands within a tx.

Cache key and linkage: key is `(original package id, module, name)` (D:L107-L110). Within one `VMDispatchTables` each original id maps to exactly one loaded package (`loaded_packages: BTreeMap<OriginalId, Arc<Package>>`, D:L68), so a key identifies a single datatype definition for the lifetime of the cache.

## 4. DepthFormula

- `normalize` (D:L999-L1018): per-variable max of coefficients via BTreeMap (deterministic ordering of `terms`), max of constants. Starting constant accumulator is 0.
- `subst(&self, map)` (D:L1021-L1038): starts from `constant(self.constant)`; for each term `(t_i, c_i)` removes `map[t_i]` (error `UNKNOWN_INVARIANT_VIOLATION_ERROR` if missing, L1028-L1033), adds `c_i`, pushes; normalizes. Parameters absent from `terms` (e.g. phantom or unused) need no mapping; extra map entries are ignored. Because of `map.remove`, a formula with the same `t_i` twice would fail the second lookup; `normalize` produces unique keys (BTreeMap, L1000-L1015), and every cached formula passes through `normalize` at L560.
- `solve(&self, tparam_depths)` (D:L1041-L1056): `max(constant, depth[t_i] + c_i)` with `saturating_add`; out-of-range index → invariant error.
- `add(c)` (D:L1060-L1066): saturating add to every coefficient and constant. All arithmetic in the formula is saturating; nothing overflows, values clamp at u64::MAX.

## 5. check_depth_of_type_impl (E:L1208-L1267)

`check_depth!(k)` (E:L1214-L1222): errors `VM_MAX_VALUE_DEPTH_REACHED` if `current_depth + k > max_depth` (saturating), else evaluates to `current_depth + k`.

- Primitives → `check_depth!(1)` (L1226-L1234).
- Reference/MutableReference/Vector → recurse with `current_depth = check_depth!(1)` (L1237-L1239). Rust recursion depth bounded by `max_depth` along vector chains (each step +1, errors beyond max).
- Datatype(si) → `calculate_depth_of_type(si)` then `check_depth!(formula.solve(&[])?)` (L1240-L1243). If the formula has any terms, `solve(&[])` returns an invariant error (D:L1046-L1050).
- DatatypeInstantiation(si, args) → each arg via recursive call with `current_depth = check_depth!(0)` (unchanged) (L1247-L1253); then formula; `check_depth!(formula.solve(&ty_arg_depths)?)` (L1254-L1255).
- TyParam → invariant error (L1258-L1263).

Semantics of the return value: the function returns an absolute depth (`current_depth + depth(ty)`), not a relative one (primitives return `current_depth + 1`). The type-argument depths passed to `solve` at L1255 are thus absolute, and `check_depth!` then adds `current_depth` again. For a generic instantiation encountered at `current_depth = d > 0` (only possible below a Vector/Reference, since only L1238 increases `current_depth`), terms are counted as `2d + depth(arg) + c_i` while the constant is `d + C`. The computed depth is ≥ the formula depth; the check is stricter than the formula for `vector<G<...>>`-shaped types. The returned u64 is discarded by all five callers (E:L559, L571, L753, L849, L862), so only the pass/fail outcome matters.

Recursion through type arguments: DatatypeInstantiation args recurse with unchanged `current_depth` (L1251), so `check_depth!` does not bound recursion through nested type arguments (`S<S<S<...>>>`). The comment at L1235-L1236 states recursion is bounded by the depth of the already-checked type arguments. The bound comes from type construction: the `Type` handed in is produced by `instantiate_struct_type`/`instantiate_enum_type`/`instantiate_single_type` (H:L45-L106), which go through `subst_with_limits`/`to_type_with_limits` using `limits.traversal()` whose `enter_type` enforces `max_type_depth` (A:L993-L998, A:L1271-L1279, A:L1318-L1335; S:L77-L84), and `instantiate_datatype_common` enforces `max_type_nodes` (H:L85-L97). The caller-frame `ty_args` substituted in were themselves bounded when that frame's instantiation was built (H:L22-L43). For Pack/PackVariant, the type is `Type::Datatype` with no args (A:L956-L974).

VecPack checks element type only: at E:L748-L753 `ty` is the element type (it is converted to a `VectorSpecialization` for the element at L756), and `check_depth_of_type` is called on `ty`, not on `vector<ty>`. The packed vector value has depth `depth(ty) + 1`, which is not itself compared against `max_depth` at this site.

Status codes: failures from `calculate_depth_of_type` surface as `VM_MAX_TYPE_DEPTH_REACHED`, `VM_MAX_TYPE_NODES_REACHED` (S:L121, L124), `VTABLE_KEY_LOOKUP_ERROR` (D:L228, L237), or invariant errors, not as `VM_MAX_VALUE_DEPTH_REACHED`.

## 6. Gas / metering

Depth computation performs no gas charging (no gas meter in scope in D:L534-L621 or E:L1208-L1267). Uncached work per call is bounded by `max_nodes`; cached calls cost a formula clone (D:L542). The check executes before `charge_pack`/`charge_vec_pack` at every site.

## 7. Invariants

1. Every cached formula has been normalized: unique, sorted type-parameter keys (D:L560, D:L1000-L1015).
2. Every cached datatype formula has constant ≥ 1 (D:L562).
3. Rust recursion in the formula computation is bounded by TypeSize depth (S:L135, L120) regardless of datatype graph acyclicity.
4. Per-call uncached work is bounded by `max_nodes` (S:L136, L123).
5. Formula arithmetic saturates (D:L1052, L1063, L1065); no panics from overflow. TypeSize counters use `safe_add`/`safe_sub` returning errors (S:L135-L140).
6. `type_depths` is empty when a VM is created (D:L190; R:L317-L323).

## 8. Assumptions

- A1: `ArenaType::Datatype(key)` only names non-generic datatypes, so its formula has no terms. Established by: `debug_assert!` at D:L601 (debug only); in release nothing found in these functions — relies on bytecode verifier signature/arity checks and JIT translation (not inspected). If violated inside a field type, the inner datatype's type-parameter indices would leak into the outer formula and be interpreted as the outer datatype's parameters in `subst`/`solve`.
- A2: For `DatatypeInstantiation`, `ty_args.len()` covers every type parameter appearing in the datatype's formula. Established by: `subst` errors on missing index (D:L1028-L1033) and `solve` errors on out-of-range (D:L1046-L1050) — enforced (as an invariant-violation error).
- A3: The `Type` passed to `check_depth_of_type_impl` has bounded nesting through type arguments. Established by: `subst_with_limits`/`to_type_with_limits` with TypeLimits traversal (A:L993-L998, A:L1318-L1335) and `instantiate_datatype_common` node limit (H:L85-L97). Not re-checked in E:L1247-L1253.
- A4: The Type contains no TyParam. Established by: substitution at H:L99-L105 / H:L50-L54; checked at E:L1258-L1263 (error).
- A5: The depth cache does not affect execution outcomes (D:L81 comment). Nothing found enforces this: cache hits bypass the traversal depth/node limits (D:L541-L543), so error outcomes depend on cache history (see §3).
- A6: The cache content is the same on all validators for the same transaction. Established by: fresh empty cache per VM (D:L190, R:L317-L323) plus deterministic instruction ordering; LRU eviction determinism of `quick_cache::unsync::Cache` not inspected.
- A7: `VecPack` element-type depth check suffices for the resulting vector value. Nothing found that checks `depth(ty)+1` at E:L753.

## 9. Open questions

- unclear; need to inspect quick_cache::unsync::Cache eviction policy for determinism (hasher seeding, CLOCK-Pro ordering) given 16_384 capacity and that eviction changes node/depth-limit outcomes.
- unclear; need to inspect bytecode verifier signature checks to confirm A1 (non-generic Datatype tokens never refer to generic datatypes) and JIT translation of SignatureToken::Datatype into ArenaType::Datatype.
- unclear; need to inspect whether other value-construction paths (natives, BCS deserialization, vector push_back of nested vectors, PTB MakeMoveVec) enforce max_value_nest_depth with the same formula, and whether they agree with the overcounting in E:L1255 and the element-only check at E:L753.
- unclear; need to inspect adapter's executable_vm_cache to confirm VM (and thus type_depths) reuse scope across PTB commands and whether dev-inspect shares it.
- unclear; need to inspect protocol values of max_move_value_depth vs max_type_to_layout_nodes to see whether TypeSize depth limit (=max_value_nest_depth) can be hit by traversal before value depth.
