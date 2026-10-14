"""Worker-side tools. This file contains no task answers or private cases.

The controller runs this module as root. Shell commands run as UID 1000 in a
network-disabled worker container. The model runs in a different container.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ARMS = ("notebook", "notes", "models", "written")
MEMORY_ACTIONS = {"save", "find", "history", "open_questions", "delete"}
MODEL_ACTIONS = {"model_get", "model_propose", "model_check", "model_accept", "model_reduce", "model_predict", "model_handoff"}


def operation_budget(action, arguments):
    """Upper bound checked before execution; batches cannot evade the limit."""
    if action == "batch":
        calls = arguments.get("calls")
        if not isinstance(calls, list) or not 1 <= len(calls) <= 8:
            raise ValueError("batch requires one to eight calls")
        if any(not isinstance(c, dict) or c.get("action") not in {"read", "write", "list", "run", "memory"}
               or not isinstance(c.get("arguments"), dict) for c in calls):
            raise ValueError("Invalid or nested batch call")
        return sum(operation_budget(c["action"], c["arguments"]) for c in calls)
    return 7 if action == "memory" and arguments.get("action") == "model_handoff" else 1


def operation_count(action, arguments, response):
    if not response.get("ok"):
        return 1
    result = response.get("result")
    if action == "batch" and isinstance(result, dict):
        return sum(operation_count(c["action"], c["arguments"], c["response"]) for c in result["calls"])
    if action == "memory" and arguments.get("action") == "model_handoff":
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return 1
        return max(1, len(result["operations"]))
    return 1


def safe_path(root, name):
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Path is outside the worker workspace")
    return path


class Notebook:
    def __init__(self, root):
        self.root = Path(root)
        self.folder = self.root / ".notebook"
        self.folder.mkdir(exist_ok=True)
        if not (self.root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, capture_output=True)

    def notes(self):
        return [json.loads(p.read_text()) for p in sorted(self.folder.glob("*.json"))]

    def call(self, arguments):
        action = arguments.get("action")
        if action == "save" or action == "open_questions":
            text = arguments.get("text", "")
            if not text:
                raise ValueError("Save requires text")
            notes = self.notes()
            note_id = f"note-{len(notes) + 1:05d}"
            note = {"id": note_id, "text": text, "payload": arguments.get("payload") or {},
                    "open_question": action == "open_questions"}
            (self.folder / f"{note_id}.json").write_text(json.dumps(note, ensure_ascii=True))
            subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True, check=True)
            subprocess.run(["git", "-c", "user.email=study@localhost", "-c", "user.name=Study",
                            "commit", "--allow-empty", "-m", text.splitlines()[0]], cwd=self.root, capture_output=True, check=True)
            return note
        notes = self.notes()
        if action == "delete":
            targets = arguments.get("ids") or []
            for note in notes:
                if note["id"] in targets:
                    note["hidden"] = True
                    (self.folder / f"{note['id']}.json").write_text(json.dumps(note))
            return {"hidden": targets}
        notes = [n for n in notes if not n.get("hidden")]
        if action == "history":
            return notes
        if action != "find":
            raise ValueError("Notebook action is unavailable")
        if arguments.get("ids"):
            return [n for n in notes if n["id"] in arguments["ids"]]
        if arguments.get("text"):
            return [n for n in notes if arguments["text"].casefold() in n["text"].casefold()][:8]
        query = re.findall(r"\w+", arguments.get("query", "").casefold())
        docs = [re.findall(r"\w+", n["text"].casefold()) for n in notes]
        avg = sum(map(len, docs)) / max(1, len(docs)) or 1
        ranks = []
        for note, words in zip(notes, docs):
            score = 0.0
            for term in set(query):
                df = sum(term in d for d in docs)
                freq = words.count(term)
                score += math.log(1 + (len(docs) - df + 0.5) / (df + 0.5)) * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * len(words) / avg))
            ranks.append((score, note["id"], note))
        return [n for score, _, n in sorted(ranks, key=lambda x: (-x[0], x[1])) if score > 0][:8]


class WorkerTools:
    def __init__(self, root, arm):
        if arm not in ARMS:
            raise ValueError("Unknown method")
        self.root, self.arm = Path(root).resolve(), arm

    def memory(self, arguments):
        action = arguments.get("action")
        if action in MODEL_ACTIONS and self.arm != "models":
            raise ValueError("Executable model tools are disabled for this method")
        if action not in MEMORY_ACTIONS | MODEL_ACTIONS:
            raise ValueError("Memory action is not available in this study")
        if self.arm == "notebook":
            return Notebook(self.root).call(arguments)
        from agent_memory.core import Memory
        from agent_memory.tools import call_tool
        return call_tool(Memory(self.root), "memory", arguments)

    def packet(self):
        if self.arm == "notebook":
            notes = [n for n in Notebook(self.root).notes() if not n.get("hidden")]
            packet = "\n".join(f"[{n['id']}] {n['text']}" for n in notes[-4:])
        else:
            from agent_memory.core import Memory
            packet = Memory(self.root).render_packet()
        return packet[:1200]

    def snapshot(self):
        if self.arm == "notebook":
            return {"notes": len(Notebook(self.root).notes()), "model": None}
        from agent_memory.core import Memory
        m = Memory(self.root)
        history = m.history()
        return {"notes": len(history), "model": m.model_get(full=True), "checks": m.model_check(),
                "evidence": [{"id": n.id, "text": n.message, "payload": n.payload} for n in history]}

    def probe(self, arguments):
        from agent_memory.core import Memory
        m = Memory(self.root)
        return [m.model_predict(q["inputs"], q["target"]) for q in arguments["queries"]]

    def execute(self, action, arguments):
        if action == "batch":
            operation_budget(action, arguments)
            results = []
            for call in arguments["calls"]:
                try:
                    response = {"ok": True, "result": self.execute(call["action"], call["arguments"])}
                except Exception as error:
                    response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
                results.append({**call, "response": response})
                if not response["ok"]:
                    break
            return {"calls": results}
        if action == "memory":
            return self.memory(arguments)
        if action == "read":
            return {"content": safe_path(self.root, arguments["path"]).read_text()}
        if action == "write":
            p = safe_path(self.root, arguments["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(arguments["content"])
            return {"written": arguments["path"], "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        if action == "list":
            p = safe_path(self.root, arguments.get("path", "."))
            return sorted(str(c.relative_to(self.root)) for c in p.iterdir())
        if action == "run":
            # Never pass model credentials to shell commands.
            options = {}
            if os.environ.get("STUDY_CONTAINER") == "1":
                options = {"user": 1000, "group": 1000, "extra_groups": []}
            command = arguments["command"]
            if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
                raise ValueError("run requires an argument vector, not shell text")
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True,
                                    timeout=30, env={"PATH": os.environ["PATH"], "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}, **options)
            return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        raise ValueError("Unknown study tool")


def main():
    request = json.load(sys.stdin)
    tools = WorkerTools(request.get("root", "/workspace"), request["arm"])
    try:
        action = request["action"]
        result = tools.packet() if action == "_packet" else tools.snapshot() if action == "_snapshot" else tools.probe(request["arguments"]) if action == "_probe" else tools.execute(action, request.get("arguments", {}))
        output = {"ok": True, "result": result}
    except Exception as error:
        output = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(output, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
