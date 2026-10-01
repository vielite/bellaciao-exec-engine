#!/usr/bin/env python3
"""Build the 12 GLM agent bundles, launch them in parallel, and grade the raw output.

Subcommands:
  build   Assemble system-prompt bundles + task prompts from references/ into <run>/.
  launch  Run all agents in parallel (subprocess) against glm_agent.py.
  grade   Machine-check every FINDING/LEAD (path exists? quote verbatim?) and emit a
          grading scaffold the orchestrator (Claude) fills in with gate verdicts.
  run     build + launch (then the orchestrator reads outputs and runs `grade`).

Design goals: pure stdlib, restartable (skips agents whose output exists unless --force),
and everything needed to judge GLM lands on disk. The orchestrator does the human-judgment
gates; this script does the mechanical, cheap, deterministic parts.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, "references")
AGENT = os.path.join(HERE, "glm_agent.py")

# agent number -> specialty file basename (in references/hacking-agents/)
SPECIALTIES = {
    1: "01-memory-safety-agent.md", 2: "02-determinism-agent.md",
    3: "03-type-confusion-agent.md", 4: "04-metering-agent.md",
    5: "05-verifier-gap-agent.md", 6: "06-interpreter-agent.md",
    7: "07-object-runtime-agent.md", 8: "08-ptb-typing-agent.md",
    9: "09-natives-crypto-agent.md", 10: "10-boundary-agent.md",
    11: "11-cache-poisoning-agent.md", 12: "12-execution-engine-agent.md",
}
GAP_HUNTERS = {11, 12}

TASK_SINGLE = """You are an attacker auditing the Sui "bella-ciao" Move VM. Your specialty, \
mindset, target, and output rules are all in your system prompt. Re-read them before you start.

You have read-only tools over the repository ({repo}): list_dir, find_files, grep, read_file. \
The source is NOT in your prompt — you must read it with the tools. Start from the seed files \
listed in your specialty section, then follow the code.

Work the target: apply the Feynman / Socratic / Inversion protocol continuously (emit the \
markers in your reasoning), weaponize any pattern you find across the whole scope, and trace \
every candidate to a concrete attacker input before calling it a FINDING.

Your final message must contain ONLY FINDING and LEAD blocks in the exact format from the \
shared rules, each with a real `location:` and a verbatim `quote:` from code you actually read. \
If you found nothing, output NO_FINDINGS and one line on what you covered."""

TASK_GAP = TASK_SINGLE + """

You are a GAP-HUNTER: report only bugs that live at the SEAM of the lenses named in your \
specialty and REQUIRE their interaction. Drop anything a single-lens agent would already catch."""


def build_bundle(n):
    """System prompt = target + orientation + SOP + specialty + shared rules."""
    parts = []
    for path in [os.path.join(REF, "target.md")]:
        parts.append(open(path).read())
    orient = os.path.join(SKILL, "..", "..", "..", "hackenProof", "sui", "audit-context", "ORIENTATION.md")
    # orientation lives in the repo, not the skill; include if present
    for cand in [os.environ.get("BELLA_ORIENTATION", ""),
                 os.path.join(os.getcwd(), "audit-context", "ORIENTATION.md"),
                 os.path.join(os.getcwd(), "sui", "audit-context", "ORIENTATION.md")]:
        if cand and os.path.exists(cand):
            parts.append("# Orientation map\n\n" + open(cand).read())
            break
    parts.append(open(os.path.join(REF, "senior-auditor-sop.md")).read())
    parts.append(open(os.path.join(REF, "hacking-agents", SPECIALTIES[n])).read())
    parts.append(open(os.path.join(REF, "shared-rules.md")).read())
    return "\n\n---\n\n".join(parts)


def cmd_build(a):
    os.makedirs(a.run, exist_ok=True)
    for n in SPECIALTIES:
        with open(os.path.join(a.run, f"agent-{n}.system.md"), "w") as f:
            f.write(build_bundle(n))
        task = (TASK_GAP if n in GAP_HUNTERS else TASK_SINGLE).format(repo=os.path.abspath(a.repo))
        with open(os.path.join(a.run, f"agent-{n}.task.md"), "w") as f:
            f.write(task)
    print(f"built 12 bundles in {a.run}")
    for n in SPECIALTIES:
        sp = os.path.join(a.run, f"agent-{n}.system.md")
        print(f"  agent-{n} ({SPECIALTIES[n][3:-3]:22}) system={sum(1 for _ in open(sp))} lines")


def run_one(a, n):
    out = os.path.join(a.run, f"agent-{n}.output.md")
    if os.path.exists(out) and not a.force:
        return n, "skipped (exists)"
    cmd = [sys.executable, AGENT,
           "--repo", os.path.abspath(a.repo),
           "--system", os.path.join(a.run, f"agent-{n}.system.md"),
           "--task", os.path.join(a.run, f"agent-{n}.task.md"),
           "--out", a.run, "--name", f"agent-{n}",
           "--model", a.model, "--max-turns", str(a.max_turns)]
    log = open(os.path.join(a.run, f"agent-{n}.log"), "w")
    t0 = time.time()
    rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT)
    return n, f"rc={rc} {time.time() - t0:.0f}s"


def cmd_launch(a):
    if not os.path.exists(os.path.join(a.run, "agent-1.system.md")):
        sys.exit("no bundles — run `build` first")
    nums = a.only or list(SPECIALTIES)
    print(f"launching agents {nums} with model={a.model}, parallelism={a.jobs}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_one, a, n): n for n in nums}
        for fut in concurrent.futures.as_completed(futs):
            n, msg = fut.result()
            print(f"  agent-{n}: {msg}", flush=True)
    print("all agents done")


# ---------------------------------------------------------------- grading

BLOCK_RE = re.compile(r"^(FINDING|LEAD)\s*\|(.+)$", re.M)
FIELD_RE = re.compile(r"^\s*([a-z_]+):\s*(.*)$", re.M)


def parse_blocks(text):
    """Split output into FINDING/LEAD blocks with their fields."""
    blocks = []
    matches = list(BLOCK_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        header = dict(re.findall(r"([a-z_]+):\s*([^|]+?)(?:\s*\||$)", m.group(2)))
        fields = {k: v.strip() for k, v in FIELD_RE.findall(body)}
        fields.update({k.strip(): v.strip() for k, v in header.items()})
        blocks.append({"kind": m.group(1), "raw_header": m.group(2).strip(), "fields": fields})
    return blocks


def check_citation(repo, fields):
    """Mechanical grounding check: does location exist and is quote verbatim?"""
    loc = fields.get("location", "")
    quote = fields.get("quote", "").strip().strip("`")
    result = {"location": loc, "path_ok": False, "line_ok": None,
              "quote_ok": None, "quote_elsewhere": None, "note": ""}
    m = re.match(r"([^\s:]+):L?(\d+)(?:-L?(\d+))?", loc)
    if not m:
        result["note"] = "unparseable location"
        return result
    path, s = m.group(1), int(m.group(2))
    e = int(m.group(3)) if m.group(3) else s
    full = os.path.join(repo, path)
    if not os.path.exists(full):
        result["note"] = "path does not exist"
        return result
    result["path_ok"] = True
    lines = open(full, errors="replace").read().splitlines()
    result["line_ok"] = 1 <= s <= len(lines) and e <= len(lines)
    if quote:
        window = "\n".join(lines[max(0, s - 3):min(len(lines), e + 3)])
        norm = lambda x: re.sub(r"\s+", " ", x).strip()
        result["quote_ok"] = norm(quote) in norm(window)
        if not result["quote_ok"]:
            whole = norm("\n".join(lines))
            result["quote_elsewhere"] = norm(quote) in whole
    if not result["line_ok"]:
        result["note"] = "line range beyond EOF"
    elif result["quote_ok"] is False and result["quote_elsewhere"]:
        result["note"] = "quote real but outside cited lines (misattributed)"
    elif result["quote_ok"] is False:
        result["note"] = "quote not found in file (possible hallucination)"
    return result


def cmd_grade(a):
    rows, summary = [], {"agents": 0, "findings": 0, "leads": 0,
                         "citation_pass": 0, "citation_fail": 0, "no_quote": 0}
    for n in SPECIALTIES:
        out = os.path.join(a.run, f"agent-{n}.output.md")
        stats_p = os.path.join(a.run, f"agent-{n}.stats.json")
        if not os.path.exists(out):
            continue
        summary["agents"] += 1
        stats = json.load(open(stats_p)) if os.path.exists(stats_p) else {}
        blocks = parse_blocks(open(out).read())
        for b in blocks:
            f = b["fields"]
            cite = check_citation(os.path.abspath(a.repo), f)
            summary["findings" if b["kind"] == "FINDING" else "leads"] += 1
            if not f.get("quote"):
                summary["no_quote"] += 1
            elif cite["quote_ok"]:
                summary["citation_pass"] += 1
            elif cite["quote_ok"] is False:
                summary["citation_fail"] += 1
            rows.append({
                "agent": n, "kind": b["kind"],
                "crate": f.get("crate", ""), "function": f.get("function", ""),
                "bug_class": f.get("bug_class", ""), "group_key": f.get("group_key", ""),
                "impact": f.get("impact", ""), "severity_claim": f.get("severity_claim", ""),
                "location": f.get("location", ""), "citation": cite,
                "description": f.get("description", ""),
                # to be filled by the orchestrator during judging:
                "grade": None, "gate_failed": None, "final_severity": None, "notes": None,
            })
    with open(os.path.join(a.run, "graded.json"), "w") as fh:
        json.dump({"summary": summary, "findings": rows}, fh, indent=2)

    # human-readable scaffold
    lines = ["# GLM raw output — mechanical grade\n",
             f"agents with output: {summary['agents']}/12 · "
             f"raw FINDINGs: {summary['findings']} · LEADs: {summary['leads']}",
             f"citation: {summary['citation_pass']} verbatim-ok · "
             f"{summary['citation_fail']} quote-mismatch · {summary['no_quote']} no-quote\n",
             "| # | agent | kind | crate | function | bug_class | impact | sev | cite | location |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        c = r["citation"]
        badge = ("✅" if c["quote_ok"] else "❌" if c["quote_ok"] is False
                 else "⚠️path" if not c["path_ok"] else "—")
        lines.append(f"| {i} | {r['agent']} | {r['kind']} | {r['crate']} | "
                     f"`{r['function']}` | {r['bug_class']} | {r['impact']} | "
                     f"{r['severity_claim']} | {badge} | `{r['location']}` |")
    lines.append("\n## Citation problems (verify these first)\n")
    for i, r in enumerate(rows, 1):
        if r["citation"]["note"]:
            lines.append(f"- #{i} agent-{r['agent']} `{r['location']}` — {r['citation']['note']}")
    scaffold = os.path.join(a.run, "GRADING.md")
    open(scaffold, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {scaffold} and graded.json")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "launch", "grade", "run"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True, help="run directory (holds bundles + outputs)")
        p.add_argument("--repo", default=".", help="repo root the agents read")
        p.add_argument("--model", default="glm", help="glm | glm-flash | NIM id")
        p.add_argument("--max-turns", type=int, default=60)
        p.add_argument("--jobs", type=int, default=6, help="parallel agents (launch/run)")
        p.add_argument("--only", type=int, nargs="*", help="agent numbers to run")
        p.add_argument("--force", action="store_true", help="rerun agents even if output exists")
    a = ap.parse_args()
    if a.cmd == "build":
        cmd_build(a)
    elif a.cmd == "launch":
        cmd_launch(a)
    elif a.cmd == "grade":
        cmd_grade(a)
    elif a.cmd == "run":
        cmd_build(a)
        cmd_launch(a)
        print("\nagents finished — orchestrator should now read outputs and run `grade`.")


if __name__ == "__main__":
    main()
