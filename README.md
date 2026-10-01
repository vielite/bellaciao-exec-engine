# bellaciao-exec-engine

Audit harness for the Sui "bella-ciao" Move VM and execution engine.
Twelve GLM attacker agents scan the target in parallel; Claude orchestrates and grades.

This repo holds no Sui source. It points at a local Sui checkout.

## Layout

- `glm-bellaciao-audit/`: skill, `scripts/swarm.py`, `scripts/glm_agent.py`, and the 12 hacking-agent briefs
- `audit-context/`: orientation notes, function records, and task queue for the target

## Usage

Needs `NVIDIA_NIM_API_KEY` and a Sui checkout (default `/home/vielite/hackenProof/sui`).

```bash
export BELLA_ORIENTATION=$PWD/audit-context/ORIENTATION.md
RUN=$PWD/.glm-audit-$(date +%Y%m%d-%H%M%S)
python3 glm-bellaciao-audit/scripts/swarm.py build  --run $RUN --repo ../sui
python3 glm-bellaciao-audit/scripts/swarm.py launch --run $RUN --repo ../sui --jobs 4
python3 glm-bellaciao-audit/scripts/swarm.py grade  --run $RUN --repo ../sui
```

Run output (`.glm-audit-*/`) is gitignored.
