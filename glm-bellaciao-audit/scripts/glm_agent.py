#!/usr/bin/env python3
"""Agentic loop that lets a GLM model (NVIDIA NIM, OpenAI-compatible) hunt bugs in a repo.

The model gets read-only tools (list_dir, find_files, grep, read_file) sandboxed to --repo,
works until it stops calling tools (its final message is the report) or hits --max-turns.
Everything is recorded so the orchestrator can grade the model afterwards:

  <out>/agent-N.transcript.jsonl   every request/response/tool result
  <out>/agent-N.output.md          final report text (FINDING / LEAD blocks)
  <out>/agent-N.reasoning.md       concatenated reasoning_content
  <out>/agent-N.stats.json         turns, tool calls, tokens, latency, errors, markers

Pure stdlib. The API key comes from NVIDIA_NIM_API_KEY / NVIDIA_API_KEY, falling back to the
`export` line in ~/.bashrc (non-interactive shells skip .bashrc). The key is never printed.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODELS = {"glm": "z-ai/glm-5.3", "glm-flash": "z-ai/glm-5.3-flash"}
MAX_READ_LINES = 400
MAX_TOOL_CHARS = 40_000
SKIP_DIRS = {".git", "target", "node_modules", ".audit", "__pycache__"}
MARKER_RE = {
    "feynman": re.compile(r"\[Feynman:"),
    "socratic": re.compile(r"\[Socratic:"),
    "inversion": re.compile(r"\[Inversion:"),
}


def load_key():
    for var in ("NVIDIA_NIM_API_KEY", "NVIDIA_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        text = open(os.path.expanduser("~/.bashrc")).read()
    except OSError:
        text = ""
    m = re.search(r"^\s*export\s+NVIDIA(?:_NIM)?_API_KEY=[\"']?([^\"'\s]+)", text, re.M)
    if not m:
        sys.exit("no NVIDIA_NIM_API_KEY / NVIDIA_API_KEY in env or ~/.bashrc")
    return m.group(1)


# ---------------------------------------------------------------- tools

TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List entries of a directory (repo-relative path). Directories end with '/'.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "find_files",
        "description": "Recursively find files whose basename matches a glob (e.g. '*.rs', '*cache*').",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string", "default": "."}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Search file contents with an extended regex. Returns path:line:text, max 150 hits.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "include": {"type": "string", "description": "file glob filter, e.g. '*.rs'"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": f"Read a file with line numbers. At most {MAX_READ_LINES} lines per call; page with start_line.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "default": 1},
            "end_line": {"type": "integer"}},
            "required": ["path"]}}},
]


class Sandbox:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.files_read = {}  # path -> set of (start, end)

    def resolve(self, path):
        path = (path or ".").strip()
        if path.startswith(self.root):
            path = os.path.relpath(path, self.root)
        full = os.path.realpath(os.path.join(self.root, path.lstrip("/")))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError(f"path escapes repo root: {path}")
        return full

    def rel(self, full):
        return os.path.relpath(full, self.root)

    def list_dir(self, path="."):
        full = self.resolve(path)
        out = []
        for name in sorted(os.listdir(full)):
            if name in SKIP_DIRS:
                continue
            out.append(name + "/" if os.path.isdir(os.path.join(full, name)) else name)
        return "\n".join(out) or "(empty)"

    def find_files(self, pattern, path="."):
        full = self.resolve(path)
        hits = []
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                if fnmatch.fnmatch(f, pattern):
                    hits.append(self.rel(os.path.join(dirpath, f)))
                    if len(hits) >= 300:
                        return "\n".join(hits) + "\n... (truncated at 300)"
        return "\n".join(sorted(hits)) or "(no matches)"

    def grep(self, pattern, path=".", include=None):
        full = self.resolve(path)
        cmd = ["grep", "-rnIE", "--exclude-dir=target", "--exclude-dir=.git", "--exclude-dir=node_modules"]
        if include:
            cmd.append(f"--include={include}")
        cmd += ["-e", pattern, full]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return "grep timed out; narrow path or include"
        lines = [l.replace(self.root + "/", "", 1) for l in res.stdout.splitlines()]
        if res.returncode == 2 and not lines:
            return "grep error: " + res.stderr.strip()[:500]
        extra = f"\n... ({len(lines) - 150} more hits, narrow the search)" if len(lines) > 150 else ""
        return "\n".join(l[:300] for l in lines[:150]) + extra if lines else "(no matches)"

    def read_file(self, path, start_line=1, end_line=None):
        full = self.resolve(path)
        with open(full, errors="replace") as fh:
            lines = fh.readlines()
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line) if end_line else start + MAX_READ_LINES - 1)
        end = min(end, start + MAX_READ_LINES - 1)
        self.files_read.setdefault(self.rel(full), []).append([start, end])
        body = "".join(f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n[showing L{start}-L{end} of {len(lines)}]" if (start > 1 or end < len(lines)) else ""
        return body + more

    def run(self, name, args):
        fn = {"list_dir": self.list_dir, "find_files": self.find_files,
              "grep": self.grep, "read_file": self.read_file}.get(name)
        if fn is None:
            return f"unknown tool {name}"
        try:
            out = fn(**args)
        except TypeError as e:
            return f"bad arguments for {name}: {e}"
        except (OSError, ValueError) as e:
            return f"error: {e}"
        if len(out) > MAX_TOOL_CHARS:
            out = out[:MAX_TOOL_CHARS] + "\n... (output truncated; request a smaller range)"
        return out


# ---------------------------------------------------------------- API

def chat(key, model, messages, max_tokens, tools=True, retries=6):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.6}
    if tools:
        body["tools"] = TOOLS
    data = json.dumps(body).encode()
    delay = 15
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=data, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.load(r), time.time() - t0, None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read()[:300]!r}"
            if e.code not in (429, 500, 502, 503, 504):
                return None, time.time() - t0, err
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            err = f"{type(e).__name__}: {e}"
        if attempt < retries - 1:
            time.sleep(delay)
            delay = min(delay * 2, 240)
    return None, 0, err


def compact(messages, keep_recent=12):
    """Elide old tool outputs so the conversation fits the context window."""
    n = 0
    for i, m in enumerate(messages[:-keep_recent]):
        if m["role"] == "tool" and not m["content"].startswith("[elided"):
            m["content"] = "[elided to save context: " + m.get("_summary", "tool output") + " — call the tool again if you still need it]"
            n += 1
    return n


# ---------------------------------------------------------------- loop

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--system", required=True, help="file with the system prompt (bundle)")
    ap.add_argument("--task", required=True, help="file with the user task prompt")
    ap.add_argument("--out", required=True, help="output dir")
    ap.add_argument("--name", required=True, help="agent name, e.g. agent-3")
    ap.add_argument("--model", default="glm", help="glm | glm-flash | full NIM model id")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--compact-at", type=int, default=140_000, help="prompt tokens that trigger compaction")
    a = ap.parse_args()

    key = load_key()
    model = MODELS.get(a.model, a.model)
    box = Sandbox(a.repo)
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, a.name)
    tlog = open(base + ".transcript.jsonl", "w")
    reasoning_log = open(base + ".reasoning.md", "w")

    messages = [{"role": "system", "content": open(a.system).read()},
                {"role": "user", "content": open(a.task).read()}]
    stats = {"agent": a.name, "model": model, "turns": 0, "tool_calls": {}, "bad_tool_calls": 0,
             "prompt_tokens": 0, "completion_tokens": 0, "max_prompt_tokens": 0, "api_seconds": 0.0,
             "api_errors": [], "compactions": 0, "finished": "unknown", "started": time.time()}
    content_log = []
    final = ""

    def log(kind, obj):
        tlog.write(json.dumps({"t": round(time.time() - stats["started"], 1), "kind": kind, **obj}) + "\n")
        tlog.flush()

    for turn in range(1, a.max_turns + 1):
        stats["turns"] = turn
        left = a.max_turns - turn
        if left == 5:
            messages.append({"role": "user", "content":
                "You have 5 turns left. Stop exploring soon and emit your final FINDING/LEAD blocks."})
        last = left == 0
        if last:
            messages.append({"role": "user", "content":
                "Turn budget exhausted. Do not call tools. Emit your final FINDING and LEAD blocks now, "
                "following the output format exactly."})
        wire = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
        resp, secs, err = chat(key, model, wire, a.max_tokens, tools=not last)
        stats["api_seconds"] += secs
        if resp is None:
            stats["api_errors"].append(err)
            log("api_error", {"error": err})
            stats["finished"] = "api_error"
            break
        usage = resp.get("usage") or {}
        stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
        stats["completion_tokens"] += usage.get("completion_tokens", 0)
        stats["max_prompt_tokens"] = max(stats["max_prompt_tokens"], usage.get("prompt_tokens", 0))
        choice = resp["choices"][0]
        msg = choice["message"]
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        calls = msg.get("tool_calls") or []
        log("assistant", {"turn": turn, "secs": round(secs, 1), "usage": usage,
                          "finish_reason": choice.get("finish_reason"), "content": content,
                          "reasoning": reasoning, "tool_calls": calls})
        if reasoning:
            reasoning_log.write(f"\n\n## turn {turn}\n\n{reasoning}")
            reasoning_log.flush()
        if content:
            content_log.append(content)
        print(f"[{a.name}] turn {turn} {secs:.0f}s prompt={usage.get('prompt_tokens')} "
              f"tools={[c['function']['name'] for c in calls]}", file=sys.stderr, flush=True)

        assistant = {"role": "assistant", "content": content}
        if reasoning:
            assistant["reasoning_content"] = reasoning
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)

        if not calls:
            final = content
            stats["finished"] = "forced_final" if last else "model_stopped"
            if choice.get("finish_reason") == "length":
                stats["finished"] += "_truncated"
            break

        for c in calls:
            name = c["function"]["name"]
            stats["tool_calls"][name] = stats["tool_calls"].get(name, 0) + 1
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments not an object")
            except (json.JSONDecodeError, ValueError) as e:
                stats["bad_tool_calls"] += 1
                out, args = f"could not parse arguments as JSON object: {e}", {}
            else:
                out = box.run(name, args)
                if out.startswith(("bad arguments", "unknown tool", "error:")):
                    stats["bad_tool_calls"] += 1
            log("tool", {"turn": turn, "name": name, "args": args, "chars": len(out), "result": out[:2000]})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": out,
                             "_summary": f"{name}({json.dumps(args)[:160]})"})

        if usage.get("prompt_tokens", 0) > a.compact_at:
            stats["compactions"] += compact(messages)
    else:
        stats["finished"] = "turn_limit"

    if not final and content_log:
        final = content_log[-1]
    all_text = "\n".join(content_log) + "\n" + open(base + ".reasoning.md").read()
    stats["markers"] = {k: len(r.findall(all_text)) for k, r in MARKER_RE.items()}
    stats["markers_in_final"] = {k: len(r.findall(final)) for k, r in MARKER_RE.items()}
    stats["files_read"] = {p: len(r) for p, r in sorted(box.files_read.items())}
    stats["wall_seconds"] = round(time.time() - stats["started"], 1)
    stats["api_seconds"] = round(stats["api_seconds"], 1)
    del stats["started"]

    with open(base + ".output.md", "w") as fh:
        fh.write(final)
    with open(base + ".stats.json", "w") as fh:
        json.dump(stats, fh, indent=2)
    tlog.close()
    reasoning_log.close()
    print(f"[{a.name}] done: {stats['finished']} turns={stats['turns']} wall={stats['wall_seconds']}s",
          file=sys.stderr)


if __name__ == "__main__":
    main()
