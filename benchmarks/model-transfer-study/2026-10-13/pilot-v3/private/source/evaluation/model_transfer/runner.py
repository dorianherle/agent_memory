"""Structured agent loop with metered Codex completions and isolated tools.

Codex emits decisions only. Study tools execute in a separate, offline
container. This makes the 40-call limit enforceable before a tool executes.
It is an evaluation agent loop, not a measurement of native IDE agents.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

DECISION_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["read", "write", "list", "run", "memory", "finish"]},
    "arguments_json": {"type": "string"}}, "required": ["action", "arguments_json"], "additionalProperties": False}
IMAGE = "agent-memory-study:2026-10-13"
NATIVE_TOOLS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "plan_update", "collab_tool_call"}


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n")


def append_jsonl(path, value):
    with Path(path).open("a") as f:
        f.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
        f.flush()


def decode_completion(stdout, exit_code):
    events, errors, usage, messages, native = [], [], [], [], set()
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            events.append(event)
        except json.JSONDecodeError:
            errors.append("Non-JSON stdout event")
            continue
        kind = event.get("type")
        item = event.get("item") or {}
        if item.get("type") in NATIVE_TOOLS:
            native.add(item.get("id", str(len(events))))
        if kind in ("error", "turn.failed") or item.get("type") == "error":
            errors.append(event.get("message") or item.get("message") or str(event.get("error")))
        if kind == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
        if kind == "turn.completed":
            u = event.get("usage") or {}
            if any(type(u.get(k)) is not int or u[k] < 0 for k in ("input_tokens", "output_tokens")):
                errors.append("Invalid or missing native usage")
                continue
            cached = u.get("cached_input_tokens")
            reasoning = u.get("reasoning_output_tokens")
            if cached is not None and (type(cached) is not int or not 0 <= cached <= u["input_tokens"]):
                errors.append("Invalid cached token count")
                continue
            if reasoning is not None and (type(reasoning) is not int or not 0 <= reasoning <= u["output_tokens"]):
                errors.append("Invalid reasoning token count")
                continue
            usage.append({"input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
                          "cached_input_tokens": cached, "reasoning_output_tokens": reasoning})
    if native:
        errors.append("Native tools are forbidden in the model-only completion process")
    if exit_code != 0:
        errors.append(f"Completion exited {exit_code}")
    if not usage:
        errors.append("No verified native token count")
    decision = None
    if messages:
        try:
            decision = json.loads(messages[-1])
            if set(decision) != {"action", "arguments_json"} or decision["action"] not in DECISION_SCHEMA["properties"]["action"]["enum"]:
                raise ValueError("Unexpected decision fields")
            arguments = json.loads(decision["arguments_json"])
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            decision = {"action": decision["action"], "arguments": arguments}
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f"Invalid decision: {error}")
    else:
        errors.append("No final decision")
    totals = {"input_tokens": sum(u["input_tokens"] for u in usage),
              "output_tokens": sum(u["output_tokens"] for u in usage),
              "cached_input_tokens": sum(u["cached_input_tokens"] for u in usage) if usage and all(u["cached_input_tokens"] is not None for u in usage) else None,
              "reasoning_output_tokens": sum(u["reasoning_output_tokens"] for u in usage) if usage and all(u["reasoning_output_tokens"] is not None for u in usage) else None}
    return {"ok": not errors, "errors": errors, "decision": decision, "usage": totals,
            "usage_complete": bool(usage) and exit_code == 0 and not any("usage" in e.lower() for e in errors),
            "native_tool_calls": len(native), "events": len(events)}


@dataclass(frozen=True)
class Settings:
    model: str = "gpt-6.1-sol"
    reasoning: str = "high"
    seconds: int = 600
    tool_calls: int = 40
    image: str = IMAGE


class CodexDecisions:
    def __init__(self, settings, auth_path=None, *, container=True):
        self.settings = settings
        self.auth_path = Path(auth_path or Path.home() / ".codex/auth.json").resolve()
        self.container = container

    def complete(self, prompt, output, timeout):
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
        (output / "prompt.txt").write_text(prompt)
        write_json(output / "schema.json", DECISION_SCHEMA)
        env = os.environ.copy()
        with tempfile.TemporaryDirectory(prefix="memory-study-model-") as scratch:
            home = Path(scratch)
            (home / "empty").mkdir()
            shutil.copyfile(self.auth_path, home / "auth.json")
            (home / "auth.json").chmod(0o600)
            name = "memory-study-model-" + uuid.uuid4().hex[:16]
            if self.container:
                command = ["docker", "run", "--rm", "-i", "--name", name, "--cap-drop=ALL",
                           "--security-opt=no-new-privileges", "--pids-limit=128",
                           "--mount", f"type=bind,src={home},dst=/root/.codex",
                           "--mount", f"type=bind,src={output / 'schema.json'},dst=/schema.json,readonly",
                           self.settings.image]
                schema, cwd = "/schema.json", "/agent"
            else:
                command = []
                env["CODEX_HOME"] = str(home)
                schema, cwd = str(output / "schema.json"), str(home / "empty")
            command += ["codex", "exec", "--json", "--ephemeral", "--ignore-user-config",
                        "--skip-git-repo-check", "--sandbox", "read-only", "--model", self.settings.model,
                        "-c", f'model_reasoning_effort="{self.settings.reasoning}"', "--output-schema", schema, "--cd", cwd, "-"]
            # Remove tool definitions as well as prohibiting their use in text.
            for feature in ("shell_tool", "multi_agent", "apps", "memories", "goals", "hooks", "remote_plugin"):
                command += ["--disable", feature]
            command += ["-c", 'web_search="disabled"']
            start = time.monotonic()
            timed_out = False
            with (output / "events.jsonl").open("w") as stdout, (output / "stderr.log").open("w") as stderr:
                proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, env=env, text=True)
                try:
                    proc.communicate(input=prompt, timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    proc.kill()
                    proc.wait()
                finally:
                    if self.container:
                        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
            result = decode_completion((output / "events.jsonl").read_text(), proc.returncode)
            result.update(elapsed_seconds=time.monotonic() - start, timed_out=timed_out,
                          requested_model=self.settings.model, reasoning=self.settings.reasoning)
            write_json(output / "completion.json", result)
            return result


class DockerTools:
    def __init__(self, workspace, arm, image=IMAGE):
        self.workspace, self.arm, self.image = Path(workspace).resolve(), arm, image
        self.name = "memory-study-tools-" + uuid.uuid4().hex[:16]

    def __enter__(self):
        command = ["docker", "run", "--rm", "-d", "--name", self.name, "--network=none",
                   "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--security-opt=no-new-privileges", "--pids-limit=128",
                   "--mount", f"type=bind,src={self.workspace},dst=/workspace", "-e", "STUDY_CONTAINER=1", self.image, "sleep", "infinity"]
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        return self

    def __exit__(self, *_):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=15)

    def raw(self, command, *, text=None, timeout=35):
        return subprocess.run(["docker", "exec", "-i", self.name, *command], input=text,
                              capture_output=True, text=True, timeout=timeout)

    def execute(self, action, arguments=None, timeout=35):
        payload = json.dumps({"arm": self.arm, "action": action, "arguments": arguments or {}})
        p = self.raw(["python", "/opt/backend/worker.py"], text=payload, timeout=timeout)
        if p.returncode:
            return {"ok": False, "error": p.stderr, "exit_code": p.returncode}
        return json.loads(p.stdout)


TOOL_INSTRUCTIONS = """You are an agent in a controlled work study. Use only the study tool protocol.
Do not call any native Codex tool. The completion process has no project files.
Return one JSON object: action plus arguments_json (a JSON-encoded object).
Actions:
read {path}; write {path,content}; list {path}; run {command:[program,arg,...]};
memory {action:save|find|history|open_questions|delete,text?,query?,ids?,payload?};
finish {}. Commands run offline in the project container, with Python and Git.
Use current files and local tests. Complete work.py and report.json. Save durable
findings and unresolved questions before finish. You may store concise reusable
rules in ordinary notes. Do not invent observations. Tool errors can be repaired.
Save source-bound findings with payload {files:[relative_source_paths]} when useful.
The history below contains every previous study tool call and its full result.
"""
MODEL_INSTRUCTIONS = """
This method requires model transfer. At the start, call model_get. Before finish,
create or revise an accepted task model from actual local observations. A note
alone is not a model handoff. Preserve prior cases and meaningful conditions.
Call model_check before accept. Call model_reduce on the tested active model;
if it proposes a passing revision, accept it. Also call model_predict for a
recorded case. Do not require size reduction when new evidence needs more rules.
Executable model memory also supports actions model_get, model_propose,
model_check, model_accept, model_reduce, model_predict. Propose arguments:
{action:model_propose,id:active_model_id_or_null,payload:model}. Accept/check:
{action:model_accept_or_model_check,id:proposal_id}. Predict:
{action:model_predict,name:target,payload:inputs}. Get {action:model_get,full:false}.
Model schema: {schema:1,title,inputs:{name:type},rules:[{id,target,claim,when,then,
evidence:[saved_note_ids],dependencies:[relative_files]}],cases:[{id,target,inputs,
expected,evidence:[saved_note_ids],challenge:bool}],alternatives:[],unresolved:[]}.
Expressions: {value:literal}, {var:input_or_nested_field}, {op:operator,args:[expressions]}.
Operators eq,ne,lt,le,gt,ge,in,not,and,or,len,add,sub,if; collection all/any:
{op:all_or_any,items:expression,as:alias,test:expression}. Input types string,
number,integer,boolean,array,object. Every target needs a challenge observation.
All saved cases must pass before accept. Changed dependencies block old rules;
new source evidence needs a new challenge. Reduction creates a proposal and
never accepts it. The next agent receives the working model first. Build and
revise models for handoff; retain raw evidence, limits, and important conditions.
For comparable unseen-case probes, use inputs request:object, policy:object, and
evidence:array for research when possible. Useful targets are ok, needs_review,
or status. Other input schemes are permitted and will be reported as unprobed.
"""


def run_stage(provider, tools, task_text, packet, output, settings):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    start, history, count, completions = time.monotonic(), [], 0, []
    stop = None
    while True:
        remaining = settings.seconds - (time.monotonic() - start)
        if remaining <= 0:
            stop = "stage_timeout"
            break
        prompt = TOOL_INSTRUCTIONS + (MODEL_INSTRUCTIONS if tools.arm == "models" else "")
        prompt += f"\nCurrent task:\n{task_text}\nInitial memory (maximum 1200 characters):\n{packet[:1200]}\n"
        prompt += f"Tool calls used: {count}/{settings.tool_calls}.\nHistory:\n" + json.dumps(history, ensure_ascii=True)
        c = provider.complete(prompt, output / f"completion-{len(completions):03d}", remaining)
        completions.append(c)
        append_jsonl(output / "run.jsonl", {"type": "completion", "number": len(completions), **c})
        if not c["usage_complete"]:
            stop = "metering_or_access_failure"
            break
        if not c["ok"]:
            stop = "completion_protocol_failure"
            break
        decision = c["decision"]
        if decision["action"] == "finish":
            if tools.arm == "models":
                snapshot = tools.execute("_snapshot", {}, timeout=max(0.1, min(35, remaining)))
                state = snapshot.get("result", {}) if snapshot.get("ok") else {}
                valid = isinstance(state, dict) and state.get("model") and state.get("checks", {}).get("status") == "tested"
                if not valid:
                    event = {"type": "handoff_guard", "result": snapshot,
                             "instruction": "An accepted, currently tested model is required for this handoff. Save actual evidence, propose, check, and accept. Retain earlier cases."}
                    history.append(event)
                    append_jsonl(output / "run.jsonl", event)
                    if count == settings.tool_calls:
                        stop = "model_handoff_failure"
                        break
                    continue
            stop = "finished"
            break
        if count == settings.tool_calls:
            stop = "tool_limit"
            break
        count += 1
        try:
            result = tools.execute(decision["action"], decision["arguments"], timeout=max(0.1, min(35, settings.seconds - (time.monotonic() - start))))
        except Exception as error:
            result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        event = {"type": "tool", "number": count, "action": decision["action"], "arguments": decision["arguments"], "result": result}
        history.append(event)
        append_jsonl(output / "run.jsonl", event)
    known = all(c["usage_complete"] for c in completions) and bool(completions)
    result = {"stop": stop, "tool_calls": count, "elapsed_seconds": time.monotonic() - start,
              "usage_complete": known, "tokens": sum(c["usage"]["input_tokens"] + c["usage"]["output_tokens"] for c in completions) if known else None,
              "known_tokens": sum(c["usage"]["input_tokens"] + c["usage"]["output_tokens"] for c in completions),
              "input_tokens": sum(c["usage"]["input_tokens"] for c in completions),
              "output_tokens": sum(c["usage"]["output_tokens"] for c in completions),
              "cached_input_tokens": sum(c["usage"]["cached_input_tokens"] for c in completions) if known and all(c["usage"]["cached_input_tokens"] is not None for c in completions) else None,
              "reasoning_output_tokens": sum(c["usage"]["reasoning_output_tokens"] for c in completions) if known and all(c["usage"]["reasoning_output_tokens"] is not None for c in completions) else None}
    write_json(output / "stage.json", result)
    return result
