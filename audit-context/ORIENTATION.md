# Orientation — new Sui Move VM ("bella-ciao"), sui @ main fd2e2a0fc6

Scope: external-crates/move/crates/move-vm-runtime/src; sui-execution/latest/{sui-adapter,sui-move-natives,sui-verifier}; sui-execution/src glue.
Language: Rust (runs Move bytecode).

## Actors
- Transaction sender (PTB author) — untrusted
- Gas sponsor — untrusted
- Package publisher / upgrader (arbitrary bytecode, type origin and linkage via deps) — untrusted
- RPC user calling dev-inspect / dry-run on fullnodes — untrusted
- Validators and consensus producing system txs (epoch change, prologue, system package upgrades) — trusted
- Protocol config and governance (limits, feature flags, execution version) — trusted
- Framework system packages (0x1, 0x2, 0x3, ...) and native implementations — trusted
- Local backing store and object cache (BackingStore, ChildObjectResolver) — trusted
- Concurrent executor threads sharing one MoveRuntime — semi-trusted

## Entrypoints
- Executor::execute_transaction_to_effects(_and_execution_error) — sui-execution/src/latest.rs — any tx sender/sponsor after consensus ordering; validators for system txs
- Executor::dev_inspect_transaction — sui-execution/src/latest.rs — untrusted RPC users on fullnodes; not consensus-affecting but shares process-wide MoveRuntime cache
- Verifier::meter_compiled_modules (run_metered_move_bytecode_verifier + sui_verify_module_metered) — sui-execution/src/latest.rs — untrusted publishers at signing/validation time, pre-consensus
- static_programmable_transactions::execute — sui-adapter/src/static_programmable_transactions/mod.rs — senders submitting PTBs (MoveCall, TransferObjects, SplitCoins, MergeCoins, MakeMoveVec, Publish, Upgrade)
- Context::vm_move_call -> MoveVM::execute_function_bypass_visibility_with_max_type_nodes — .../execution/context.rs — senders calling any public/entry fn of any package, incl. publisher-controlled bytecode
- Context::publish_and_init_package / Context::upgrade -> MoveRuntime::validate_package — .../execution/context.rs — untrusted publishers (module bytes, dep ids, type origin/linkage from deps); upgrade needs UpgradeTicket
- MoveRuntime::make_vm / make_vm_with_native_extensions (load_and_cache_vtables) — move-vm-runtime/src/runtime/mod.rs — any tx whose linkage names on-chain packages; loads, verifies, JITs into process-wide caches
- MoveRuntime::resolve_and_cache_package — move-vm-runtime/src/runtime/mod.rs — adapter package lookups during linkage analysis and type resolution
- eval::call_native_with_args — move-vm-runtime/src/execution/interpreter/eval.rs — Move code reaching natives (dynamic_field, transfer, event, crypto, string, vector)
- execute_genesis_state_update / advance_epoch / process_system_packages — sui-adapter/src/execution_engine.rs — validators/consensus only
- Executor::type_layout_resolver (TypeLayoutResolver::get_annotated_layout) — sui-adapter/src/type_layout_resolver.rs — node internals and RPC for arbitrary on-chain types

## Persistent / shared state
- MoveCache.package_cache (DashMap<VersionId, Arc<Package{verified, runtime}>>), process-wide, never evicted — cache/move_cache.rs — add_package_to_cache, package_resolution::jit_and_cache_package, resolve_packages
- MoveCache.linkage_vtables (quick_cache LRU<LinkageHash, VMDispatchTables>) — cache/move_cache.rs — add_linkage_tables_to_cache, drop_all_cached_linkage_tables, MoveRuntime::load_and_cache_vtables, make_vm_with_native_extensions
- MoveCache.system_packages (pinned Arc<Package>, targets of direct-call pointers) — cache/move_cache.rs — add_system_package, MoveRuntime::install_system_packages
- IdentifierInterner (global lasso ThreadedRodeo with memory limit) — cache/identifier_interner.rs — intern_identifier, intern_ident_str, jit translate::{package, make_arena_type_impl, call}
- Package arenas (bumpalo, bounded by package_arena_size) holding Functions/types/constants/bytecode via VMPointer — cache/arena.rs — ArenaBuilder::{alloc_vec, alloc_box}, translate::{preallocate_functions, function_bodies, constants}
- VMDispatchTables.type_depths (per-VM QCache of DepthFormula) — execution/dispatch_tables.rs — calculate_depth_of_datatype_and_cache
- TelemetryContext (process-wide counters) — runtime/telemetry.rs
- MachineState call stack, operand stack, MachineHeap (per call) — execution/interpreter/state.rs — CallStack::{push_call, pop_frame}, ValueStack::push, MachineHeap::{allocate_stack_frame, free_stack_frame}
- Context.executable_vm_cache (per-tx LinkageHash -> MoveVM) — sui-adapter/.../execution/context.rs — with_vm!, Context::vm_move_call
- TransactionPackageStore.new_packages / package_cache (per-tx published packages visible to VM ModuleResolver) — sui-adapter/src/data_store/transaction_package_store.rs — push_package, pop_package, fetch_move_package
- ObjectRuntime state (new_ids, deleted_ids, transfers, events, child object store + fingerprints, root_version, wrapped_object_containers, accumulator events) — sui-move-natives/src/object_runtime/mod.rs
- BaseHeap (PTB argument/result locations referenced by VM refs) — execution/interpreter/locals.rs — BaseHeap::{allocate_value, take_loc}, Locals::store (adapter execution/values.rs)
- GasStatus / GasCharger — sui-adapter/src/gas_meter.rs, gas_charger.rs
- TemporaryStore (written, deleted, events, loaded runtime objects) — sui-adapter/src/temporary_store.rs

## Modules
- sui-execution glue (sui-execution/src/lib.rs, latest.rs): picks executor/verifier by execution_version (4 = latest); Arc<MoveRuntime> with all_natives
- runtime/: MoveRuntime — resolve+cache packages, build VMDispatchTables per LinkageContext (make_vm), validate packages for publish, install pinned system packages
- cache/: MoveCache (DashMap packages, LRU vtables, pinned system pkgs), bump arena (unsafe), global string interner (unsafe new_unchecked, panics on OOM)
- validation/: deserialize SerializedPackage (name/address/dup checks), unmetered bytecode verifier, native resolution, cross-package linkage, cycles, linkage-context consistency
- jit/: verified modules -> arena runtime AST; basic blocks/renumbering; datatype descriptors with defining ids from type_origin_table; direct VMPointer calls (same pkg / pinned system pkg) vs virtual vtable keys
- execution/dispatch_tables.rs: per-linkage function/type resolution, TypeTag->Type via defining_id_origins, abilities, ty-arg constraints, depth formulas, type->tag/layout
- execution/interpreter/: main loop, per-op gas, call/return, native dispatch, stack limits, Rc<RefCell> locals, generic instantiation with type-node limits, depth checks
- execution/values/values_impl.rs: value model, refs, containers, vectors, equality, BCS (de)serialize, constants into arena, GlobalValue
- execution/vm.rs: MoveVM API (execute_function_bypass_visibility(_with_max_type_nodes), find_function, load_type, layouts)
- natives/: native table, NativeContext, extensions, move-stdlib natives (string uses unsafe from_utf8_unchecked)
- shared/: VMPointer (unsafe deref, Send+Sync), LinkageContext/LinkageHash, safe_ops, gas meter trait, BinaryCache
- sui-adapter static_programmable_transactions: linkage analysis, loading, typing + verify (input args, visibility, private generics, memory safety, drop safety, private entry args), translation metering, execution Context
- sui-adapter execution_engine.rs: tx kinds, gas smashing/charging, conservation & ownership checks, effects
- sui-adapter data_store: CachedPackageStore over TransactionPackageStore (backing store + in-tx new package stack)
- sui-adapter gas_meter.rs / gas_charger.rs: SuiGasMeter over GasStatus; budget, smashing, rebates, publish charges
- sui-adapter temporary_store.rs: per-tx object store, conservation/ownership/published-package checks
- sui-adapter adapter.rs: VMConfig from ProtocolConfig, native extensions, substitute_package_id, metered verification for signing
- sui-move-natives: object runtime, dynamic_field, transfer, object, event, tx_context, crypto, scratch runtime, config
- sui-verifier: key-struct rules, no global storage, id leak, private generics v1/v2, entry/init rules, tx_context rules, OTW, SuiVerifierMeter
