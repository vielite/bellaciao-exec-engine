## `VMPointer::to_ref` and `unsafe impl Send/Sync for VMPointer<T>` in external-crates/move/crates/move-vm-runtime/src/shared/vm_pointer.rs (L14-L101)

Abbreviations used below (all under `external-crates/move/crates/move-vm-runtime/src/`):
- `vp` = shared/vm_pointer.rs
- `arena` = cache/arena.rs
- `ast` = jit/execution/ast.rs
- `tr` = jit/execution/translate.rs
- `mc` = cache/move_cache.rs
- `pr` = runtime/package_resolution.rs
- `rt` = runtime/mod.rs
- `dt` = execution/dispatch_tables.rs
- `vm` = execution/vm.rs

**Purpose:** `VMPointer<T>` is a raw `*const T` (vp:L14) that the JIT uses to link parts of a package's
runtime AST to each other: direct calls, struct/enum/field/variant handles, constants, vector
element types, signatures, jump tables, and the per-package vtable entries. `to_ref` turns the raw
pointer into `&'a T` for any `'a` the caller picks (vp:L22-L24 -> vp:L48-L50). Nothing in the type
system ties `'a` to the pointee's owner. The `Send`/`Sync` impls (vp:L58-L59) let `Package`,
`VMDispatchTables`, and `MoveVM` hold these pointers and still be shared across executor threads.
Without them, the process-wide `DashMap<VersionId, Arc<Package>>` (mc:L37) would not be `Sync`.

---

**Inputs & Assumptions:**
- `&self` (`VMPointer<T>`): wraps the `*const T` built by `from_ref(&T)` (vp:L27-L29). `from_ref` is
  `pub(crate)` (vp:L27), so only code inside the crate can build one. The type itself is `pub`
  (vp:L14; `pub mod vm_pointer` shared/mod.rs:L24; `pub mod shared` lib.rs:L31). Trust: semi-trusted.
  The address comes from crate code, but the pointee content comes from translating untrusted,
  verified bytecode.
- Implicit: the pointee memory is alive for all of `'a`. It lives in a bumpalo `Arena` owned by a
  `jit::execution::ast::Package` (ast:L56), and that package is owned through an `Arc`.
- Preconditions (none of them is checked at the call site; see the table):

| Precondition | What establishes it |
|---|---|
| P1: the pointer is non-null, aligned, and points to an initialized `T` | Every constructor goes through `from_ref(&T)` (vp:L27-L29). A Rust reference guarantees this at construction time. |
| P2: the pointee is alive for the chosen `'a` | Nothing in `to_ref`. Owner-side mechanisms are listed under "Lifetime tracing" below. |
| P3: no `&mut T` or write to the pointee coexists with the returned `&T` | After freezing: `Arena` exposes no allocation methods (arena:L98-L102), and `Package` is only reached through `Arc` (mc:L143-L146). During construction, pointees are written after pointers to them already exist (tr:L760-L802, tr:L1087-L1143). The vp:L12-L13 comment is the only statement of the rule. |
| P4: `T` is safe to share across threads (for the `Send`/`Sync` impls) | Nothing found. vp:L58-L59 place no bound on `T`. |

---

**Outputs & Effects:**
- `to_ref` returns `&'a T` with an unconstrained lifetime (vp:L22, vp:L48). It writes no state.
- `Deref` (vp:L95-L101), `Debug` (vp:L63), `PartialEq` (vp:L70), `PartialOrd`/`Ord` (vp:L79, vp:L85),
  and `Hash` (vp:L91) all route through `to_ref`. As a result, a safe `*ptr`, `==`, hashing, or
  formatting call is an unchecked dereference.
- The `Send`/`Sync` impls (vp:L58-L59) are unconditional in `T`.

---

**Block-by-Block:**

```rust
// vp:L14
pub struct VMPointer<T>(*const T);
```
- **What:** a newtype over a raw const pointer. It has no `PhantomData<&T>` and no lifetime parameter.
- **Why here:** a raw pointer removes both the auto-trait inference and the borrow tracking.
- **Assumes:** P2 and P3.
- **Establishes:** nothing. Because `*const T` is `!Send + !Sync`, the auto traits are gone until
  vp:L58-L59 add them back.
- **Depended on by:** every `VMPointer` field in ast (e.g. ast:L198, L231, L242, L439, L481-L853) and in
  `PackageVirtualTable` (dt:L90-L92).

```rust
// vp:L20-L24, L46-L50
pub(crate) fn to_ref<'a>(&self) -> &'a T { to_ref(self.0) }
fn to_ref<'a, T>(value: *const T) -> &'a T { unsafe { &*value as &T } }
```
- **What:** an unchecked dereference that returns a reference with a caller-chosen lifetime.
- **Why here:** it lets interpreter frames keep `&Function` without borrowing from `VMDispatchTables`.
  For example, `CallFrame::function<'a>(&self) -> &'a Function` at execution/interpreter/state.rs:L481-L483
  returns a reference whose lifetime is unrelated to `&self`.
- **Assumes:** P1, P2, P3.
- **Establishes:** nothing.
- **Depended on by:** interpreter/mod.rs:L44 and L49, eval.rs:L840, L981, L1171, vm:L236-L243, L384-L406,
  L450, dt:L455-L801, interpreter/helpers.rs:L29-L72, and tracing/tracer.rs:L1003-L1960.
  `#[allow(clippy::not_unsafe_ptr_arg_deref)]` (vp:L46) silences the lint that flags a safe fn
  dereferencing a pointer argument.

```rust
// vp:L56-L59
unsafe impl<T> Send for VMPointer<T> {}
unsafe impl<T> Sync for VMPointer<T> {}
```
- **What:** unconditional thread-safety claims for every `T`.
- **Assumes:** (a) the pointee is never mutated after it is shared (vp:L12-L13, L56); (b) `T` itself
  can be shared, i.e. `T: Sync`, because sending a `VMPointer<T>` and dereferencing it gives another
  thread a `&T`. Nothing enforces (b): there is no `T: Sync` bound.
- **Actual `T`s:** `Function`, `StructDef`, `EnumDef`, `VariantDef`, `StructInstantiation`,
  `EnumInstantiation`, `VariantInstantiation`, `FieldHandle`, `FieldInstantiation`,
  `FunctionInstantiation`, `Constant`, `ArenaType`, `ArenaVec<ArenaType>`, `VariantJumpTable`, and
  `DatatypeDescriptor` (grep of `VMPointer<` in ast/tr/dt).
  - A grep of ast.rs for `Cell<|RefCell|Rc<|Mutex|RwLock|Atomic` found no match.
  - `Function::native` is `Option<NativeFunction>` (ast:L165), where
    `NativeFunction = Arc<dyn Fn + Send + Sync>` (natives/functions.rs:L43-L48).
  - `ConstantValue` holds only primitives and `ArenaVec`s (values_impl.rs:L175-L194).
  - So for the `T`s in use today, (b) holds by construction. No compile-time check enforces it for
    future `T`s.
- **Establishes:** `Package`, `PackageVirtualTable`, `VMDispatchTables`, and `MoveVM` are
  `Send`/`Sync` as far as their `VMPointer` fields go. `Package` also depends on
  `unsafe impl Sync for Arena` (arena:L145-L146).
- **Depended on by:** `MoveCache.package_cache: Arc<DashMap<VersionId, Arc<Package>>>` (mc:L37, L44).
  Its `linkage_vtables` QCache of `VMDispatchTables` (mc:L45) is shared through `Arc<MoveCache>`
  (rt:L76, rt:L236-L238).

```rust
// vp:L67-L72
impl<T: PartialEq> PartialEq for VMPointer<T> {
    fn eq(&self, other: &Self) -> bool { self.ptr_eq(other) || self.to_ref().eq(other.to_ref()) }
}
```
- **What:** two pointers are equal if the addresses match or the pointees compare equal. `Ord`, `Hash`,
  and `PartialOrd` (vp:L77-L93) use the pointee only.
- **Assumes:** P2 for both operands.
- **Note:** equality accepts an address match as a shortcut, but ordering and hashing always compare
  pointee content.

```rust
// vp:L95-L101
impl<T> Deref for VMPointer<T> { fn deref(&self) -> &T { self.to_ref() } }
```
- **What:** a safe `Deref`. Its return lifetime is tied to `&self`, but the pointee is still
  dereferenced without a check (P2).
- **Depended on by:** implicit uses such as `ptr.name` (tr:L109) and `ptr.intra_package_key()` (tr:L123).

---

**Creation sites (every path to `from_ref`)**

All creation goes through `from_ref(&T)` (vp:L27). The call sites:

1. `ArenaVec::to_ptrs` (arena:L121-L126). Used for datatype descriptors (tr:L467), for the per-module
   `Definitions` tables (tr:L497-L506), and for jump tables (tr:L1258).
2. `tr:L464`: pointers to signatures in `instantiation_signatures`.
3. `tr:L616`, `L629`: `Datatype::Struct/Enum(VMPointer)` placed in an `arena_box`.
4. `tr:L761`: `enum_def` pointer made from `&mut EnumDef` reborrowed as `&`. The enum's `variants`
   field is assigned afterwards (tr:L802), while every `VariantDef` in it holds that same `enum_def`
   pointer (tr:L793).
5. `tr:L834`: the signature map.
6. `tr:L877`, `L940`, `L1008-L1010`: variant pointers.
7. `tr:L208`: vector element type (`get_vec_type`).
8. `tr:L1090`: function pointers into `loaded_functions`, before bodies exist. These go into
   `vtable_funs` (tr:L491) and become `DirectCall` targets. Afterwards `function_bodies` iterates the
   same `ArenaVec<Function>` with `iter_mut()` (tr:L1126) and writes `fun.code` and `fun.jump_tables`
   (tr:L1142-L1143). So `&mut Function` exists while `VMPointer`s derived from `&Function` are held in
   `vtable_funs`, and while earlier-translated bodies in the same module already hold `DirectCall`s to
   it. This is single-threaded and happens before the `Package` is wrapped in `Arc`.
9. Cross-package pointers: `try_resolve_direct_function_call` (tr:L144-L175) copies a pointer
   (`ptr_clone`) out of a **system package's** vtable (tr:L151-L158) into the package under
   translation.

Pointer stability during construction: `alloc_vec` puts the slice in the bump arena and wraps it in
`ManuallyDrop<Vec>` (arena:L63-L66). Moving the `ArenaVec` header therefore does not move the elements,
and dropping it frees nothing (arena:L25-L28). `alloc_box` works the same way (arena:L78-L86).

---

**Lifetime tracing (what keeps each class of pointee alive)**

| Pointer class | Pointee owner | Keeper while `to_ref` results are used |
|---|---|---|
| Same-package (all intra-module tables, same-package `DirectCall`, vtable entries) | That package's `Arena` (ast:L56) | The same `jit::Package` whose code contains the pointer. It is reached through `VMDispatchTables.loaded_packages: Arc<BTreeMap<OriginalId, Arc<Package>>>` (dt:L68), held by `MoveVM.virtual_tables` (vm:L52). |
| Pointers returned by `resolve_function` / `resolve_type` / `try_resolve_*` (dt:L201-L286) | The package found in `self.loaded_packages` | The same `Arc` inside `VMDispatchTables`. |
| Cross-package `DirectCall` into a pinned system package (tr:L151-L158) | The system package's `Arena` | `MoveCache.system_packages` (mc:L51). Its doc comment (mc:L47-L50) states the soundness rule: these Arcs outlive every user package compiled against them. `jit::Package` has no field that holds the dependency's `Arc` (ast:L47-L58). `MoveVM` holds no `Arc<MoveCache>` (vm:L50-L63). |

Supporting facts:
- **Package cache never evicts** in non-test builds. The only removal is `remove_package` under
  `#[cfg(test)]` (mc:L211-L215).
- **LRU eviction of `linkage_vtables`** does not free packages. The cache hands out clones
  (mc:L196), and `MoveVM` owns one (rt:L326-L333, vm:L52).
- **Publish path.** `jit_package_for_publish` does not insert into the cache (pr:L227-L230). The only
  owner is the `VMDispatchTables` built at rt:L464-L475 and moved into the returned `MoveVM`
  (rt:L478-L485).
- **System-package set is fixed per `MoveCache`.** `add_system_package` needs `Arc::get_mut` on the
  map, which succeeds only while the map `Arc` is unique (mc:L87-L100). `install_system_packages`
  needs `Arc::get_mut` on the cache (rt:L160-L171). `MoveCache::clone` copies the `system_packages`
  Arc together with the `package_cache` Arc (mc:L292-L309). So one `package_cache` is never paired with
  a different system-package set.
- **Sui production uses no system packages.** `new_move_runtime` calls `MoveRuntime::new`
  (sui-adapter/src/adapter.rs:L49-L52), which installs `SystemPackages::empty()` (rt:L46), and
  `install_system_packages` returns early when the set is empty (rt:L102-L104). `effective` is then
  empty (pr:L296-L298), so cross-package `DirectCall` is not produced there. Only same-package direct
  pointers and vtable-resolved pointers exist. `new_with_system_packages` is called in unit tests and
  language-benchmarks (grep results).
- **Drop order of `jit::Package`.** Fields drop in declaration order (ast:L47-L58):
  1. `loaded_modules`: `Module::drop` drains `functions`, running `Function::drop`, which releases the
     native `Arc` (ast:L126-L134, L172-L179).
  2. `package_arena`: frees the bump memory.
  3. `vtable`: holds only raw `VMPointer`s, which have no `Drop`.

  No `VMPointer` is dereferenced during drop (vp has no `Drop` impl).
- **Error path in translation.** If `package()` fails after the arena has been partly filled, the
  `PackageContext` is dropped (tr:L278 `?`). Its pointers never escape because no `Package` is
  returned. A system-package vtable is only read (tr:L154-L158), never written.

---

**Cross-Function Dependencies:**
- **Callee `to_ref` (free fn, vp:L48-L50), internal.** `unsafe { &*value }`. It has one path and no
  checks.
- **Callee `ArenaBuilder::alloc_vec` / `alloc_box` (arena:L59-L86), internal.** Establish that pointee
  addresses stay the same for the arena's lifetime. The error path returns `PACKAGE_ARENA_LIMIT_REACHED`
  (arena:L69, L84) before any pointer is formed.
- **Callee `ArenaBuilder::finish` (arena:L93-L95), internal.** Establishes that no allocation API is
  reachable after freezing. The frozen `Arena` exposes only `allocated_bytes` (arena:L98-L102).
- **Callee `ArenaVec::iter_mut` (arena:L111-L113, `pub(crate)`), internal.** This is the one API that
  mutates arena elements. Its call sites are tr:L760 and tr:L1126, both during construction.
- **Callers of `to_ref`:** the interpreter and its frames, the dispatch tables, `MoveVM` function
  lookup, the tracer, and the ast `Display` code (listed above). All of them get their pointer either
  from a `VMDispatchTables` they borrow or from bytecode inside a package owned by that
  `VMDispatchTables`.
- **Shared state:** `MoveCache.package_cache`, `MoveCache.system_packages`, `MoveCache.linkage_vtables`,
  and the package arenas.
- **Invariant couplings:**
  - I1: `to_ref` is sound only while some `Arc<jit::Package>` owning the pointee is alive. The code
    that keeps it alive is the ownership chain `MoveVM` -> `VMDispatchTables` -> `loaded_packages`.
    For system-package direct calls the chain is `MoveCache.system_packages` instead.
  - I2: the pointee is immutable after `Arc::new` (mc:L143-L146, pr:L227-L230). No `&mut` path through
    `Arc<Package>` exists: the fields are `pub(crate)` and there is no `get_mut` usage (grep of jit
    found no `unsafe`, `as *mut`, or `iter_mut` outside tr:L760 and tr:L1126).

---

**Open Questions:**
- When `new_with_system_packages` is used, a user package JIT'd with a direct call into pinned version
  V_p (the filter at pr:L304-L306 uses that package's *own* linkage table) can later execute under a
  transaction `LinkageContext` that maps the same OriginalId to another version. In that case V_p's
  `Arc` is not in `VMDispatchTables.loaded_packages`, and only `MoveCache.system_packages` keeps it
  alive.
  - Unclear whether `validate_for_vm_execution` (rt:L372) rejects such mismatches. Need to inspect
    `validation/` linkage checks.
  - Also unclear whether a `MoveVM` can outlive its `MoveRuntime`/`MoveCache`. `MoveVM` holds no cache
    `Arc` (vm:L50-L63).
- `MoveVMFunction` (vm:L65-L69) stores a `VMPointer<Function>` with no lifetime link to the `MoveVM`
  that produced it. Unclear whether any public API lets a `MoveVMFunction` be used with a different
  `MoveVM` or after its `MoveVM` is dropped. Need to inspect the callers of `find_function` (vm.rs)
  and the `MoveVMFunction` visibility at the crate boundary.
- Aliasing during construction: `VMPointer`s built from `&Function` / `&EnumDef` (tr:L1090, tr:L761)
  remain in use after `&mut` writes to the same objects (tr:L1142-L1143, tr:L802). Unclear whether
  this matters under the Rust aliasing model (Stacked/Tree Borrows). Need to run Miri on the JIT unit
  tests.
- `Bump::allocated_bytes` is called on a frozen `Arena` that `Sync` (arena:L145-L146) exposes to
  concurrent readers (mc:L249). Unclear whether bumpalo's implementation touches any `Cell` state on
  that path. Need to inspect bumpalo `allocated_bytes`.
