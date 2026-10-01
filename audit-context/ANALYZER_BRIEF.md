# Brief for per-function analyzers

Repo root: /home/vielite/hackenProof/sui (branch main, commit fd2e2a0fc6). File paths in your task are repo-relative.
System context (actors, entrypoints, shared state, modules): /home/vielite/hackenProof/sui/audit-context/ORIENTATION.md — read it first.

Security setting, for orientation only: Sui validators all execute the same transactions with the new Move VM; transaction senders and package publishers (arbitrary bytecode) are untrusted. What matters system-wide is determinism across validators, type/ability/ownership identity of objects and coins, bounded resource use, and memory safety of `unsafe` code.

## Instructions

Write the full prose analysis to the output file named in your task using the Write tool. That file is the deliverable and it should be thorough. Then return only the structured record — it is a compact index, not a summary of the prose.

Follow every call this function makes. When the callee's source is available, read it, and record in 'propagates' what the caller depends on the callee to establish. If a precondition the caller relies on is only established inside a callee, or only on some of the callee's paths, that belongs in 'assumptions' with 'establishedBy' naming exactly where. If nothing establishes it, say "nothing found". When source is not available, treat the callee as adversarial and record what is assumed about it.

Cite line numbers (file:Lnn) for every claim. Where you cannot, do not assert it — put it in openQuestions as "unclear; need to inspect X". Length is not a goal: a short record with cited claims beats a long one with padded ones.

This is context building, not vulnerability hunting. Describe structure, invariants, and assumptions. Do not name vulnerabilities, propose fixes, write exploits, or assign severity. An unenforced assumption is recorded as an unenforced assumption, not as a bug.

## Return format

Return ONLY a JSON object, no surrounding prose, strings one line each:

{"function": "...", "file": "...", "lines": "L40-L88", "analysisFile": "<absolute path written>",
 "invariants": [{"claim": "...", "evidence": "file:Lnn"}],
 "assumptions": [{"claim": "...", "establishedBy": "fn at file:Lnn | nothing found"}],
 "calls": [{"callee": "...", "kind": "internal|external-source-available|external-black-box", "propagates": "..."}],
 "callers": ["..."], "sharedState": ["..."],
 "openQuestions": ["unclear; need to inspect X"]}
