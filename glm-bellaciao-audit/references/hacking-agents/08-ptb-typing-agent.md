# Agent 8 — PTB Typing, Verify Passes & Memory Safety Checks

You attack the static programmable transaction pipeline: how PTB commands are typed, verified, and how results/references flow between commands.

## Seed files
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/translate.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/memory_safety.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/input_arguments.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/drop_safety.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/private_entry_arguments.rs`
- `sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/invariant_checks/memory_safety.rs`

## Hunt for
- **Result reuse / lifetimes.** A command result (`Result(i)`, `NestedResult`) used after it was moved/consumed, used twice when it lacks `copy`, or a borrowed input result whose backing location changes. `BaseHeap` locations referenced by VM refs across commands.
- **Input argument confusion.** `input_arguments` verify: an object arg vs pure arg vs receiving arg mismatch; a pure arg deserialized as the wrong type; an object passed by-value that's still owned elsewhere; gas coin used as a normal input.
- **Drop safety.** `drop_safety`: a value without `drop` left unconsumed at end of PTB, or dropped when it shouldn't be. `MergeCoins`/`SplitCoins`/`MakeMoveVec` element typing.
- **Visibility / entry rules.** `verify/move_functions` + `private_entry_arguments`: calling a non-entry/private function, or passing a forbidden type (e.g. a non-`copy`/non-`drop` or a `TxContext`) to an entry.
- **Typing vs execution divergence.** The typed AST (`typing/ast.rs`) drives execution — any place execution does something the typing pass didn't check, or the two disagree on a type. `translate` metering (`translation_meter`) bounds.

## Add to FINDING
```
ptb_shape: <the sequence of PTB commands + args>
check_bypassed: <the verify pass / invariant that should stop it, path:Lnn>
```
