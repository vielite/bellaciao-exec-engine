## `call_function`, `call_native`, `call_native_impl`, `call_native_with_args` in external-crates/move/crates/move-vm-runtime/src/execution/interpreter/eval.rs (L934-L1125), plus `push_call_frame` (L1165-L1176)

Abbreviations: `eval.rs` = external-crates/move/crates/move-vm-runtime/src/execution/interpreter/eval.rs; `state.rs` = .../interpreter/state.rs; `locals.rs` = .../interpreter/locals.rs; `imod.rs` = .../interpreter/mod.rs; `ast.rs` = .../jit/execution/ast.rs; `translate.rs` = .../jit/execution/translate.rs; `gas_meter.rs` = sui-execution/latest/sui-adapter/src/gas_meter.rs; `tables.rs` = crates/sui-types/src/gas_model/tables.rs.

**Purpose:** The single path by which the interpreter transfers control to a callee. `call_function` charges call gas against the caller's operand stack, then either runs a native synchronously (and advances the caller's pc) or allocates and pushes a new call frame. `call_native_with_args` is also the entry for a native invoked as the top-level function of a VM call (imod.rs:L56-L66). Without these, call gas, native cost accounting, the call-depth limit and the native return-arity check do not happen.

**Inputs & Assumptions:**
- `state: &mut MachineState` — operand stack (shared across all frames, state.rs:L51, L59-L61), call stack, heap counter. Trusted structure; contents derived from untrusted bytecode.
- `function: VMPointer<Function>` — raw pointer into a package arena (vm_pointer.rs:L22-L24, L48-L50 unsafe deref). Comes from `DirectCall` (eval.rs:L287), `VirtualCall` via `vtables.resolve_function` (eval.rs:L269-L271), or `CallGeneric` via `call_type_to_function` (eval.rs:L254, L924-L932). Target code is untrusted publisher bytecode.
- `ty_args: Vec<Type>` — `vec![]` for DirectCall/VirtualCall (eval.rs:L275, L287); for CallGeneric the result of `instantiate_generic_function` (eval.rs:L245-L249).
- `gas_meter` — in Sui, `SuiGasMeter` over `GasStatus` (gas_meter.rs:L93).
- Implicit: `run_context.vm_config.runtime_limits_config`, `run_context.extensions` (native extensions, e.g. ObjectRuntime) passed to natives (eval.rs:L1048-L1063).
- Preconditions (with establishing source):
  - The top `function.arg_count()` operands on the shared operand stack are exactly this callee's arguments, of the declared parameter types, and belong to the calling frame. Runtime checks only the count lower bound (state.rs:L388, L381). Type/ownership: nothing at runtime; established by bytecode verifier stack/type safety (not inspected here).
  - `ty_args.len()` equals the callee's type-parameter count and satisfies its ability constraints: not checked in `call_function` or `push_call_frame`/`CallStack::push_call` (state.rs:L433-L459). Establishment: `instantiate_generic_function` for CallGeneric (not inspected here) and JIT choice of Call vs CallGeneric for the empty case; nothing found in this code path.
  - For VirtualCall, the function found under the vtable key has the signature the caller's bytecode was verified against: `resolve_function` looks up by key only (dispatch_tables.rs:L201-L221); establishment lies in load-time linkage verification (not inspected here).
  - Native functions have a resolved `native` pointer: `get_native` returns `UNREACHABLE` otherwise (ast.rs:L927-L943); `check_natives` rejects modules whose native defs do not resolve (validation/verification/translate.rs:L82-L108) — both lookups use the module self address (translate.rs:L1165-L1169 vs verification/translate.rs:L96-L99).

**Outputs & Effects:**
- Gas: `charge_call`/`charge_call_generic` always (eval.rs:L956-L970); for natives additionally `charge_native_function_before_execution` then `charge_native_function` (eval.rs:L1098, L1106/L1110-L1111).
- Operand stack: args popped (native: eval.rs:L1044-L1045; non-native: eval.rs:L1172-L1173), native return values pushed (eval.rs:L1067-L1069).
- Call stack: new `CallFrame` with pc 0 becomes current, previous frame pushed to `frames` (state.rs:L444-L453); `MachineHeap.cur_size += local_count` (locals.rs:L151); `callstack_highwatermark` updated (state.rs:L156-L158).
- Caller pc: +1 after a successful native call (eval.rs:L988-L999). For non-native calls, the caller pc is advanced at `Ret` (eval.rs:L218-L229).
- Native side effects on `extensions` (e.g. ObjectRuntime) — black box from this function's view.
- Errors: charging/last_n errors carry the caller frame's location and pc (imod.rs:L94-L100 via eval.rs:L954, L961, L968). Native errors carry `Location::Module(native's module)` and code offset `(native fn index, 0)` (eval.rs:L1017-L1025). `push_call_frame` errors go through `finalize_execution_error` (eval.rs:L1003, L1270-L1282); native and charging errors do not.

---

**Block-by-Block:**

```rust
// eval.rs:L941-L949
trace!(run_context.tracer, |tracer| { tracer.enter_frame(..., &function, &ty_args) });
```
- **What:** Tracer notification before any charge.
- **Why here:** Tracing only; no effect when `tracer` is `None`.
- **Assumes:** Nothing.
- **Establishes:** Nothing. Note: on the error returns at L954/L962/L969/L1003 no `exit_frame` is emitted; on the native path `exit_frame` is emitted even on error (L976-L984). Tracer-only asymmetry.

```rust
// eval.rs:L952-L954
let last_n_operands = state.last_n_operands(function.arg_count()).map_err(...)?;
```
- **What:** Borrows an iterator over the top `arg_count` operands without popping.
- **Why here:** Gas for the call is priced on the argument sizes before they move.
- **Assumes:** `operand_stack.len() >= arg_count`; enforced: `EMPTY_VALUE_STACK` otherwise (state.rs:L388-L393). Slice is `len - n ..` (state.rs:L394).
- **Establishes:** At least `arg_count` operands exist. It does not establish that they were pushed by the current frame: the operand stack is one `Vec` for all frames (state.rs:L59-L61) with no per-frame base index; nothing found at runtime prevents consuming an outer frame's operands. Relies on verifier stack balance.
- **Depended on by:** L1044-L1045 and L1172-L1173 (pop the same count; they recheck independently).

```rust
// eval.rs:L956-L970
if ty_args.is_empty() { gas_meter.charge_call(last_n_operands, local_count) }
else { gas_meter.charge_call_generic(last_n_operands, local_count) }
```
- **What:** Charges one instruction plus pops and argument sizes.
- **Why here:** Before native dispatch and before the call-depth check, so gas is consumed even if the frame push later fails with `CALL_STACK_OVERFLOW` (state.rs:L450-L457).
- **Assumes:** The generic/non-generic branch is selected by `ty_args.is_empty()`, not by the opcode. A `CallGeneric` whose instantiation vector is empty would be charged via `charge_call`. In Sui both impls are identical (gas_meter.rs:L168-L182 vs L184-L199): `charge(1, 0, pops, 0, size)`; `_num_locals` is ignored in both. So the branch choice does not change Sui gas.
- **Detail (Sui):** `local_count` passed is `locals_len`, which is 0 for natives (translate.rs:L1205) and `params + locals` otherwise (translate.rs:L1191-L1193); unused by Sui. `GasStatus::charge` does `push_stack`, `increase_instruction_count`, `increase_stack_size` before `deduct_gas`, then `pop_stack` (tables.rs:L244-L263); `incr_size` is 0 here so only the instruction count affects the cost; `deduct_gas` sets `gas_left = 0` on failure and returns `OUT_OF_GAS` (tables.rs:L283-L291). `abstract_memory_size` of each arg is computed during the fold (gas_meter.rs:L177-L180) and can itself error.
- **Establishes:** Call instruction paid for.
- **Depended on by:** Consensus determinism of gas (charging order fixed here).

```rust
// eval.rs:L972-L999  (native branch)
let native_result = call_native(state, run_context, gas_meter, &function, ty_args);
trace!(... exit_frame ...);
native_result?;
state.call_stack.current_frame.pc = pc.checked_add(1).ok_or_else(PC_OVERFLOW)?;
```
- **What:** Runs the native synchronously in the caller's frame; on success advances the caller's pc past the call.
- **Why here:** Natives do not push a frame (comment eval.rs:L1016), so the Call instruction must be advanced here rather than at `Ret`.
- **Assumes:** `step` does not also advance pc for call opcodes: true because the `CallGeneric`/`VirtualCall`/`DirectCall` arms return from `step` directly (eval.rs:L236-L289) and never reach `op_step_impl`'s pc increment (eval.rs:L907-L919).
- **Establishes:** Caller resumes at pc+1 with native returns on top of the operand stack.

```rust
// eval.rs:L1000-L1004 + push_call_frame eval.rs:L1165-L1176
push_call_frame(state, run_context, function, ty_args).map_err(finalize_execution_error)?;
  // args = pop_n_operands(checked_as!(arg_count, u16)?)
  // state.push_call(function, ty_args, args)
```
- **What:** Pops exactly `arg_count` operands (u16-checked, eval.rs:L1172; `pop_n` fails with `EMPTY_VALUE_STACK` if short, state.rs:L378-L382) and pushes a frame.
- **`CallStack::push_call` paths (state.rs:L433-L459):**
  1. `heap.allocate_stack_frame(args, local_count)` first (L440-L443): `invalids_len = size - params.len()` via `safe_sub` (locals.rs:L149) — errors if `local_count < arg_count`; `cur_size += size` via `safe_add` (L151); builds `Vec<MemBox<Value>>` with args then `Invalid` fill (L154-L158). `cur_size` has no upper bound check anywhere (locals.rs:L132-L170); it feeds only the telemetry high-water mark (state.rs:L156-L158, eval.rs:L124).
  2. Limit check after allocation: `if frames.len() < CALL_STACK_SIZE_LIMIT` (state.rs:L450), with `CALL_STACK_SIZE_LIMIT = 1024` (shared/constants.rs:L9). `frames` excludes the current frame, so maximum live frames = 1024 in `frames` + 1 current = 1025.
  3. Overflow path (L454-L457): returns `CALL_STACK_OVERFLOW` located at `new_frame` (the callee's module and pc 0, not the caller's). The already-allocated frame is dropped without `free_stack_frame`, so `cur_size` stays incremented; `run` returns early at eval.rs:L97, before telemetry recording (L124), so the stale counter is not observed.
  4. On error from allocation (L443) the location is the caller's current frame.
- **Assumes:** `local_count >= arg_count` (translate.rs:L1191-L1193 guarantees it for non-natives). Args' order: `split_off` keeps stack order, arg 0 is the deepest (state.rs:L383).
- **Establishes:** Call depth <= 1025 frames; callee locals initialized with args and `Invalid`.
- **`finalize_execution_error`:** rewrites `StatusType::Verification` errors into `UNKNOWN_INVARIANT_VIOLATION_ERROR` (eval.rs:L1270-L1282). Only applied on this branch.

```rust
// call_native eval.rs:L1009-L1028
call_native_impl(...).map_err(|e| { maybe with_exec_state; e.at_code_offset(function.index(), 0).finish(Location::Module(native module id)) })?;
```
- **What:** Converts native-path `PartialVMError` into a `VMError` located at the native function (offset 0), not at the caller's call site.
- **Establishes:** Abort/error location for all native failures, including `ABORTED` with a native's sub-status, is the native's module and function index. The caller's pc is not in the location; with `error_execution_state` enabled, the frames are attached (L1019-L1023, state.rs:L319-L340).

```rust
// call_native_impl eval.rs:L1037-L1047
let arg_count: u16 = expected_args.try_into() else return Err(ABORTED);
let args = state.pop_n_operands(arg_count)?.into_iter().collect::<VecDeque<_>>();
```
- **What:** Pops args for the native.
- **Assumes:** `arg_count <= u16::MAX`; otherwise returns `ABORTED` with no sub-status (L1041), which is a different status than the non-native path's `checked_as!` failure (L1172).
- **Establishes:** `args.len() == arg_count` (pop_n returns exactly n or errors, state.rs:L378-L384). Therefore the arity check at L1086 always passes on this path.

```rust
// call_native_impl eval.rs:L1048-L1069
let RunContext { extensions, vm_config, vtables, .. } = run_context;
let return_values = call_native_with_args(Some(state), vtables, gas_meter, &vm_config.runtime_limits_config, extensions, function, &ty_args, args)?;
for value in return_values { state.push_operand(value)?; }
```
- **What:** Dispatches, then pushes returns onto the shared operand stack.
- **Assumes:** Native returns are of the declared types (only the count is checked, L1118). Nothing found checks return value types or abilities.
- **Detail:** `push_operand` fails with `EXECUTION_STACK_OVERFLOW` at 1024 operands (state.rs:L352-L357); a failure mid-loop leaves a partial push, and the error ends execution.
- **Detail:** The native receives `Some(&MachineState)` after its args were already popped (L1044 precedes L1055).

```rust
// call_native_with_args eval.rs:L1084-L1088
if args.len() != expected_args { return Err(EMPTY_VALUE_STACK) }
```
- **What:** Arity check on inputs.
- **Why here:** Redundant for `call_native_impl`; the effective check is for the top-level path in imod.rs:L57-L66, where `args` comes from the adapter's `Vec<Value>`.
- **Establishes:** Natives always receive exactly `parameters.len()` args. Types of top-level args are not checked here.

```rust
// eval.rs:L1089-L1096
let mut native_context = NativeContext::new(state, vtables, extensions, runtime_limits_config, gas_meter.remaining_gas());
let native_function = function.get_native(&vtables.interner)?;
```
- **What:** Snapshot of remaining gas becomes the native's `gas_budget` / `gas_left` (natives/functions.rs:L223-L238); resolves the native pointer.
- **Ordering fact:** The snapshot at L1094 is taken before `charge_native_function_before_execution` (L1098). The native's budget therefore exceeds the meter's real remaining gas by that pre-execution charge. `SuiGasMeter::remaining_gas` returns `u64::MAX` when charging is disabled (gas_meter.rs:L386-L391).
- **`get_native` paths:** `None` -> `UNREACHABLE` (or `MISSING_DEPENDENCY` with `lazy_natives`) (ast.rs:L931-L942). This error occurs before any native-specific gas charge but after `charge_call` on the in-interpreter path.

```rust
// eval.rs:L1098
gas_meter.charge_native_function_before_execution(args.iter())?;
```
- **Sui:** For `gas_model_version <= 13` charges 1 instruction + pops = args.len() + arg sizes (+ args.len() base) (gas_meter.rs:L151-L160, gas_predicates.rs:L71-L73), i.e. args are counted as popped twice on the interpreter path (comment gas_meter.rs:L152). For later versions, charges one instruction only (L161-L165).
- **Top-level path:** From imod.rs:L57 there is no preceding `charge_call`, so for gas_model_version > 13 the top-level native's args are never charged as pops/size.

```rust
// eval.rs:L1100
let result = native_function(&mut native_context, ty_args.to_vec(), args)?;
```
- **What:** Calls the native (trusted framework code, but its inputs are untrusted).
- **Paths:** (a) `Err(PartialVMError)` — propagated immediately with no `charge_native_function` and no `record_native_call`; only `charge_call` + before-execution charge were paid. (b) `Ok(NativeResult{cost, result: Ok(vals)})`. (c) `Ok(NativeResult{cost, result: Err(code)})`.
- **Assumes:** `result.cost` is bounded by what the native consumed. Nothing here compares `cost` against `native_context.gas_budget`; the meter's `deduct_gas` is the enforcement (tables.rs:L283-L291).

```rust
// eval.rs:L1104-L1114
Ok(vals) => { gas_meter.charge_native_function(result.cost, Some(vals.iter()))?; vals }
Err(code) => { gas_meter.charge_native_function(result.cost, None)?; return Err(ABORTED.with_sub_status(code)); }
```
- **What:** Charges native cost (and pushes/size for returns), then returns values or an abort.
- **Order (comment L1102-L1103, consensus-relevant):** charge precedes the abort; if the charge fails the reported status is `OUT_OF_GAS`, not the abort code.
- **Sui `charge_native_function` (gas_meter.rs:L106-L145):** computes `pushes = ret_vals.len()` and summed abstract sizes (can error), increments `num_native_calls` (saturating, tables.rs:L295-L297), then either charges `amount` as instructions via `charge(amount, pushes, 0, size, 0)` when the native-call threshold is exceeded (gas_predicates.rs:L76-L82) or `charge(0, pushes, 0, size, 0)` followed by `deduct_gas(amount)`.
- **Establishes:** Native cost debited before return values reach the operand stack.

```rust
// eval.rs:L1118-L1124
if return_values.len() != return_type_count { return Err(UNKNOWN_INVARIANT_VIOLATION_ERROR) }
```
- **What:** Return arity check after gas was charged for all returned values.
- **Establishes:** Number of values pushed equals the declared return count. Types unchecked.

---

**Cross-Function Dependencies:**
- `MachineState::last_n_operands` / `ValueStack::last_n` (internal, state.rs:L137-L142, L387-L395): count lower bound; errors otherwise.
- `GasMeter::charge_call` / `charge_call_generic` (external-source-available, gas_meter.rs:L168-L199): identical in Sui; 1 instr + pops + arg sizes; num_locals ignored.
- `call_native` (internal, eval.rs:L1009-L1028): error location = native module, offset 0.
- `call_native_impl` (internal, eval.rs:L1030-L1072): pops exactly arg_count (u16) args; pushes returns.
- `call_native_with_args` (internal, eval.rs:L1074-L1125): input arity check, native budget snapshot, before/after charges, return arity check.
- `Function::get_native` (internal, ast.rs:L927-L943): Some on all loaded natives only if `check_natives` ran (verification/translate.rs:L82-L108).
- native function (external-source-available per native; treated as a black box here): may return a hostile `cost`, wrong-typed values, or wrong count (count checked, types not). May mutate `extensions` before returning an error; those mutations are not rolled back by this code.
- `GasMeter::charge_native_function_before_execution` / `charge_native_function` (external-source-available, gas_meter.rs:L106-L166): version-gated behavior (<=13 legacy double-pop; threshold >8).
- `push_call_frame` -> `MachineState::push_call` -> `CallStack::push_call` -> `MachineHeap::allocate_stack_frame` (internal, eval.rs:L1165-L1176, state.rs:L148-L160, L433-L459, locals.rs:L143-L163): depth limit 1024 checked after allocation; heap counter unbounded.
- `finalize_execution_error` (internal, eval.rs:L1270-L1282): Verification -> invariant violation, non-native branch only.
- `set_err_info!` (internal macro, imod.rs:L94-L100): location from the given frame.
- `dispatch_tables::resolve_function` (upstream in step, dispatch_tables.rs:L201-L221): key lookup only.
- Callers: `step` arms CallGeneric (eval.rs:L257), VirtualCall (L275), DirectCall (L287). `call_native_with_args` also from `interpreter::run` for a native entry function (imod.rs:L57-L66) with `state = None`.
- Shared state: operand stack (all ops), `CallStack.frames`/`current_frame` (Ret at eval.rs:L194-L234, `pop_frame` state.rs:L464-L477), `MachineHeap.cur_size` (`free_stack_frame` locals.rs:L166-L169, run cleanup eval.rs:L128), `GasStatus` counters (`num_native_calls`, stack height/size, instruction tiers), `NativeContextExtensions` (ObjectRuntime etc.).
- Invariant couplings: gas determinism depends on the fixed order charge_call -> (native: budget snapshot -> get_native -> before-exec charge -> native -> after charge -> arity check) | (frame alloc -> depth check). Per-frame locals memory is bounded by the depth limit times `locals_len`, not by `cur_size`.

---

**Open Questions:**
- unclear; need to inspect `instantiate_generic_function` (interpreter/helpers.rs) to confirm it enforces `ty_args.len() == type_parameters.len()` and ability constraints, since `call_function`/`push_call` do not.
- unclear; need to inspect JIT translation of `Call` vs `CallGeneric` and the linkage verifier to confirm a `VirtualCall`/`DirectCall` target is never generic and that vtable-resolved signatures match the caller's verified signature (dispatch_tables.rs:L201-L221 checks only key presence).
- unclear; need to inspect whether the Sui adapter permits a PTB MoveCall directly to a public native function (e.g. 0x1 hash natives); if so, the imod.rs:L57 path skips `charge_call` and, for gas_model_version > 13, argument pop/size charging.
- unclear; need to inspect how the Sui adapter converts a native `ABORTED` error located at `(native module, native fn index, offset 0)` into a `MoveAbort` location, and whether any native returns a `StatusType::Verification` error (not rewritten on the native path).
- unclear; need to inspect whether `check_natives` runs for every package load path used in production (and `lazy_natives` feature is off in Sui builds), since `get_native` returning `UNREACHABLE` would otherwise be reachable after `charge_call`.
- unclear; need to inspect natives' use of `NativeContext::gas_budget` vs actual meter balance, given the snapshot at eval.rs:L1094 precedes the pre-execution charge at L1098.
- unclear; need to inspect whether any native mutates `extensions` (ObjectRuntime) and then returns `Err(PartialVMError)` or `result: Err(code)`; this code performs no rollback.
