"""Small, logged, model-only calls for private memory development experiments."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SCHEMA = {"type":"object", "properties":{"text":{"type":"string"}},
          "required":["text"], "additionalProperties":False}

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n")

def token_size(text):
    import tiktoken
    return len(tiktoken.get_encoding("o200k_base").encode(text))

def complete(prompt, output, timeout=90, model="gpt-5.6-sol"):
    """The output schema wraps arbitrary task JSON or prose in a text field.

    The model receives only the supplied prompt. Native tools, global memory,
    hooks, and plugins are disabled. Prompts and usage events remain available.
    No usage is estimated when a native completion event is absent.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/"prompt.txt").write_text(prompt)
    dump(output/"schema.json", SCHEMA)
    command=["codex","exec","--json","--ephemeral","--ignore-user-config",
             "--skip-git-repo-check","--sandbox","read-only","--model",model,
             "-c",'model_reasoning_effort="low"',"--output-schema",str(output/"schema.json")]
    for feature in ["shell_tool","multi_agent","apps","memories","goals","hooks","remote_plugin"]:
        command += ["--disable",feature]
    command += ["-c",'web_search="disabled"']
    start=time.monotonic()
    timed_out=False
    with tempfile.TemporaryDirectory(prefix="memory-micro-empty-") as scratch:
        command += ["--cd",scratch,"-"]
        with (output/"events.jsonl").open("w") as stdout,(output/"stderr.log").open("w") as stderr:
            proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,text=True)
            try:
                proc.communicate(input=prompt+"\nReturn the requested answer inside the JSON text field. Use short, direct sentences.",timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out=True
                proc.kill()
                proc.wait()
    events=[]; errors=[]; texts=[]; usage=[]; native=[]
    forbidden={"command_execution","file_change","mcp_tool_call","web_search","collab_tool_call"}
    for line in (output/"events.jsonl").read_text().splitlines():
        try: event=json.loads(line)
        except ValueError:
            errors.append("non_json_event")
            continue
        events.append(event)
        item=event.get("item") or {}
        if item.get("type") in forbidden: native.append(item.get("type"))
        if event.get("type") in {"error","turn.failed"}:
            errors.append(str(event.get("message",event.get("error",event))))
        if event.get("type")=="item.completed" and item.get("type")=="agent_message":
            texts.append(item.get("text",""))
        if event.get("type")=="turn.completed":
            u=event.get("usage") or {}
            if all(type(u.get(k)) is int and u[k]>=0 for k in ["input_tokens","output_tokens"]):
                usage.append(u)
            else: errors.append("missing_native_usage")
    text=None
    if texts:
        try: text=json.loads(texts[-1])["text"]
        except (ValueError,KeyError,TypeError): errors.append("invalid_output_schema")
    else: errors.append("no_answer")
    if proc.returncode: errors.append("exit_"+str(proc.returncode))
    if timed_out: errors.append("timeout")
    if native: errors.append("forbidden_native_tools")
    if not usage: errors.append("no_native_usage")
    result={"ok":not errors,"text":text,"errors":errors,"model":model,"reasoning":"low",
            "elapsed_seconds":time.monotonic()-start,"usage_complete":bool(usage) and not timed_out and proc.returncode==0,
            "usage":{"input_tokens":sum(u["input_tokens"] for u in usage),
                     "output_tokens":sum(u["output_tokens"] for u in usage)} if usage else None,
            "native_tools":native,"prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest()}
    dump(output/"result.json",result)
    if not result["ok"]: raise RuntimeError(json.dumps({k:result[k] for k in ["errors","model","elapsed_seconds"]}))
    return result

if __name__=="__main__":
    print(json.dumps(complete("Return exactly READY.",ROOT/"preflight",timeout=60),indent=2))
