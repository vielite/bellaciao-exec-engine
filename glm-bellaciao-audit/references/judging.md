# Finding Validation (orchestrator — Claude)

GLM wrote the findings; you verify them against the real code. Unlike a same-model audit, **you MUST read the cited code** — the point of this run is to measure whether GLM's claims hold. Budget: read only what the gate needs (the cited location, the guards on the claimed path, the callee that would interrupt it).

Every deduped FINDING passes four sequential gates. Fail a gate → **REJECTED** or **DEMOTED** to lead; later gates are not evaluated.

## Gate 0 — Grounding (machine + you)

Use the citation check from `swarm.py grade`:
- `location` path missing, or line range beyond EOF → **HALLUCINATED** (record, reject)
- `quote` not found anywhere in the cited file → read the location; if the described code isn't there either → **HALLUCINATED**; if it exists but was paraphrased → clears with note `paraphrased-quote`
- Quote found in a different file → clears with note `misattributed`

## Gate 1 — Attack execution

Trace the claimed path from the untrusted entrypoint to the harm. Read every check, verifier pass, limit, and `?`-propagated error on that path.
- A specific guard interrupts the exploit before harm (quote the line) → **REJECTED** (or **DEMOTED** if a related smell remains)
- The supposed interruption is speculative → clears

Common interrupters to check before accepting: Move bytecode verifier / Sui verifier passes run before JIT; `validate_package` / linkage checks; PTB typing + `verify/*` passes; protocol-config limits (type nodes, value depth, max vector len, arena size); gas charged before the operation; `PartialVMError` returned instead of panicking.

## Gate 2 — Reachability

- Structurally impossible (enforced invariant or verifier guarantee you located) → **REJECTED**
- Requires trusted actor (validator, governance, protocol config, framework package), no untrusted amplifier → **DEMOTE**
- Reachable by a tx sender, sponsor, publisher, or dev-inspect/dry-run RPC caller → clears

## Gate 3 — Trigger

- Needs a feature flag / protocol config not enabled on the latest config at this commit → **DEMOTE**
- An unprivileged actor triggers it with a normal tx/publish/RPC call → clears

## Gate 4 — Impact

- Only the attacker's own tx fails / pays gas → **REJECTED**
- Bounded mispricing, error-code-only difference that does not change effects, fullnode-only dev-inspect slowdown → **DEMOTE**
- Impact class from target.md with an identifiable victim (network, validators, other users' objects/coins) → **CONFIRMED**

A difference in *error code or abort location* between validators is still `consensus-split` if it lands in effects.

## Confidence

Start at **100**, deduct: partial path **-20**, impact bounded/non-compounding **-15**, needs specific achievable state (cold cache, particular upgrade history, concurrent tx) **-10**, relies on a callee you did not read **-10**. ≥80 gets fix; <80 description only.

## Lead promotion

- **Multi-agent convergence** — 2+ GLM agents flagged the same (file, function) and it was demoted (not rejected) → FINDING at 75.
- **Partial-path completion** — only weakness is an incomplete trace, but you confirmed the path is reachable and unguarded while verifying → FINDING at 75.

## GLM grading labels (record for every raw FINDING, before dedup merges them)

- `confirmed` — survived all gates
- `demoted` — real smell, exploit incomplete/privileged/bounded
- `rejected-guarded` — a guard GLM missed interrupts it (name the guard)
- `rejected-wrong` — misread semantics / not a bug
- `hallucinated` — nonexistent path, line, quote, or code behaviour

## Do not report

Test/dev_utils/tracing code. Trusted-actor-only behaviour. Style/lints. Gas micro-mispricing without amplification.
