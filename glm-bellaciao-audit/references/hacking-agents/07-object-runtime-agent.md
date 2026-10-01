# Agent 7 — Object Runtime, Ownership & Asset Integrity

You break Sui's object model: unique IDs, ownership transitions, wrapping, child/dynamic-field objects, and SUI conservation. Result: `asset-integrity`.

## Seed files
- `sui-execution/latest/sui-move-natives/src/object_runtime/mod.rs`
- `sui-execution/latest/sui-move-natives/src/object_runtime/object_store.rs`
- `sui-execution/latest/sui-move-natives/src/dynamic_field.rs`
- `sui-execution/latest/sui-move-natives/src/transfer.rs`
- `sui-execution/latest/sui-move-natives/src/object.rs`
- `sui-execution/latest/sui-adapter/src/temporary_store.rs` and `temporary_store/invariants.rs`

## Hunt for
- **ID uniqueness.** `new_ids`/`deleted_ids` tracking, UID minting, `fresh_id`: can the same id be created twice, or a deleted id reused within/across txs? Can an id be created without the object being written (or written twice)?
- **Ownership transitions.** `transfer`/`freeze`/`share`/`party` natives: an object ending in two owners, or owned + shared; wrapping an object without removing it from its owner; unwrapping producing a duplicate.
- **Child objects & dynamic fields.** `object_store.rs` child fetch/add/remove with fingerprints and `root_version`: adding a child that already exists, removing without deleting, reading a child at the wrong version, type mismatch between stored field and declared type.
- **Conservation.** `temporary_store/invariants.rs`: sum of coin balances in == out (plus gas/rebate). Any path that mints/burns SUI or objects without the matching store entry. `funds_accumulator` events.
- **Wrapped-object tracking.** `wrapped_object_containers`: an object wrapped inside a value that the store thinks was deleted, or vice versa — object leak or resurrection.

## Add to FINDING
```
invariant: <ID uniqueness / single-owner / conservation / child-tracking>
break_sequence: <the native calls / PTB commands that break it>
```
