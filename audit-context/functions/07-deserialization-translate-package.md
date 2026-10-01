# deserialization::translate::package

File: external-crates/move/crates/move-vm-runtime/src/validation/deserialization/translate.rs:L20-L77
Paths below are relative to external-crates/move/crates unless stated otherwise.

## Purpose

Turns a `SerializedPackage` (a map from module name to raw bytes, plus version_id, original_id, linkage
table, type origin table, version) into a `deserialization::ast::Package` (translate.rs:L20). Every module
is deserialized under `vm_config.binary_config`, and four package-level checks run. The doc comment lists them
(translate.rs:L14-L18): map key equals the module's self name, module self address equals original_id, no
duplicate names, and the package is non-empty.

## Inputs and trust

- `vm_config: &VMConfig`. Only `vm_config.binary_config` is used (translate.rs:L30). In the Sui adapter it is
  set from `protocol_config.binary_config(None)` (sui-execution/latest/sui-adapter/src/adapter.rs:L71). That
  function builds the version bounds, the no-extraneous-bytes flag, deprecate_global_storage_ops and the table
  limits from protocol config (crates/sui-protocol-config/src/lib.rs:L4893-L4918).
- `pkg: SerializedPackage` (move-core-types/src/resolver.rs:L23-L46). On the publish path the bytes come from
  the untrusted publisher. On the load path they come from a `ModuleResolver` store
  (move-vm-runtime/src/runtime/package_resolution.rs:L161-L169).
  - `modules: BTreeMap<Identifier, Vec<u8>>` (resolver.rs:L24). Because it is a BTreeMap, its keys are unique
    and iteration follows `Identifier` order.
  - `version_id`, `original_id`, `linkage_table`, `type_origin_table`, `version` are caller-supplied metadata.
    This function checks none of them against anything except `original_id`, which is compared with each
    module's address (translate.rs:L44).

## Block-by-block

### L26-L28: setup
Copies `original_id` (AccountAddress is Copy) and creates an empty `BTreeMap<ModuleId, CompiledModule>`.

### L29-L31: per-module deserialization
`CompiledModule::deserialize_with_config(module, &vm_config.binary_config)` (move-binary-format/src/deserializer.rs:L37-L44):
1. `deserialize_compiled_module` (deserializer.rs:L359-L382):
   - `VersionedBinary::initialize` checks the version against `min_binary_format_version`
     (deserializer.rs:L2184) and against `min(max_binary_format_version, VERSION_MAX)` (deserializer.rs:L2189).
   - It reads `self_module_handle_idx` from the binary (deserializer.rs:L366). No bounds check happens at this
     point.
   - `build_compiled_module` builds the tables (deserializer.rs:L374).
   - Trailing bytes are rejected only when `binary_config.check_no_extraneous_bytes` is set
     (deserializer.rs:L378). When the flag is off, trailing bytes are allowed and the module is returned.
2. `BoundsChecker::verify_module` (move-binary-format/src/check_bounds.rs:L36-L52):
   - Rejects an empty module_handles table (check_bounds.rs:L46-L50).
   - Otherwise runs `verify_impl` (check_bounds.rs:L51). That includes `check_module_handles`: every handle's
     address and name index is checked against address_identifiers/identifiers (check_bounds.rs:L101-L106,
     L199-L202). It also includes `check_self_module_handle`: self_handle_idx is checked against module_handles
     (check_bounds.rs:L204-L206).
   - Both paths out of `verify_module` either return an error or run the full `verify_impl`. The two checks
     this function depends on run before any fallible step that could skip them (check_bounds.rs:L55-L58), and
     `?` propagates every failure.

Errors are finished with `Location::Package(pkg.version_id)` (translate.rs:L31).

What the caller depends on: after L30 returns Ok, `module.self_id()`, `module.name()`, `module.address()` and
`module.self_handle_idx()` can index without panicking. `self_handle()` indexes `module_handles[self_idx]`
(file_format.rs:L2455-L2460, L2484-L2485). `identifier_at` and `address_identifier_at` are raw slice indexes
(file_format.rs:L2551-L2557). `self_id` builds a `ModuleId` from both (file_format.rs:L2780-L2790). The bounds
checks above are what make these indexes safe. No other code between L30 and L34 establishes that.

### L33-L42: name match
The map key `mname` is compared with `module.self_id().name()` (translate.rs:L34). A mismatch returns
`UNKNOWN_INVARIANT_VIOLATION_ERROR` (translate.rs:L36). This is an invariant-violation status, not a
verification status. The status class tells the upstream code whether a failure is reported as a user error or
as an invariant violation. Unclear how the Sui adapter maps this status on the publish path (open question).

### L44-L51: self-address check
`module.address() != &pkg.original_id` returns `MISMATCHED_MODULE_IDS_IN_PACKAGE`. The error carries the
`self_handle_idx` index (translate.rs:L44-L50). This check makes every module's self address equal to
`original_id`. It does not look at other module handles in the module, which reference dependencies, or at
their addresses.

### L53-L61: duplicate check
Inserts under the key `module.self_id()`, which is `(original_id, name)` because of L44. The key `mname` is
unique within `pkg.modules` (a BTreeMap) and equals the module name (L34). So two insertions cannot share a
key, and this branch cannot be reached on its own. The code comment says the same (translate.rs:L53). The
check is defensive.

### L64-L70: non-empty
When `pkg.modules` is empty the loop does not run and `EMPTY_PACKAGE` is returned (translate.rs:L65-L69).

### L72-L76: construction
`Package::new(original_id, modules.into_values().collect(), pkg)` (validation/deserialization/ast.rs:L25-L38).
It rebuilds the `BTreeMap<ModuleId, CompiledModule>` by calling `self_id()` again (ast.rs:L32). It moves
`version_id`, `type_origin_table`, `linkage_table` and `version` from `pkg` without checking them
(ast.rs:L33-L36). `pkg.modules` (the raw bytes) is dropped. The resulting `Package` keeps no copy of the
original bytes (ast.rs:L11-L18).

## Invariants on return (Ok)

1. Every module deserialized under `vm_config.binary_config` and passed the bounds check (translate.rs:L30;
   deserializer.rs:L41-L42).
2. Every module's self name equals its key in the input map (translate.rs:L34).
3. Every module's self address equals `original_id` (translate.rs:L44). So every key in `Package.modules` is
   `(original_id, name)`.
4. `Package.modules` is non-empty (translate.rs:L65).
5. `Package.original_id == pkg.original_id` (translate.rs:L26, L73).
6. The number of modules equals `pkg.modules.len()`. Each entry is inserted exactly once, and the duplicate
   branch returns an error (translate.rs:L54).

## Not checked here (passed through)

- `version_id`: never compared with anything. Only used for error locations and copied through
  (ast.rs:L33).
- `linkage_table`: copied without checks (ast.rs:L35). The field doc says it "must include the self linkage"
  (resolver.rs:L33-L34), but nothing in this function checks that. `verification::translate::package` also
  passes it through unchanged (validation/verification/translate.rs:L32-L58).
- `type_origin_table`: copied without checks (ast.rs:L34). The field doc says every type must have an entry
  (resolver.rs:L36-L37), but no check here compares entries with the types defined in the modules.
  `verification::translate::package` passes it through unchanged (verification/translate.rs:L55).
- `version`: copied without checks (ast.rs:L36).
- The `publishable` flag parsed from the binary (deserializer.rs:L364) is not inspected here.
- Module handles other than self (dependencies and their addresses) are not inspected here. That belongs to
  the later linkage checks (validation/mod.rs:L70, L81).
- No gas or metering applies. Deserialization cost is bounded only by binary_config's table limits and by the
  byte length. The verifier call that follows is explicitly unmetered (verification/translate.rs:L65-L66).

## Callers

- `validation::validate_package` (validation/mod.rs:L98). This caller runs
  `verification::translate::package` next (mod.rs:L102). It is reached from:
  - `validate_for_publish` (mod.rs:L56), called from runtime/mod.rs:L449. After this function returns, the
    caller compares `validated_package.original_id` with the `original_id` argument (mod.rs:L58-L66). That
    comparison is the only place where `pkg.original_id` is bound to an independent value on the publish path.
  - `load_and_verify_packages` (runtime/package_resolution.rs:L151). It uses packages from the store. There is
    a FIXME about linkage-checking loaded packages (package_resolution.rs:L143-L144).
  - dev_utils/in_memory_test_adapter.rs:L134 (test utility).

## Determinism notes

Iteration follows `BTreeMap<Identifier, _>` order (translate.rs:L29), which is deterministic. When several
modules fail, the error comes from the first failing module in identifier order. The error depends only on the
input and on binary_config.
