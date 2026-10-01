## `MoveVM::execute_function` (+ `execute_function_bypass_visibility(_with_max_type_nodes)`, `execute_entry_function`, `find_function`) in external-crates/move/crates/move-vm-runtime/src/execution/vm.rs (L125-L212, L360-L477)

All citations without a file prefix refer to `external-crates/move/crates/move-vm-runtime/src/execution/vm.rs`. Other files:
- `dispatch_tables.rs` = external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs
- `shared/mod.rs` = external-crates/move/crates/move-vm-runtime/src/shared/mod.rs
- `ast.rs` = external-crates/move/crates/move-vm-runtime/src/jit/execution/ast.rs
- `interp/mod.rs` = external-crates/move/crates/move-vm-runtime/src/execution/interpreter/mod.rs
- `interner.rs` = external-crates/move/crates/move-vm-runtime/src/cache/identifier_interner.rs
- `context.rs` = sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs
- `env.rs` = sui-execution/latest/sui-adapter/src/static_programmable_transactions/env.rs

**Purpose:** This is the only path from outside the VM into the interpreter for a named function (the doc on `interpreter::run` says "All external calls need to be routed through this function", interp/mod.rs L27-L28, and `execute_function_impl` is its only caller in vm.rs, L412/L491). It resolves `(original module id, function name)` against this VM's linkage-specific dispatch tables, checks argument count, type-argument count and type-argument ability constraints, optionally checks the `entry` flag, and hands the function pointer, type args and raw `Value`s to the interpreter. Without it, callers would need `VMPointer<Function>`, which is `pub(crate)` (L65-L69).

It does not check that argument values match the parameter types. The doc comment says so directly (L102-L107): "There are NO checks on the `args`". It says it is the adapter's job to use that power responsibly.

---

**Inputs & Assumptions:**

- `original_id: &ModuleId`: module address, which must be the *original* (runtime) package id, plus the module name (L357-L359, L433-L435). Trust: semi-trusted. It comes from the adapter, which gets it from the linkage (`env.rs` L240-L247) or from `module.self_id()` for `init` (`context.rs` L1265).
- `function_name: &IdentStr`: supplied by the sender through the PTB (`env.rs` L230) or the `INIT_FN_NAME` constant (`context.rs` L1266). Trust: untrusted string, already syntactically validated as an `IdentStr`.
- `type_arguments: Vec<Type>`: already-loaded VM types. In production they come from `vm.load_type(&tag)` on a `TypeTag` (`context.rs` L976-L986), or `vec![]` for init (`context.rs` L1267). Trust: shape is sender-chosen, construction is VM-controlled.
- `args: Vec<Value>`: raw VM values. Trust: the VM treats them as trusted. Nothing in this function checks their types (L378-L420, and the note at L102-L107).
- `tracer: Option<&mut MoveTraceBuilder>`: set to `None` unless compiled with `feature = "tracing"` (L188-L192). The `execute_entry_function` path always passes `None` (L144).
- `gas_meter`: passed through to the interpreter unchanged (L415, L504).
- `bypass_declared_entry_check: bool`: `true` from `execute_function_bypass_visibility_with_max_type_nodes` (L201), `false` from `execute_entry_function` (L138).
- `type_limits: TypeLimits`: `VM_DEFAULT`, or `VM_DEFAULT` with `max_type_nodes` raised to `max(given, default)` (L184-L187, shared/mod.rs L66-L71). The raise can only increase the limit (`.max`, shared/mod.rs L68). Depth is unchanged (shared/mod.rs L69).
- Implicit state read: `self.virtual_tables` (the per-linkage `VMDispatchTables`: `loaded_packages`, `interner`), `self.interner`, `self.telemetry`, `self.native_extensions` (`Rc<RefCell<..>>`, natives/extensions.rs L24), `self.vm_config`.

Preconditions and what establishes them:
1. `original_id` is an original id that is valid for this VM's linkage. Established by the adapter: `env.rs` L240 (`resolve_to_original_id`). Within the VM, a wrong id only produces "not found" (dispatch_tables.rs L263).
2. Each `args[i]` is a value whose runtime representation matches `parameters[i]` after type-argument substitution. **Nothing found in this function or its callees on this path.** The adapter's typing and verification phase is responsible (per ORIENTATION). This analysis did not inspect it.
3. `type_arguments` contain no `TyParam`. `abilities_impl` returns UNREACHABLE for `TyParam` (dispatch_tables.rs L445-L448), so `verify_ty_args` turns this into an error whenever the function has at least one type parameter.
4. `type_arguments` contain no reference types. **Nothing found in the VM.** `abilities_impl` gives references `AbilitySet::REFERENCES` without error (dispatch_tables.rs L442). The production source is `load_type(TypeTag)`, and `load_type_impl` has no reference arm (dispatch_tables.rs L337-L401), so references cannot be produced on that path.
5. Visibility (private/friend/public) is not checked anywhere on the bypass path. The only gate is `is_entry`, and it applies only when `bypass_declared_entry_check == false` (L406). Visibility is the adapter's job: it gets `visibility`/`is_entry` from `function_information` (`env.rs` L263-L304).

---

**Outputs & Effects:**

- Returns `VMResult<Vec<Value>>`, which is the interpreter's result unchanged (L412-L420, L425).
- Telemetry: creates a fresh `TransactionTelemetryContext`, times execution, and folds the context into process-wide per-thread counters with `record_transaction`. This happens on both success and error (runtime/telemetry.rs L343-L351, L423).
- Mutably borrows `self.native_extensions` for the whole interpreter run (L496-L502). If the `RefCell` is already borrowed, the result is `UNKNOWN_INVARIANT_VIOLATION_ERROR`, returned with `Location::Undefined`.
- Mutates `self.virtual_tables` through `&mut` inside the interpreter (L492). The type-depth cache is per-VM (ORIENTATION).
- `find_function` is `&self` and has no writes. `get_ident_str` does not intern new strings (interner.rs L65-L71), so an unknown name does not grow the global interner.

---

**Block-by-Block:**

```rust
// L125-L149 execute_entry_function
let bypass_declared_entry_check = false;
self.execute_function(module, function_name, ty_args, args, None, gas_meter, bypass_declared_entry_check, TypeLimits::VM_DEFAULT)
```
- **What:** Public wrapper that enforces the `entry` flag and uses default type limits with no tracer.
- **Why here:** Separates the checked entry path from the bypass path.
- **Assumes:** Nothing beyond `execute_function`'s assumptions.
- **Establishes:** On this path, only functions with `is_entry == true` reach the interpreter (via L406).
- **Depended on by:** Callers found are dev_utils/vm_arguments.rs L88 and unit tests (leak_tests.rs L77). Grep found no production Sui adapter call to `execute_entry_function` in sui-execution/latest.

```rust
// L153-L171 execute_function_bypass_visibility
self.execute_function_bypass_visibility_with_max_type_nodes(..., tracer, None)
```
- **What:** Forwards with `max_type_nodes = None`.

```rust
// L184-L192
let type_limits = match max_type_nodes {
    Some(n) => TypeLimits::VM_DEFAULT.raise_max_type_nodes_to(n),
    None => TypeLimits::VM_DEFAULT,
};
let tracer = if cfg!(feature = "tracing") { tracer } else { None };
```
- **What:** Builds the type limits and drops the tracer unless the feature is compiled in.
- **Assumes:** `raise_max_type_nodes_to` never lowers the limit. This holds because of `.max(self.max_type_nodes)` (shared/mod.rs L68).
- **Establishes:** `type_limits.max_type_nodes >= MAX_TYPE_INSTANTIATION_NODES` and `max_type_depth == TYPE_DEPTH_MAX` (shared/mod.rs L61-L71).
- **Depended on by:** The interpreter's `MachineState` (interp/mod.rs L85). **The raised limit is not used by the checks in `find_function`.** `to_type()` uses `VM_DEFAULT` (ast.rs L989-L991), and `verify_ty_args` → `abilities` uses `TypeSize::for_type_traversal()` = `VM_DEFAULT` (dispatch_tables.rs L425-L427, shared/mod.rs L99-L101). The adapter's `vm.load_type` also uses `for_type_traversal` (dispatch_tables.rs L326-L327). In production the raise applies only to settlement `u128` calls (`context.rs` L987-L992).

```rust
// L201-L211
let bypass_declared_entry_check = true;
self.execute_function(..., tracer, gas_meter, bypass_declared_entry_check, type_limits)
```
- **What:** The bypass path turns off the `entry` check.
- **Establishes:** Any function in the linked vtables can be reached, whatever its visibility or entry flag, as long as `find_function` resolves it.
- **Depended on by:** Production callers `Context::execute_function_bypass_visibility_with_vm` (`context.rs` L1014-L1038). It is reached from `vm_move_call` (`context.rs` L993) and from init execution (`context.rs` L1263).

```rust
// L371-L374, L423-L426 telemetry wrapper
let telemetry = Arc::clone(&self.telemetry);
telemetry.with_transaction_telemetry(|txn_telemetry| { ... txn_telemetry.report_time(execution_timer); result })
```
- **What:** Scoped telemetry around the whole operation.
- **Why here:** It wraps the `try_block!` so the timer is reported even on the error paths.
- **Assumes:** `with_transaction_telemetry` calls `f` exactly once and returns its result (runtime/telemetry.rs L347-L350).
- **Establishes:** No behavioral effect on the result.

```rust
// L376-L382
let result = try_block! {
    let MoveVMFunction { function, parameters: _, return_type: _ } =
        self.find_function(original_id, function_name, &type_arguments)?;
```
- **What:** Resolves the function and discards the materialized parameter and return types.
- **Why here:** Everything later needs the `VMPointer<Function>`.
- **Assumes:** `try_block!` is an immediately-invoked closure (shared/mod.rs L27-L34). The `return Err(..)` at L385/L397/L407 and the `?` leave the closure only, not `execute_function`, so L423 always runs.
- **Establishes:** Several things become true once `find_function` returns (see the `find_function` breakdown below): the function exists in `loaded_packages[original_id.address]` under key (module, name); its parameter/return `ArenaType`s convert to `Type` within `VM_DEFAULT` limits; and `type_arguments.len() == function.type_parameters.len()`, with each argument's abilities being a superset of the declared constraint.
- **Depended on by:** L384-L420.

```rust
// L384-L392
if args.len() != function.to_ref().parameters.len() {
    return Err(partial_vm_error!(NUMBER_OF_ARGUMENTS_MISMATCH, ...).finish(Location::Module(function.module_id(&self.interner))));
}
```
- **What:** Checks argument count.
- **Assumes:** `function.module_id(&self.interner)` resolves the interned module name. `resolve_ident` panics if the key is missing (interner.rs L45-L51, ast.rs L870-L874, dispatch_tables.rs L1156-L1159). The key was obtained from the same interner at dispatch_tables.rs L294, so it exists. `self.interner` and `self.virtual_tables.interner` must be the same global interner. Unclear; see open questions.
- **Establishes:** `args.len() == parameters.len()`. `allocate_stack_frame` computes `local_count - args.len()` with `safe_sub` (interpreter/locals.rs L149). That subtraction is separately guarded only if `local_count >= parameters.len()`, which is a JIT/verifier fact this analysis did not inspect.
- **Depended on by:** `CallStack::new` → `allocate_stack_frame(args, local_count)` (interpreter/state.rs L408-L415). The native path passes `args` directly to `call_native_with_args` (interp/mod.rs L56-L66).

```rust
// L394-L404
if type_arguments.len() != function.to_ref().type_parameters().len() {
    return Err(partial_vm_error!(INTERNAL_TYPE_ERROR, ...)...);
}
```
- **What:** A redundant type-argument count check.
- **Assumes:** Nothing new. `verify_ty_args` already checked the same condition and returns `NUMBER_OF_TYPE_ARGUMENTS_MISMATCH` (dispatch_tables.rs L413-L416). On every path that reaches L396, that check has already passed, so this branch cannot be reached through `find_function`.
- **Establishes:** Defense in depth for the count invariant.

```rust
// L406-L409
if !bypass_declared_entry_check && !function.to_ref().is_entry {
    return Err(partial_vm_error!(EXECUTE_ENTRY_FUNCTION_CALLED_ON_NON_ENTRY_FUNCTION)...);
}
```
- **What:** Entry gate, active only on the `execute_entry_function` path.
- **Establishes:** Nothing on the bypass path. No visibility check exists at any point in vm.rs.

```rust
// L411-L420 → L481-L509 execute_function_impl
self.execute_function_impl(txn_telemetry, &mut tracer.map(|t| VMTracer::new(t, self.interner.clone())), gas_meter, function, type_arguments, args, type_limits)
// inside: interpreter::run(&mut self.virtual_tables, ..., &mut *self.native_extensions.try_borrow_mut()...?, ...)
```
- **What:** Borrows the native extensions and runs the interpreter.
- **Assumes:** No outstanding borrow of the shared `Rc<RefCell<NativeContextExtensions>>`. The adapter clones this `Rc` into VMs (`context.rs` L178). If the adapter holds a borrow at call time, the call fails with an invariant violation (L496-L502).
- **Establishes:** No other code can reach the extensions through this `RefCell` for the duration of the run.
- **Depended on by:** Natives that read or write the object runtime through extensions.

---

### `find_function` (L431-L477)

```rust
// L439-L448
let function = self.virtual_tables.try_resolve_function_for_external(original_id, function_name)
    .ok_or_else(|| partial_vm_error!(EXTERNAL_RESOLUTION_REQUEST_ERROR, ...).finish(Location::Module(original_id.clone())))?;
```
- **Callee paths** (dispatch_tables.rs L253-L301):
  1. The module name is not in the global interner → `None` (L294).
  2. The function name is not in the interner → `None` (L295).
  3. `loaded_packages` has no entry for `original_id.address()` → `None` (L263).
  4. The package vtable has no entry for (module, member) → `None` (L264).
  5. Otherwise → `Some(ptr_clone)` (L265).
  All `None` paths become the same `EXTERNAL_RESOLUTION_REQUEST_ERROR` (L442-L448). The adapter maps that status to `FunctionNotFound` (`env.rs` L266-L273). The lookup is keyed only on `(package original id, module name, member name)`. The vtable `functions` map holds functions, and types are in a separate map (dispatch_tables.rs L264 vs L284), so a type name cannot resolve to a function.
- **Assumes:** The interner lookup is read-only (interner.rs L69-L71). Because the interner is global and shared across threads, whether a name resolves at step 1 or 2 depends on what other packages have interned. It still only gates "not found", because a key interned by anyone else has no vtable entry in this linkage and fails at step 3 or 4, which gives the same error. So the result depends only on whether the name exists in `loaded_packages`.
- **Establishes:** `function` belongs to the package that this VM's linkage maps `original_id.address()` to.

```rust
// L450-L464
let parameters = fun_ref.parameters.iter().map(|ty| ty.to_type()).collect::<PartialVMResult<Vec<_>>>()...?;
let return_ = fun_ref.return_.iter().map(|ty| ty.to_type())...?;
```
- **What:** Deep-copies the signature `ArenaType`s into `Type`s. Each type is checked separately against `VM_DEFAULT` depth and node limits (ast.rs L989-L1026, shared/mod.rs L131-L142).
- **Paths:** An error on any parameter or return type aborts with the limit error, finished at `Location::Module(original_id)`. `TyParam` is kept unsubstituted (ast.rs L1000).
- **Establishes:** For `function_information`, unsubstituted signatures that respect the limits. `execute_function` discards them (L380-L381), so on the execution path this block acts only as a limit gate, and its error paths can still fail the call.

```rust
// L467-L469
self.virtual_tables.verify_ty_args(fun_ref.type_parameters(), ty_args)...?;
```
- **Callee paths** (dispatch_tables.rs L408-L423):
  1. Length mismatch → `NUMBER_OF_TYPE_ARGUMENTS_MISMATCH` (L414-L416).
  2. For each pair, `abilities(ty)?`. This can fail on `TyParam` (UNREACHABLE, L445-L448), on `resolve_type` failure for a datatype key missing from `loaded_packages`/vtable (VTABLE_KEY_LOOKUP_ERROR, L205-L220 via L455/L458), and on type-size overflow under `VM_DEFAULT` (L426, L430).
  3. Constraint not a subset → `CONSTRAINT_NOT_SATISFIED` (L418-L419).
  4. Otherwise `Ok`.
- **Assumes:** Datatype keys inside `ty_args` resolve in *this* VM's vtables. If the `Type`s were loaded by a different VM, a key missing here gives a lookup error, not a mismatch. `abilities_impl` does not check the arity of `DatatypeInstantiation` type args against the declared parameters. `polymorphic_abilities` receives both iterators (dispatch_tables.rs L467-L471), and its behavior on a length mismatch was not inspected. `load_type` checks the constraints of nested instantiations (dispatch_tables.rs L398) but not their arity explicitly.
- **Establishes:** `ty_args.len() == type_parameters.len()`, and each argument's abilities satisfy its declared constraint.

---

**Cross-Function Dependencies:**

- `VMDispatchTables::try_resolve_function_for_external` (internal, dispatch_tables.rs L253-L266). The caller depends on it for function identity. Every failure path collapses to `None` → `EXTERNAL_RESOLUTION_REQUEST_ERROR`.
- `IdentifierInterner::get_ident_str` (internal, interner.rs L69-L71). A read-only lookup that never interns.
- `ArenaType::to_type` (internal, ast.rs L989-L1026). Limit-checked deep copy using `VM_DEFAULT`, even when `type_limits` was raised.
- `VMDispatchTables::verify_ty_args` / `abilities` (internal, dispatch_tables.rs L408-L475). The caller depends on these for type-argument count and ability constraints. They use `VM_DEFAULT` traversal limits.
- `Function::module_id` → `VirtualTableKey::module_id` → `IdentifierInterner::resolve_ident` (internal; ast.rs L870-L874, dispatch_tables.rs L1156-L1159, interner.rs L45-L51). Called only on error paths (L391, L403, L408). It panics if the key does not resolve.
- `TypeLimits::raise_max_type_nodes_to` (internal, shared/mod.rs L66-L71). Only raises the limit.
- `interpreter::run` (internal, interp/mod.rs L30-L92). It branches on `is_native()`: natives get `args` directly (L56-L66), and non-natives get a `CallStack::new` with `args` as the first locals (L78, state.rs L408-L415). It does not type-check `args` against parameters (interp/mod.rs L55-L88).
- `TelemetryContext::with_transaction_telemetry` (internal, runtime/telemetry.rs L343-L351).
- Callers:
  - `Context::execute_function_bypass_visibility_with_vm` (`context.rs` L1014-L1038), reached from `vm_move_call` (L967-L1012) and init (L1263-L1272).
  - `function_information` (L220-L248), which `env.rs` L263-L264 uses to obtain the signature, `is_entry` and `visibility` that the adapter later uses for typing.
  - dev_utils/vm_arguments.rs L67, L78, L88.
  - Unit tests.
- Shared state:
  - `VMDispatchTables` of this VM, including `loaded_packages` and the per-VM depth cache.
  - The global `IdentifierInterner`.
  - The shared native-extensions `Rc<RefCell>` (`context.rs` L178).
  - Process-wide telemetry counters.
  - The adapter's `executable_vm_cache`. With `with_vm!`, the VM is removed during the call and reinserted only on success (`context.rs` L168-L183).
- Invariant couplings:
  - The signature, `is_entry` and `visibility` the adapter checks come from `function_information` on a VM built by `make_vm` in `env.rs` L256-L264. Execution uses a different `MoveVM` instance obtained with `with_vm!` for `function.linkage` (`context.rs` L973, L166-L181). Both are derived from the same `linkage` value (`env.rs` L260, L300). The assumption that both VMs resolve `(original_mid, name)` to the same `Function` is established by their shared `LinkageContext`, together with the determinism of `make_vm`/`make_vm_with_native_extensions` for a given linkage hash (not inspected here).
  - Type arguments are loaded twice: once in `env.rs` L248-L252 (`load_vm_type_argument_from_adapter_type`) for `function_information`, and again in `context.rs` L976-L986 by converting the adapter type to a `TypeTag` and calling `vm.load_type`. Execution depends on that round-trip producing equivalent `Type`s.
  - Value typing: by design the VM trusts `args` (L102-L107). Correctness depends on the adapter's typing and verification. Nothing on the vm.rs → interpreter path checks it.

**Open Questions:**
- unclear; need to inspect the JIT/translation guarantee that `Function::local_count() >= parameters.len()` (`allocate_stack_frame` safe_sub, interpreter/locals.rs L149), and whether native functions have a separate arity check against `args` inside `call_native_with_args` (eval.rs).
- unclear; need to inspect whether `self.interner` (L391) and `self.virtual_tables.interner` (dispatch_tables.rs L294) are always the same `Arc`. See the MoveVM construction in runtime/mod.rs `make_vm*`.
- unclear; need to inspect `AbilitySet::polymorphic_abilities` for behavior when the number of type args differs from the declared type parameters (dispatch_tables.rs L467-L471). This matters for `Type`s not built through `load_type`.
- unclear; need to inspect whether a settlement `u128` call whose type args exceed `VM_DEFAULT` nodes can fail in `load_type` (dispatch_tables.rs L327) or `verify_ty_args` (L426) before the raised limit applies. The raised limit reaches only `MachineState` (interp/mod.rs L85).
- unclear; need to inspect the adapter typing/verify phase (sui-adapter static_programmable_transactions/typing) that establishes arg value types and visibility, since vm.rs enforces neither on the bypass path.
- unclear; need to inspect `make_vm` vs `make_vm_with_native_extensions` to confirm the two VM instances for the same linkage yield identical vtables (runtime/mod.rs).
