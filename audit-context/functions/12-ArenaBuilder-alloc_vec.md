## `ArenaBuilder::alloc_vec` and `ArenaBuilder::alloc_box` in external-crates/move/crates/move-vm-runtime/src/cache/arena.rs (L59-L86)

File abbreviations used below:
- `arena.rs` = external-crates/move/crates/move-vm-runtime/src/cache/arena.rs
- `translate.rs` = external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs
- `ast.rs` = external-crates/move/crates/move-vm-runtime/src/jit/execution/ast.rs
- `values_impl.rs` = external-crates/move/crates/move-vm-runtime/src/execution/values/values_impl.rs
- `vm_pointer.rs` = external-crates/move/crates/move-vm-runtime/src/shared/vm_pointer.rs

**Purpose:** These two methods are the only allocation entry points into a package arena (a `bumpalo::Bump`, arena.rs:L19). Every runtime-AST object the JIT produces for a package — functions, bytecode, jump tables, types, datatype descriptors, constants — is placed in bump memory through them (translate.rs:L177-L186, L1259; values_impl.rs:L560-L563). They wrap the bump memory in std `Vec`/`Box` headers held inside `ManuallyDrop` (arena.rs:L28, L33, L66, L81), so the rest of the runtime can use `Vec`/`Box` APIs and take stable `VMPointer`s into the memory (arena.rs:L121-L126, translate.rs:L1090). Without them, no package can be translated; without the size cap enforced through the bump's allocation limit (arena.rs:L52), translation memory per package is unbounded.

**Inputs & Assumptions:**
- `&self` (ArenaBuilder): one per package translation, created at translate.rs:L271 from `vm_config`. `Bump` is not `Sync`, and allocation only takes `&self`; `ArenaBuilder` has no manual `Send`/`Sync` impl (only `Arena` does, arena.rs:L145-L146). Trust: trusted (runtime-internal).
- `items` (alloc_vec, `impl ExactSizeIterator<Item = T>`): elements to copy into the arena. The element *values* are derived from untrusted publisher bytecode (function signatures, constants, bytecode), but the *iterator* is always a std iterator constructed by the runtime: `Vec::into_iter` (e.g. translate.rs:L554, L640, L1086, L1187, L1213, L1214, L1255, L1264; values_impl.rs:L562) or `slice::Iter::cloned` (translate.rs:L697, L741). Trust: semi-trusted (values untrusted, `len()` contract trusted because std).
- `item` (alloc_box, `T`): single value. Call sites pass `Datatype` (translate.rs:L616, L629), `u128`/`U256` constants from bytecode (translate.rs:L1484-L1485), `ArenaType` (translate.rs:L1734, L1737, L1740), and `(VirtualTableKey, ArenaVec<ArenaType>)` (translate.rs:L1774).
- Generic `T`: no trait bounds on either method (arena.rs:L59, L78). The documented restriction on `alloc_box` — "never be called on strings (or any other type that contains internal allocations or pointers)" (arena.rs:L74-L77) — is not expressed as a bound; enforced by: nothing found. `alloc_vec` carries no such restriction in its comment (arena.rs:L56-L58) and is in fact called with `T = Function`, which owns an `Option<Arc<..>>` (ast.rs:L165; natives/functions.rs:L48) at translate.rs:L1086.
- Implicit: the allocation limit set at construction, `package_arena_size` or 10,000,000 bytes (arena.rs:L45-L52, L37). Sui sets it from protocol config `package_arena_size_in_bytes` (sui-execution/latest/sui-adapter/src/adapter.rs:L63; crates/sui-protocol-config/src/lib.rs:L4751 sets `Some(10_000_000)`).

**Outputs & Effects:**
- `alloc_vec`: on bumpalo success returns `ArenaVec<T>` whose inner `Vec` has `ptr` = bump slice start, `len == cap == slice.len()` (arena.rs:L64-L66). On bumpalo failure returns `PACKAGE_ARENA_LIMIT_REACHED` (arena.rs:L69).
- `alloc_box`: on success returns `ArenaBox<T>` wrapping `Box::from_raw` on the bump slot (arena.rs:L79-L81); on failure `PACKAGE_ARENA_LIMIT_REACHED` (arena.rs:L84).
- State write: advances the bump pointer / may allocate a new bump chunk from the global allocator (inside bumpalo; source not in the workspace, see Open Questions). The allocated bytes are later readable via `allocated_bytes()` (arena.rs:L88-L90, L99-L101) and summed for telemetry (cache/move_cache.rs:L249).
- Postcondition relied on system-wide: the element memory never moves and is never freed until the owning `Bump` is dropped. The returned wrappers never free it: `ManuallyDrop` suppresses `Vec`/`Box` destructors (arena.rs:L66, L81), so neither element destructors nor the global-allocator `dealloc` run when an `ArenaVec`/`ArenaBox` is dropped.

---

**Block-by-Block:**

```rust
// arena.rs:L59-L63
pub fn alloc_vec<T>(&self, items: impl ExactSizeIterator<Item = T>) -> PartialVMResult<ArenaVec<T>> {
    if let Ok(slice) = self.0.try_alloc_slice_fill_iter(items) {
```
- **What:** asks bumpalo to allocate `items.len()` slots of `T` and move each yielded item into them.
- **Why here:** single call; everything after depends on its `Result`.
- **Assumes:** (a) `items.len()` equals the number of items the iterator yields — callers only pass std `Vec::IntoIter`/`Cloned<slice::Iter>` (see Inputs), whose `len()` is exact; (b) bumpalo returns a slice that is fully initialized, aligned for `T`, and of length `items.len()`; (c) bumpalo's `try_` variant turns both limit exhaustion and global-allocator failure into `Err` rather than panicking. (b) and (c) rest on bumpalo 3.19.1 (Cargo.lock:L3180-L3181; external-crates/move/Cargo.lock:L352-L353 pins 3.17.0 for the standalone workspace), whose source is not in the workspace.
- **Establishes:** on `Ok`, `slice: &mut [T]` into bump memory whose lifetime is erased by the conversion below.
- **Depended on by:** L64-L67.

```rust
// arena.rs:L64-L67
let size = slice.len();
Ok(ArenaVec(unsafe {
    std::mem::ManuallyDrop::new(Vec::from_raw_parts(slice.as_mut_ptr(), size, size))
}))
```
- **What:** builds a std `Vec<T>` header over bump memory with `len == capacity == size`, wrapped in `ManuallyDrop`.
- **Why here:** converts a borrow tied to `&self` into an owned, lifetime-free value that can be stored in `Module`/`Function` structs (ast.rs:L72-L123, L158-L169).
- **Assumes:**
  - `Vec::from_raw_parts` documents that `ptr` must come from the global allocator with the same layout; here it comes from bumpalo. Soundness therefore rests on the `Vec` never deallocating or reallocating. Established by: `ManuallyDrop` (L66) prevents drop-time dealloc; the `ArenaVec.0` field is private to arena.rs (L28, tuple field without `pub`), and arena.rs exposes only `iter`, `iter_mut`, `to_ptrs`, `drain`, `Deref<Target=[T]>`, `AsRef<[T]>` (L104-L131, L152-L164) — none grow, shrink, or reallocate. `Clone` is not derived (L27), so the header cannot be duplicated into a droppable `Vec`.
  - The bump memory outlives every `ArenaVec` pointing into it. `ArenaVec<T>` carries no lifetime parameter (L28). Established by: struct field ordering in the holders — `ast::Package` declares `loaded_modules` (ast.rs:L53) before `package_arena` (ast.rs:L56), and `PackageContext` declares `loaded_modules` (translate.rs:L64) before `package_arena` (translate.rs:L67), so Rust drops the modules (running `Module::drop`, ast.rs:L126-L134) before the `Bump`. `ArenaVec`s outside those structs (e.g. `vtable` VMPointers, ast.rs:L57; `Definitions` pointers, translate.rs:L85-L99; cross-package `DirectCall` pointers into system packages, translate.rs:L151-L158) are not tied to the arena by the type system — nothing found in arena.rs.
  - `cap * size_of::<T>() <= isize::MAX` and alignment match — follows from bumpalo having produced a valid `[T]` of that length.
- **Establishes:** element addresses are stable for the life of the `Bump` (no path in arena.rs can move the buffer). `VMPointer::from_ref` on elements (arena.rs:L121-L126; vm_pointer.rs:L27-L29) relies on this.
- **Depended on by:** every `VMPointer<T>` into arena data: vtable function pointers (translate.rs:L1087-L1091, L491), `Definitions` (translate.rs:L495-L507), jump-table pointers (translate.rs:L1258), `sig_pointers` (translate.rs:L462-L465).

```rust
// arena.rs:L68-L70
} else {
    Err(partial_vm_error!(PACKAGE_ARENA_LIMIT_REACHED))
}
```
- **What:** maps every bumpalo error to one status code.
- **Why here:** the only failure path.
- **Assumes:** the only error bumpalo returns is capacity-related. The `if let Ok` discards the error value (L63), so a global-allocator failure and a limit hit are indistinguishable to callers.
- **Establishes:** on `Err`, no `ArenaVec` is produced. What happened to `items` on this path (moved into bumpalo and dropped, or partially consumed) depends on bumpalo internals — see Open Questions.
- **Depended on by:** callers propagate with `?` (e.g. translate.rs:L1086, L1265; values_impl.rs:L562), aborting translation of the whole package (translate.rs:L278).

```rust
// arena.rs:L78-L85
pub fn alloc_box<T>(&self, item: T) -> PartialVMResult<ArenaBox<T>> {
    if let Ok(slice) = self.0.try_alloc(item) {
        Ok(ArenaBox(unsafe {
            std::mem::ManuallyDrop::new(Box::from_raw(slice as *mut T))
        }))
    } else {
        Err(partial_vm_error!(PACKAGE_ARENA_LIMIT_REACHED))
    }
}
```
- **What:** moves one `T` into the bump and wraps the `&mut T` as a `Box<T>` inside `ManuallyDrop`.
- **Why here:** same lifetime-erasure purpose as `alloc_vec`, for single recursive nodes (`ArenaType::Vector/Reference/MutableReference/DatatypeInstantiation`, ast.rs:L295-L299) and boxed large literals (`Bytecode::LdU128(ArenaBox<u128>)`, ast.rs:L414).
- **Assumes:**
  - `Box::from_raw` documents that the pointer must be from the global allocator with `Layout::new::<T>()`. Soundness rests on the `Box` never being dropped or turned back into a raw allocation. Established by: `ManuallyDrop` (L81); `ArenaBox.0` private (L33); arena.rs exposes only `inner_ref` and `Deref` (L133-L137, L170-L176) — no `DerefMut`, no `into_inner`, no `Clone` (L32 derive list).
  - `T` has no owned heap allocations, because the `T` destructor never runs (L74-L77). Enforced by: nothing found (no trait bound). Actual call-site `T`s: `Datatype` holds only `VMPointer` (ast.rs:L315-L318); `u128` and the U256 literal (translate.rs:L1484-L1485); `ArenaType`, whose boxed/vec'd children are themselves arena-allocated (ast.rs:L288-L304); `(VirtualTableKey, ArenaVec<ArenaType>)`, where `VirtualTableKey` is `OriginalId` + two `IdentifierKey`s (execution/dispatch_tables.rs:L107-L121). So current callers satisfy "no owned heap allocations"; they do not satisfy the literal "or pointers" wording of L74-L75, since `ArenaType` nodes hold `ArenaBox`/`ArenaVec` into the same arena.
  - Arena outlives the `ArenaBox` — same field-ordering argument as `alloc_vec`; `ArenaBox` has no lifetime parameter (L33).
- **Establishes:** stable address for the boxed value; `ArenaBox` is read-only through its public API.
- **Depended on by:** type traversal code dereferencing `ArenaType` children; `DatatypeDescriptor::datatype_info` (ast.rs:L311).

---

**Cross-Function Dependencies:**

- Callee `bumpalo::Bump::try_alloc_slice_fill_iter` (external-black-box: source for 3.19.1 not present in the workspace or any local cargo registry searched). The caller depends on it to: return a slice of exactly `items.len()` initialized, `T`-aligned elements; honour the allocation limit set at arena.rs:L52 by returning `Err`; never hand out overlapping memory across calls on the same `Bump`. Not excluded from source: panicking instead of returning `Err` (for example if the iterator yields fewer than `len()` items, or on a `Layout::array` overflow); partial consumption of `items` on the error path; the limit being checked against chunk capacity rather than the bytes requested (affects where the 10 MB boundary falls).
- Callee `bumpalo::Bump::try_alloc` (external-black-box). Depended on for: a unique, aligned, initialized `&mut T`; `Err` on limit. Not excluded: whether `item` is dropped or leaked on the `Err` path.
- Callee `Bump::set_allocation_limit` (called at construction, arena.rs:L52; external-black-box). All bounding of per-package translation memory depends on it.
- Callee `Vec::from_raw_parts` / `Box::from_raw` (std, source available). Their documented preconditions (global-allocator provenance) are not met; the code relies on never reaching the dealloc/realloc paths, which is enforced by `ManuallyDrop` and the restricted API of `ArenaVec`/`ArenaBox` in arena.rs.
- Callee `partial_vm_error!` (internal macro) — constructs the error; no side effects relevant here.

- Callers:
  - `PackageContext::arena_vec` / `arena_box` (translate.rs:L177-L186) — thin forwards; used throughout `translate.rs` (L554, L616, L629, L640, L681, L694, L697, L709, L741, L756, L777, L786, L799, L824, L827, L860, L911, L926, L950, L975, L994, L1014, L1057, L1086, L1187, L1194, L1213, L1214, L1251, L1255, L1484, L1485, L1734, L1737, L1740, L1762, L1774).
  - `code` direct call `package_arena.alloc_vec` (translate.rs:L1259-L1265).
  - `Value::into_constant_value` (values_impl.rs:L556-L625), called from `constants` (translate.rs:L1051).
  - All callers assume the returned element addresses are stable and remain valid as long as the owning `Package` lives.

- Shared state: the per-package `Bump` inside `PackageContext.package_arena` (translate.rs:L67), frozen into `ast::Package.package_arena` via `finish()` (translate.rs:L300; arena.rs:L93-L95). After freezing, `Arena` is `Send + Sync` via unsafe impl (arena.rs:L145-L146); `Package`s are shared process-wide through `MoveCache.package_cache` (cache/move_cache.rs:L32-L37).

- Invariant couplings:
  - **Heap-owning `T` in `alloc_vec`.** `Function` owns `native: Option<Arc<UnboxedNativeFunction>>` (ast.rs:L165; natives/functions.rs:L48). Because `ArenaVec` never drops its elements, releasing that `Arc` depends on `Module::drop` calling `self.functions.drain()` (ast.rs:L126-L134), which moves each `Function` out and drops it (running `Function::drop`, ast.rs:L172-L179). This only covers `ArenaVec<Function>` values that end up inside a `Module`. On error paths after `preallocate_functions` returns (translate.rs:L490) and before `Module` is built (translate.rs:L512) — `insert_vtable_functions` (L491), `function_instantiations` (L493), `function_bodies` (L509, including its early `Err` at L1128-L1133 and `code(...)?` at L1141) — and on the `unique_map` error inside `preallocate_functions` (translate.rs:L1092-L1099), the `ArenaVec<Function>` is dropped without draining, so the `Function` destructors and the `Arc` decrements do not run. Establishment of Arc release on these paths: nothing found.
  - **Mutation after pointer publication.** `preallocate_functions` takes `VMPointer::from_ref(fun)` on elements (translate.rs:L1090), and they are inserted into `vtable_funs` (translate.rs:L491). `function_bodies` then writes to the same elements through `ArenaVec::iter_mut` (translate.rs:L1126, L1142-L1143; arena.rs:L111-L113). `VMPointer`'s own safety comment says `T` "must not be mutated after initial creation" (vm_pointer.rs:L12-L13). The write happens during single-threaded translation before `finish()`. Under Rust's aliasing model, `&mut` reborrows through `Vec`/`Box` headers made by `from_raw_parts`/`from_raw` interact with the earlier raw pointers derived from `&T` — recorded as a structural fact for the next phase, not resolved here.
  - **Drop order is load-bearing.** Drain in `Module::drop` reads bump memory. It is sound only because `loaded_modules` is declared before `package_arena` in both `ast::Package` (ast.rs:L53, L56) and `PackageContext` (translate.rs:L64, L67). The code has no compile-time check or comment tying the order to arena validity.
  - **Cross-package pointers.** `try_resolve_direct_function_call` copies `VMPointer<Function>` from a *system package's* arena (translate.rs:L151-L158, L164) into this package's bytecode. The `ast::Package` struct holds no `Arc` to that system package (ast.rs:L47-L58). Validity then depends on system packages being pinned in `MoveCache.system_packages` (per ORIENTATION.md L33) — outside arena.rs.
  - **Limit accounting.** Bounded memory per package is `package_arena_size` bump bytes (arena.rs:L52). Each value is first built in a global-heap `Vec` and then copied (e.g. translate.rs:L1077-L1086, L1260-L1264), so during translation peak heap usage exceeds the arena limit by the size of the transient `Vec`s. Those `Vec`s are not counted against the limit.
  - **Error-code determinism.** A global-allocator failure inside bumpalo (if it surfaces as `Err`) and a limit hit both become `PACKAGE_ARENA_LIMIT_REACHED` (arena.rs:L63-L69, L79-L84). Whether a given package hits the limit depends only on bumpalo's chunk-growth policy applied to a deterministic allocation sequence, provided bumpalo's policy does not itself depend on allocator failures (see Open Questions).

---

**Open Questions:**
- unclear; need to inspect bumpalo 3.19.1 `try_alloc_slice_fill_iter` / `try_alloc_slice_fill_with`: does it panic (e.g. `expect("Iterator supplied too few elements")`) rather than return `Err` when the iterator underfills, and does it return `Err` or panic on `Layout::array::<T>(len)` overflow? The source is not present locally.
- unclear; need to inspect bumpalo `set_allocation_limit` semantics: is the limit compared against total chunk capacity (including footers and doubling growth) or against requested bytes? This decides the exact byte count at which a package fails with `PACKAGE_ARENA_LIMIT_REACHED`, and whether that count is identical across validator builds (e.g. if chunk-size policy retries smaller chunks after an allocator failure, results could depend on host memory).
- unclear; need to inspect bumpalo `try_alloc` Err path: is `item` dropped or leaked? For current `T`s it has no heap ownership, so this only matters if a future caller passes a heap-owning `T`.
- unclear; need to inspect whether any code outside arena.rs can reach `ArenaVec::drain` (arena.rs:L128, `pub`) on an `ArenaVec` whose elements have outstanding `VMPointer`s during execution (not just in `Module::drop`). Only one use (`Module::drop`, ast.rs:L132) was confirmed in this pass; a crate-wide grep for `.drain()` on `ArenaVec` was not done.
- unclear; need to inspect `PackageVirtualTable` (execution/dispatch_tables.rs:L88) drop behaviour: it is declared after `package_arena` in `ast::Package` (ast.rs:L57), so it drops after the arena. `VMPointer` has no `Drop` impl (vm_pointer.rs:L14-L101), but any `Drop` impl on the table that dereferences its pointers would run against freed memory.
- unclear; need to inspect ZST handling: whether any call site passes a zero-sized `T` (none seen at the listed call sites), and what dangling pointer bumpalo returns for it.
