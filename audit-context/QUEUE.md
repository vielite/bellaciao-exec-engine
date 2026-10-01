# Analyzer queue (20-concurrent subagent limit)
Full prompts for all 30 tasks: TASKS.json (index i -> functions/NN-*.md).
Run 1 (2026-09-17) died on a rate limit; only 04's prose survived, no records.
Run 2 (2026-09-18): relaunched 01-20 (04 = record-only pass over existing prose).
Pending: 21-30 — launch as slots free.
Done = record appended to RECORDS.jsonl (one JSON object per line). A task is complete only when both its
functions/NN-*.md file and its RECORDS.jsonl line exist.
After all 30: synthesize DOSSIER.md (see workflow Synthesize phase).
