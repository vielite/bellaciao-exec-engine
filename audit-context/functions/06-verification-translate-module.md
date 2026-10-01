## `module` and `check_natives` in external-crates/move/crates/move-vm-runtime/src/validation/verification/translate.rs (L62-L79, L82-L126)

File abbreviations used below:
- `T` = external-crates/move/crates/move-vm-runtime/src/validation/verification/translate.rs
- `V` = external-crates/move/crates/move-bytecode-verifier/src/verifier.rs
- `SS` = external-crates/move/crates/move-bytecode-verifier/src/script_signature.rs
- `CD` = external-crates/move/crates/move-bytecode-verifier/src/check_duplication.rs
- `NF` = external-crates/move/crates/move-vm-runtime/src/natives/functions.rs
- `D` = external-crates/move/crates/move-vm-runtime/src/validation/deserialization/translate.rs
- `VM` = external-crates/move/crates/move-vm-runtime/src/validation/mod.rs
- `PC` = crates/sui-protocol-config/src/lib.rs
- `AD` = sui-execution/latest/sui-adapter/src/adapter.rs

**Purpose:** `module` is the only step that converts a deserialized `CompiledModule` into a
`verification::ast::Module` (T:L78). `ast::Module.value` is `pub(crate)` with the comment that verified
packages must not be created without going through the verifier (verification/ast.rs:L30-L32). Everything
downstream (JIT translation, interpreter) that treats a module as type-, ability-, and reference-safe rests
on the checks executed here. `check_natives` ensures every `native` function definition in the module names
an entry present in the runtime's native table, and rejects every native struct definition.

---

**Inputs & Assumptions:**
- `context` (&Context): holds `natives: &NativeFunctions` and `vm_config: &VMConfig` (T:L21-L24), built in
  `package` at T:L46. Trust: trusted (process-wide config and native table; the VMConfig verifier config is
  `protocol_config.verifier_config(None)` at AD:L57).
- `m` (CompiledModule): deserialized from publisher-supplied or on-chain bytes by
  `deserialization::translate::package` (D:L30). Trust: untrusted contents. What deserialization already
  established: module self name matches the map key (D:L34), module self address equals the package
  `original_id` (D:L44), no duplicate module names (D:L54), package non-empty (D:L65).
- Implicit: none of the checks consult other packages; this is intra-module verification only. Linkage and
  cycle checks happen later (VM:L70, VM:L81; comment at VM:L85, VM:L100-L101).
- Preconditions for the post-verifier loop (T:L68-L76) and `check_natives` (T:L82-L126): all table indices
  are in bounds. Established by `BoundsChecker::verify_module` at V:L106, which runs inside the call at
  T:L66 before either consumer.

**Outputs & Effects:**
- Returns `Ok(ast::Module { value: m })` (T:L78) or the first `VMError`.
- No state writes. No gas is charged (TODO at T:L65; `DummyMeter` passed at V:L97 returns `Ok` from every
  method, move-bytecode-verifier-meter/src/dummy.rs:L10-L18).
- Postcondition on Ok: every pass listed in V:L106-L119, V:L88 and V:L90 returned Ok under
  `context.vm_config.verifier`; every native fdef resolved in `natives`; no struct def has
  `StructFieldInformation::Native`.

---

**Block-by-Block:**

```rust
// T:L66
move_bytecode_verifier::verify_module_with_config_unmetered(&context.vm_config.verifier, &m)?;
```
- **What:** Runs the full Move bytecode verifier with a no-op meter.
- **Callee path walk (V:L93-L98 -> V:L81-L91 -> V:L100-L121):** the passes run in order and any error
  short-circuits with `?`: BoundsChecker (V:L106, error finished with `Location::Undefined` at V:L109 because
  the self handle cannot be trusted yet), LimitsVerifier (V:L111), DuplicationChecker (V:L112),
  SignatureChecker (V:L113), InstructionConsistency (V:L114), constants (V:L115), friends (V:L116),
  ability_field_requirements (V:L117), RecursiveDataDefChecker (V:L118), InstantiationLoopChecker (V:L119),
  then code_unit_verifier (control flow, stack usage, type safety, locals, reference safety) (V:L88), then
  `script_signature::verify_module` with `no_additional_script_signature_checks` (V:L90). There is no path
  that returns Ok while skipping a pass; every pass is unconditional in this function. Individual passes
  contain config-gated behavior (e.g. `config.deprecate_global_storage_ops` at V:L106).
- **Why here:** first in `module`, so all later indexing in T:L68-L126 operates on a bounds-checked module.
- **Assumes:** `context.vm_config.verifier` is the correct on-chain config. At execution time it is
  `verifier_config(None)` (AD:L57), which differs from the signing-time config: back-edge limits and the
  regex reference-safety sanity limit are `None` (PC:L4832-L4834), `additional_borrow_checks` and
  `deprecate_global_storage_ops` come from protocol flags rather than being forced `true`
  (PC:L4836-L4847). Unmetered: the only bound on verifier work here is the LimitsVerifier/binary table
  limits in the config (PC:L4849-L4890) plus whatever the pre-consensus metered run rejected
  (ORIENTATION entrypoint `Verifier::meter_compiled_modules`); that pre-consensus run covers newly published
  bytes, not on-chain packages being re-loaded via `package_resolution.rs:L151`.
- **Establishes:** in-bounds indices (V:L106); unique function handles by `(module, name)` (CD:L151-L160);
  unique function defs by handle (CD:L348-L356); every function def's handle belongs to the self module
  (CD:L369-L376); every struct/enum def's handle belongs to the self module (CD:L243-L250, CD:L334-L341);
  the bytecode-level type/ability/reference safety that code_unit_verifier provides (V:L88).
- **Depended on by:** T:L69-L70 and T:L93-L99 index tables without checks; the JIT and interpreter depend on
  the code-unit guarantees.

```rust
// T:L68-L76
for function in m.function_defs() {
    let handle = m.function_handle_at(function.function);
    let name = m.identifier_at(handle.name);
    script_signature::verify_module_function_signature_by_name(&m, name, no_additional_script_signature_checks)?;
}
```
- **What:** For every function def, looks up the def again by name and runs the "main signature" check.
- **Callee path walk:** `verify_module_function_signature_by_name` (SS:L60-L80) linearly searches
  function defs for the first whose handle name equals `name` (SS:L65-L67); if none, returns
  VERIFICATION_ERROR (SS:L68-L74). Since `name` was taken from a def in the same module, the not-found path is
  unreachable, and because handles are unique by `(module, name)` (CD:L151-L160), defs unique by handle
  (CD:L348-L356), and all defs point to self (CD:L369-L376), the found def is the same def. Then
  `verify_module_function_signature` (SS:L84-L105) calls `verify_main_signature_impl` (SS:L107-L120):
  `legacy_script_signature_checks` runs only when `module.version() < VERSION_5 && is_entry` (SS:L114-L118);
  otherwise the only check is the passed-in function, which is
  `no_additional_script_signature_checks`, returning `Ok(())` unconditionally (SS:L122-L129).
- **Consequence (structural):** for a module with version >= 5, or for non-entry functions, this loop
  performs no check. The current Sui protocol sets `min_move_binary_format_version = Some(6)` (PC:L3521),
  consumed by `binary_config` (PC:L4902-L4903), so modules with version < 5 are rejected at deserialization
  under that config and the legacy branch is unreachable there. The module-level header comment claims "All
  return types are not references" (SS:L10); no code in SS:L107-L129 checks that. The comment at T:L67 says
  this is done "to avoid needing to do it during VM runs"; the only non-trivial behavior it provides is the
  version<5 legacy branch. Note V:L90 already runs `script_signature::verify_module`, which returns early for
  version < 5 (SS:L39-L41) and otherwise runs the same no-op check for entry functions (SS:L43-L54), so the
  loop at T:L68-L76 is the only place the legacy branch can run.
- **Cost:** O(F^2) identifier comparisons (F defs, each doing a linear `find` at SS:L65), unmetered. F is
  bounded by `max_function_definitions` (PC:L4867) via LimitsVerifier and by the binary table limits.
- **Assumes:** bounds already checked (V:L106).
- **Establishes:** nothing beyond T:L66 under current protocol config.
- **Depended on by:** unclear whether any runtime code skips an entry-signature check because of this loop
  (see Open Questions).

```rust
// T:L87-L109 (check_natives_impl, native functions)
for (idx, native_function) in module.function_defs().iter().filter(|fdv| fdv.is_native()).enumerate() {
    let fh = module.function_handle_at(native_function.function);
    let mh = module.module_handle_at(fh.module);
    if natives.resolve(module.address_identifier_at(mh.address), module.identifier_at(mh.name).as_str(),
                       module.identifier_at(fh.name).as_str()).is_none() { return Err(... MISSING_DEPENDENCY ...) }
}
```
- **What:** For every native fdef, requires `(address, module name, function name)` to exist in the native
  table.
- **Callee:** `NativeFunctions::resolve` (NF:L178-L185) is a three-level HashMap lookup; it returns
  `Some(NativeFunction)` iff the triple was registered; no other condition. Table is built by
  `NativeFunctions::new` (NF:L192-L208), which rejects duplicate triples (NF:L203-L205).
- **Why here:** after T:L66 so `fh.module == self_handle` (CD:L369-L376); therefore `mh` is the module's own
  handle and the address is the module's self address, which equals the package `original_id` (D:L44).
- **Checks performed:** existence by name only. Nothing in T:L87-L109 or NF:L178-L185 compares the declared
  parameter types, return types, type parameter count, or type parameter ability constraints of the Move
  declaration against what the Rust native implementation expects. The native implementation's expectations
  about argument count/types are established by the declared signature in bytecode, which is set by whoever
  authored the module at that address. Resolution requires the module's `original_id` to be an address present
  in the native table, so the set of modules that can declare a resolvable native is bounded by which
  `original_id`s can be supplied to `validate_package` for publish (VM:L56, VM:L58) — see Open Questions.
- **Error index:** `idx` is the position within the filtered native-only iterator (T:L90-L91), reported as
  `IndexKind::FunctionHandle` (T:L105). It is neither a function-handle index nor a function-definition
  index. `checked_as!(idx, TableIndex)?` (T:L106) returns an error rather than truncating if idx exceeds
  u16.
- **Establishes:** every native fdef in a verified module has a registered implementation at load time; the
  JIT/interpreter can resolve it without a missing-native path at call time (dependent on the JIT re-resolving
  with the same table — see Open Questions).

```rust
// T:L113-L121 (native structs)
for (idx, struct_def) in module.struct_defs().iter().enumerate() {
    if struct_def.field_information == StructFieldInformation::Native { return Err(... MISSING_DEPENDENCY, IndexKind::FunctionHandle, idx ...) }
}
```
- **What:** Rejects any struct def with native field information.
- **Why here:** unconditional; any module containing a native struct fails validation regardless of address.
- **Error metadata:** uses `IndexKind::FunctionHandle` with a struct-def index (T:L116-L118); TODO at T:L111-L112
  acknowledges this.
- **Establishes:** every struct def in a verified module has `Declared` field information. Enum defs are not
  examined here; unclear whether enums can carry a native marker (Open Questions).
- **Depended on by:** JIT datatype translation, which can then assume fields are declared.

```rust
// T:L124-L125
check_natives_impl(context.natives, in_module).map_err(|e| e.finish(Location::Module(in_module.self_id())))
```
- **What:** Attaches module location to the partial error. `self_id()` is safe here because bounds were
  checked at V:L106.

```rust
// T:L78
Ok(ast::Module { value: m })
```
- **What:** Wraps the module unchanged. The `CompiledModule` is not modified by any step in `module`.

---

**Cross-Function Dependencies:**
- Callee `move_bytecode_verifier::verify_module_with_config_unmetered` (external-source-available,
  V:L93-L98): all intra-module safety; every pass on the only path (V:L86-L90, V:L106-L119). The caller depends
  on it for bounds (V:L106) before its own unchecked indexing, and for self-handle ownership of defs
  (CD:L369-L376) that makes T:L94 name the module's own handle.
- Callee `script_signature::verify_module_function_signature_by_name` (external-source-available,
  SS:L60-L80): with `no_additional_script_signature_checks` (SS:L122-L129) it only enforces the legacy
  version<5 entry checks (SS:L114-L118); returns Ok otherwise.
- Callee `NativeFunctions::resolve` (internal, NF:L178-L185): name-triple existence only.
- Callee `checked_as!` (external-source-available, move_binary_format): converts usize to u16 or errors.
- Callers: `package` (T:L48-L50), called from `validate_package` (VM:L102), which is reached from
  `validate_for_publish` (VM:L56; runtime/mod.rs:L449) for publish/upgrade and from
  `package_resolution.rs:L151` when loading packages from storage into the process-wide cache. Both treat an
  `Ok` as permission to JIT and cache the package.
- Shared state: none read or written directly. The result feeds `MoveCache.package_cache` via the callers
  (ORIENTATION: cache/move_cache.rs), which is never evicted, so the verification outcome for a VersionId is
  computed under the VMConfig in force at first load and reused.
- Invariant couplings: Sui-specific verifier passes (sui-verifier: id leak, private generics, entry rules) are
  not invoked in `module`; they run in the adapter at publish time. On reload from storage only the passes in
  T:L66-L77 run.

---

**Open Questions:**
- unclear; need to inspect the adapter publish path (sui-adapter execution/context.rs `publish_and_init_package`
  and `substitute_package_id`) to confirm which `original_id` values an untrusted publisher can cause
  `validate_for_publish` to receive, and therefore whether any untrusted module can have a self address present
  in the native table (T:L95-L101 checks only name existence).
- unclear; need to inspect sui-verifier and the adapter for any check that native function declarations match
  the Rust implementation's expected signature for system packages, or that restricts `native` fdefs to
  system addresses; nothing found in T or NF.
- unclear; need to inspect jit/execution/translate.rs to confirm it resolves natives with the same
  `NativeFunctions` instance and handles a `None` from `resolve` (relevant if the table differs between
  verification and JIT).
- unclear; need to inspect whether any runtime or adapter code omits an entry-function signature check on the
  basis of the comment at T:L67; under current protocol (min version 6, PC:L3521) T:L68-L76 checks nothing.
- unclear; need to inspect move_binary_format EnumDefinition to confirm enums have no native form, since
  T:L113-L121 only inspects struct defs.
- unclear; need to inspect whether package_cache entries loaded before a protocol upgrade that changes
  `verifier_config` fields (e.g. `additional_borrow_checks`, `deprecate_global_storage_ops`) are re-verified;
  `module` itself has no notion of config version.
