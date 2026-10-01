## `IdentifierInterner::resolve_ident` (L45-L51) and `IdentifierInterner::get_or_intern_str_internal` (L78-L85) in external-crates/move/crates/move-vm-runtime/src/cache/identifier_interner.rs

Abbreviations: `II` = external-crates/move/crates/move-vm-runtime/src/cache/identifier_interner.rs;
`IDENT` = external-crates/move/crates/move-core-types/src/identifier.rs;
`MC` = external-crates/move/crates/move-vm-runtime/src/cache/move_cache.rs;
`PR` = external-crates/move/crates/move-vm-runtime/src/runtime/package_resolution.rs;
`TR` = external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs;
`DT` = external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs.

**Purpose:** The interner maps identifier strings (module, datatype, function, field, variant names) to
compact `IdentifierKey(Spur)` handles (II:L16, II:L23) used as keys inside JIT-compiled packages and
virtual-dispatch tables. `get_or_intern_str_internal` is the only write path into the interner
(II:L81); `resolve_ident` is the reverse lookup that turns a key back into an owned `Identifier`
(II:L45-L51), skipping identifier validation via `Identifier::new_unchecked` (II:L47). Both convert
every failure into a process panic (II:L49, II:L83) instead of a `PartialVMError`.

One interner exists per `MoveCache` (MC:L46, MC:L71), shared via `Arc` across clones of the cache
(MC:L305) and across all threads executing against that cache. It has no removal API: the only
methods are `new`, `resolve_ident`, `intern_identifier`, `intern_ident_str`, `get_ident_str`,
`get_or_intern_str_internal`, `size` (II:L29-L91). Content therefore grows monotonically for the
lifetime of the `MoveCache`.

---

**Inputs & Assumptions:**

`resolve_ident(&self, key: &IdentifierKey, key_type: &str) -> Identifier`
- `key`: an `IdentifierKey`. Trust: internal. The tuple field is private (II:L23 has no `pub` on the
  field) and the only construction sites in the crate are II:L70 (`get_ident_str`, via `.map(IdentifierKey)`)
  and II:L82 (`get_or_intern_str_internal`). A grep for `IdentifierKey(` across
  move-vm-runtime/src returns only II:L23 and II:L82 (L70 uses the constructor as a function value).
  So every key value was produced by *some* `IdentifierInterner`.
- `key_type`: static label for the panic message only (II:L49). Trust: trusted.
- Implicit: `self.0` is the `ThreadedRodeo` (II:L16), shared across threads.
- Preconditions:
  1. The key was produced by *this* interner instance. Established by: nothing in the type; `IdentifierKey`
     carries no interner identity (II:L20-L23, `Copy` + `Eq` + `Hash`). Structural containment only: keys
     are stored in packages/vtables built against `cache.interner` (PR:L220, PR:L258) and resolved via
     that same cache's interner (MC:L305 clones the same `Arc`). A key from a different `MoveCache`
     resolves either to a different string or to the panic at II:L49.
  2. The string stored under the key is a valid Move identifier. Established by: the input types of the
     only write path — `intern_identifier(&Identifier)` (II:L55-L57) and `intern_ident_str(&IdentStr)`
     (II:L61-L63) — combined with the validity invariant of `Identifier`/`IdentStr`, which is itself
     established by `Identifier::new` (IDENT:L110-L117) / `IdentStr::new` (IDENT:L209-L215) but *bypassable*
     by `Identifier::new_unchecked` (IDENT:L126-L128). See Cross-Function Dependencies.

`get_or_intern_str_internal(&self, string: &str) -> IdentifierKey` (private, II:L78)
- `string`: `borrow_str()` of an `Identifier` or `IdentStr` (II:L56, II:L62). Content originates from
  untrusted published bytecode (module identifier pool) and from the on-chain package `type_origin_table`.
  Trust: validated upstream (see below), content attacker-chosen.
- Implicit: current memory usage and key-space occupancy of the rodeo.
- Preconditions: none checked locally. The call can only be reached with a `&str` borrowed from an
  `Identifier`/`IdentStr` (private fn, only callers II:L56, II:L62).

---

**Outputs & Effects:**

`resolve_ident`
- Success path: allocates and returns a fresh owned `Identifier` wrapping a `Box<str>` copied from the
  rodeo's string (II:L47; `new_unchecked` body is `Self(s.into())`, IDENT:L126-L128, where `&str -> Box<str>`
  copies). No interner state is written.
- Failure path: `panic!` (II:L49). In release builds of sui-node `panic=abort` (CLAUDE.md, Build flags),
  so the whole process terminates.

`get_or_intern_str_internal`
- Success: returns the existing key if the string is already interned, else inserts it and returns a new key
  (II:L81-L82). Writes shared, cross-thread interner state.
- Failure: `panic!("Identifier interner OOM")` (II:L83) on any `Err` from `try_get_or_intern`, whatever the
  error variant.

---

**Block-by-Block:**

```rust
// II:L29-L34 (constructor, context for both functions)
pub fn new() -> Self {
    let memory_limits = lasso::MemoryLimits::new(IDENTIFIER_INTERNER_SIZE_LIMIT);
    let rodeo = ThreadedRodeo::with_memory_limits(memory_limits);
    Self(rodeo)
}
```
- **What:** builds the rodeo with a memory limit of `IDENTIFIER_INTERNER_SIZE_LIMIT = 10_000_000_000`
  (external-crates/move/crates/move-vm-runtime/src/shared/constants.rs:L42).
- **Assumes:** the constant's doc comment says "Maximum number of identifiers we can ever intern"
  (constants.rs:L40) while it is passed to `MemoryLimits::new`; whether lasso interprets it as a byte count
  or an item count is not verified here (lasso source not present locally; see Open Questions). Its TODO
  says it should be "experimentally determined" (constants.rs:L41).
- **Establishes:** the only bound on interner growth. No other cap exists in this file.
- **Depended on by:** the `Err` branch at II:L83.

```rust
// II:L45-L51
pub(crate) fn resolve_ident(&self, key: &IdentifierKey, key_type: &str) -> Identifier {
    if let Some(result) = self.0.try_resolve(&key.0) {
        unsafe { Identifier::new_unchecked(result) }
    } else {
        panic!("Failed to resolve {key_type} key in ident interner: {key:?}")
    }
}
```
- **What:** looks the key up in the rodeo; on hit wraps the string as an `Identifier` without validation;
  on miss panics.
- **Why here:** used on paths that need a real `Identifier` (module IDs, type names, error messages) from
  the compact key.
- **Assumes:**
  - (a) the key belongs to this interner (nothing in the type enforces it; see precondition 1).
  - (b) the stored string is a valid identifier (established only transitively; see precondition 2).
  - (c) `try_resolve` returns `Some` for every key this interner ever returned — i.e. the rodeo never
    forgets keys. II has no removal method (II:L29-L91), so no in-crate path removes entries.
- **Establishes:** on return, the result is an `Identifier` whose validity equals the validity of whatever
  was interned; the function does not itself re-check (the `unsafe` block at II:L47 is the entire check
  elision). `Identifier::new_unchecked` has no memory-safety precondition — it is a safe-bodied
  constructor marked `unsafe` for a logical invariant (IDENT:L119-L128); `Box<str>` from `&str` is always
  valid UTF-8. The `unsafe` here only concerns the identifier-grammar invariant (IDENT:L66-L76).
- **Depended on by:** every caller that formats or constructs a `ModuleId`/`StructTag`/type name:
  TR:L169 (error message on function resolution failure), TR:L575, ast.rs:L1036, ast.rs:L1045,
  ast.rs:L1649, DT:L498, DT:L508, DT:L652, DT:L825, DT:L834, DT:L859, DT:L1157, DT:L1162, DT:L1188-L1189.
  DT:L1146 and DT:L1151 carry the comment "[SAFETY] This cannot be ade public, because it may lead to panics
  in the interner", i.e. the authors rely on crate-privacy of keys to keep (a) true.

```rust
// II:L78-L85
fn get_or_intern_str_internal(&self, string: &str) -> IdentifierKey {
    match self.0.try_get_or_intern(string) {
        Ok(result) => IdentifierKey(result),
        Err(err) => panic!("Identifier interner OOM: {err:?}"),
    }
}
```
- **What:** returns the existing key or inserts; any error panics.
- **Why here:** single choke point for all writes (callers II:L56, II:L62 only).
- **Assumes:**
  - `try_get_or_intern` returns the same key for equal strings (dedup) and is linearizable across threads.
    External, not inspected (lasso 0.7.3 per external-crates/move/Cargo.toml:L77 and Cargo.lock:L2093-L2096).
  - Every `Err` is "OOM": the panic message says OOM for all variants (II:L83); the actual error set of
    `try_get_or_intern` is not inspected here (Open Questions).
  - Reaching the limit is not attacker-drivable in practice. Established by: nothing found. The interner
    has no eviction (II:L29-L91), `MoveCache` has no interner reset (MC:L42-L52, MC:L66-L75), and the only
    bound is the constant at constants.rs:L42.
- **Establishes:** on return, `string` is resident in the rodeo and the key resolves to it via
  `resolve_ident` for the life of this interner (no removal API).
- **Depended on by:** all JIT translation sites that intern names (TR:L255-L256, L328, L341, L451, L544,
  L547, L617, L630, L658, L692, L729, L770, L784, L1178, L1685, L1688, L1746, L1751, L1766, L1771).

---

**Cross-Function Dependencies:**

- Callee `ThreadedRodeo::try_resolve` (external-black-box; lasso 0.7.3 source not present on this machine):
  resolve_ident depends on it to return `Some(&str)` equal to the originally interned string for every key
  returned by this rodeo. Outcomes not excluded from this file: returning a string for a key minted by a
  different rodeo (Spur is a bare integer handle — II:L23 wraps it with no provenance); returning `None`
  for a key that was minted concurrently on another thread but not yet visible.
- Callee `Identifier::new_unchecked` (external-source-available, IDENT:L126-L128): body is `Self(s.into())`;
  no validation, no memory-unsafe operation. Caller depends on nothing from it except the copy into
  `Box<str>`.
- Callee `ThreadedRodeo::try_get_or_intern` (external-black-box): depended on for dedup, key stability,
  and for returning `Err` rather than panicking/aborting internally when limits are hit. Outcomes not
  excluded: `Err` for reasons other than the memory limit (key-space exhaustion of the 32-bit `Spur`),
  internal allocation failure that aborts before an `Err` can be returned.
- Callee `ThreadedRodeo::current_memory_usage` (II:L89; used in telemetry MC:L252 only).

- Origin of identifier validity (precondition 2), traced per write source:
  - Module identifier pools: the deserializer builds each via `Identifier::from_utf8` (move-binary-format
    src/deserializer.rs:L899), which calls `Identifier::new` (IDENT:L136-L139) → `is_valid` check
    (IDENT:L110-L117). Strings from `module.identifier_at(...)` at TR:L544, L770, L1685, L1746, L1751,
    L1766, L1771 come from that pool. Established by deserializer.rs:L899.
  - Type origin table: `IntraPackageName { module_name, type_name }` are `Identifier`s
    (move-core-types src/resolver.rs:L14-L17) built in crates/sui-types/src/move_package.rs:L591-L593 via
    `expect_valid_identifier!` → `Identifier::new` (move_package.rs:L574-L584). Established there.
  - `Identifier::new_unchecked` call sites in the repo: II:L47 (this function), crates/sui-types/src/
    type_input.rs:L145-L146 (`into_type_tag_unchecked`, whose only callers are historical-versions v0-v2
    adapters), and historical-versions v0-v3 adapters. None found in sui-execution/latest or in
    move-vm-runtime other than II:L47. `Deserialize for Identifier` goes through `FromStr` → `Identifier::new`
    (IDENT:L99-L106, L165-L170). So no path found in the latest execution layer that places an unvalidated
    string into this interner; the property is established by the type system plus the absence of
    `new_unchecked` producers feeding the new VM, not by any check at II:L47.
  - `get_ident_str` (II:L69-L71) does lookup only (`self.0.get`), never inserts — so identifiers from
    transaction inputs used for lookup (DT:L294-L295) cannot add strings.

- Callers of `resolve_ident` listed in the block analysis. Callers of `get_or_intern_str_internal`:
  `intern_identifier` (II:L56) and `intern_ident_str` (II:L62), themselves called only during JIT
  translation (TR list above).

- Reachability of interning by untrusted publishers: `jit_package_for_publish` (PR:L204-L231) calls
  `jit::translate_package` with `&cache.interner` (PR:L218-L220) for a package being published, and does
  *not* insert the result into `package_cache` (PR:L227-L230 builds a standalone `Package`). Whatever the
  publish outcome, all identifiers interned during that translation remain in the shared interner, since
  there is no removal. Same for `jit_and_cache_package` when translation returns `Err` partway
  (PR:L256-L266: `runtime_pkg?` after interning has already happened).

- Shared state: `MoveCache.interner` (MC:L46), `Arc`-shared by `MoveCache::clone` (MC:L305) and read by
  VMDispatchTables (`self.interner` at DT:L294, L498, etc.). Telemetry reads `size()` (MC:L252).

- Invariant couplings:
  - Determinism: `Spur` values depend on insertion order across threads; the type comment says keys'
    "ordering is unstable, so they should not be used as keys" (II:L18-L19) — yet `IdentifierKey` derives
    `Hash`/`Eq` (II:L20) and is used as a map key component (`IntraPackageKey`, `VirtualTableKey`, DT:L296).
    Hash-map use is order-independent for lookups; any iteration-order or `Ord` use over key values would
    be validator-divergent. Whether any such iteration exists is outside this function (Open Questions).
  - Liveness: both panics terminate the validator process under `panic=abort`. Any input that reaches
    II:L49 or II:L83 is a process-crash trigger, deterministic across validators only if the interner
    contents are identical across validators — which they are not in general, because interner contents
    depend on each node's cache history (every package ever JIT'd since the `MoveCache` was created).

---

**Open Questions:**
- unclear; need to inspect lasso 0.7.3 `MemoryLimits::new` to confirm whether `IDENTIFIER_INTERNER_SIZE_LIMIT`
  is interpreted as bytes (constants.rs:L40 doc says "number of identifiers").
- unclear; need to inspect lasso 0.7.3 `ThreadedRodeo::try_get_or_intern` error variants (memory limit,
  key-space exhaustion for 32-bit `Spur` ≈ 4.29e9 keys, allocation failure) and whether any path panics or
  aborts internally rather than returning `Err`.
- unclear; need to inspect whether lasso's memory accounting covers only arena string bytes or also the
  internal hash map / key vector; if only string bytes, actual resident memory can exceed the 10e9 limit.
- unclear; need to inspect `ThreadedRodeo::try_resolve` visibility semantics under concurrent insert (a key
  handed to thread A, read by thread B before publication).
- unclear; need to inspect how long a `MoveRuntime`/`MoveCache` lives in a validator (created in
  sui-execution/latest/sui-adapter/src/adapter.rs:L43-L53; replaced at epoch change execution_engine.rs:L2025?)
  to bound interner growth per lifetime.
- unclear; need to inspect whether JIT translation during publish (PR:L204-L231) is gas-metered or otherwise
  bounded per transaction, since each distinct identifier (up to IDENTIFIER_SIZE_MAX = 65535 bytes,
  file_format_common.rs:L147) is retained permanently.
- unclear; need to inspect whether any code iterates over or orders `IdentifierKey` / `IntraPackageKey` /
  `VirtualTableKey` values (e.g. BTreeMap, sorted vec) in a way that reaches effects or gas.
- unclear; need to inspect whether any path can mix keys from two distinct `MoveCache` instances (e.g.
  system packages or `VMDispatchTables` built by one runtime and used with another runtime's interner).
