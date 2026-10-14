"""Prepare, verify, execute, and report the private model-transfer study."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
import hashlib
import inspect
import itertools
import json
import os
import random
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from threading import Event, Lock

from .analysis import binomial_upper, summarize
from .runner import (CodexDecisions, DockerTools, IMAGE, Settings, append_jsonl,
                     run_stage, write_json)
from .tasks import build_task, canonical, digest, grade_outputs, reference, task_pack, validate_controls

ROOT = Path(__file__).resolve().parents[2]
ARMS = ("notebook", "notes", "models")


def source_hashes():
    paths = sorted((ROOT / "agent_memory").rglob("*.py")) + sorted((ROOT / "evaluation/model_transfer").glob("*.py"))
    paths += [ROOT / "evaluation/model_transfer/Dockerfile", ROOT / "pyproject.toml"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def prepare(output, phase, settings):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tasks = ([build_task(f, 73) for f in ("source_dependencies", "calibration_limits")]
             if phase == "handoff" else task_pack(phase))
    controls = validate_controls(tasks)
    repeats = 2 if phase == "pilot" else 1
    permutations = list(itertools.permutations(ARMS))
    schedule = []
    for repeat in range(repeats):
        for i, task in enumerate(tasks):
            for arm in (("models",) if phase == "handoff" else permutations[(i + repeat * len(tasks)) % 6]):
                schedule.append({"task": task["id"], "repeat": repeat, "arm": arm})
    # Preselect equal numbers by domain. These tasks are never picked by outcome.
    ablation_count = 8 if phase == "pilot" else 12
    selected = []
    for domain in ("code", "research"):
        selected += [t["id"] for t in tasks if t["domain"] == domain][:ablation_count // 2]
    write_json(output / "private/task-pack.json", tasks)
    write_json(output / "private/controls.json", controls)
    frozen_sources = source_hashes()
    for name in frozen_sources:
        target = output / "private/source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    manifest = {"schema": 1, "phase": phase, "settings": asdict(settings), "repeats": repeats,
                "task_ids": [t["id"] for t in tasks], "task_digest": digest(tasks),
                "control_digest": digest(controls), "schedule": schedule, "ablation_tasks": selected,
                "source_hashes": frozen_sources, "analysis": {"bootstrap": 10000, "seed": 1327,
                 "task_unit": "distinct constructed work sample; repeats remain within task",
                 "confidence": "paired domain-stratified bootstrap; 97.5% one-sided upper cost bound per baseline; exact 97.5% added-failure bound",
                 "useful_ratio": 0.75, "exceptional_ratio": 0.5, "max_added_failure": 0.05,
                 "pilot_ratio": 0.85, "pilot_added_failures": 0,
                 "completion": "final stage checks pass, including all earlier-stage regression cases",
                 "model_exposure": "accepted tested model at every model-method handoff"},
                "execution": {"parallel_task_blocks": 3, "order": "counterbalanced method order within each task/repeat block"},
                "limits": ["Constructed task population, eight families, shared author for task contracts and oracle",
                 "Confirmation variants are new instances of these families, not 96 new domains",
                 "Structured decision loop; not native IDE-agent performance",
                 "No monetary cost estimate without verified billing",
                 "Research records are constructed test evidence, not measured fabrication data"]}
    write_json(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(digest(manifest) + "\n")
    return manifest


def verify(output):
    output = Path(output)
    m = json.loads((output / "manifest.json").read_text())
    tasks = json.loads((output / "private/task-pack.json").read_text())
    controls = json.loads((output / "private/controls.json").read_text())
    if digest(m) != (output / "manifest.sha256").read_text().strip():
        raise ValueError("Frozen manifest changed")
    if digest(tasks) != m["task_digest"] or digest(controls) != m["control_digest"]:
        raise ValueError("Frozen task pack or controls changed")
    current = source_hashes()
    if current != m["source_hashes"]:
        raise ValueError("Study or memory source changed after freeze; prepare a new study")
    return m, tasks


def build_image(log):
    """Only the memory backend and tool gateway enter the worker image."""
    with tempfile.TemporaryDirectory(prefix="memory-study-build-") as tmp:
        context = Path(tmp)
        shutil.copytree(ROOT / "agent_memory", context / "agent_memory", ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
        shutil.copyfile(ROOT / "evaluation/model_transfer/worker.py", context / "worker.py")
        shutil.copyfile(ROOT / "evaluation/model_transfer/Dockerfile", context / "Dockerfile")
        with Path(log).open("w") as f:
            p = subprocess.run(["docker", "build", "-t", IMAGE, str(context)], stdout=f, stderr=subprocess.STDOUT, timeout=900)
    if p.returncode:
        raise RuntimeError(f"Worker image build failed; see {log}")


def preflight(output, settings, *, host_model=False):
    output = Path(output)
    folder = output / ("host-preflight" if host_model else "preflight")
    folder.mkdir(exist_ok=False)
    report = {"model": settings.model, "model_access": False, "metering": False, "isolation": False, "errors": []}
    docker = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
    write_json(folder / "docker.json", {"exit_code": docker.returncode, "stdout": docker.stdout, "stderr": docker.stderr})
    if docker.returncode and not host_model:
        report["errors"].append("Docker service is unavailable")
    try:
        c = CodexDecisions(settings, container=not host_model).complete(
            "Do not use native tools. Return {action:finish,arguments_json:'{}'} as valid JSON.", folder / "completion", 60)
        report["model_access"] = c["ok"]
        report["metering"] = c["usage_complete"]
        report["errors"].extend(c["errors"])
    except Exception as error:
        report["errors"].append(f"Completion check failed: {type(error).__name__}: {error}")
    if docker.returncode == 0 and not host_model:
        try:
            # A sibling secret and the real private task pack must be absent.
            with tempfile.TemporaryDirectory(prefix="memory-study-isolation-") as tmp:
                secret = Path(tmp) / "private-grade-canary.json"
                secret.write_text('"SEALED GRADER"')
                workspace = Path(tmp) / "worker"
                workspace.mkdir(mode=0o777)
                workspace.chmod(0o777)
                with DockerTools(workspace, "notes", settings.image) as tools:
                    probe = tools.execute("run", {"command": ["python", "-c",
                        "import os,socket,pathlib; assert not pathlib.Path('/private-grade-canary.json').exists(); "
                        "assert not os.access('/opt/backend/tasks.py',os.R_OK); "
                        "assert not os.access('/root/.codex/auth.json',os.R_OK); "
                        "s=socket.socket(); s.settimeout(1); "
                        "r=s.connect_ex(('1.1.1.1',443)); assert r!=0; print('ISOLATED')"]})
                    denied = tools.execute("read", {"path": "../private-grade-canary.json"})
                    report["isolation"] = probe["ok"] and probe["result"]["exit_code"] == 0 and not denied["ok"]
                    write_json(folder / "isolation.json", {"probe": probe, "outside_read": denied})
            image = subprocess.run(["docker", "image", "inspect", settings.image, "--format", "{{.Id}}"], capture_output=True, text=True)
            report["image_id"] = image.stdout.strip()
        except Exception as error:
            report["errors"].append(f"Isolation check failed: {type(error).__name__}: {error}")
    report["ready"] = report["model_access"] and report["metering"] and report["isolation"] and not report["errors"]
    write_json(folder / "result.json", report)
    return report


# This grader driver has no oracle. Only test inputs enter the grade container.
GRADE_DRIVER = '''import contextlib,copy,importlib.util,io,json,pathlib,sys
p=json.load(sys.stdin)
output=[]
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
 spec=importlib.util.spec_from_file_location("candidate", "/workspace/work.py")
 module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
 for case in p["cases"]:
  args=[copy.deepcopy(case["request"])]
  if p["research"]: args.append(copy.deepcopy(case["evidence"]))
  args.append(copy.deepcopy(case["policy"]))
  before=json.dumps(args,sort_keys=True)
  try:
   value=module.evaluate(*args)
   if json.dumps(args,sort_keys=True)!=before: value={"_grader_error":"input mutation"}
   output.append(value)
  except Exception as e: output.append({"_grader_error":type(e).__name__+": "+str(e)})
 try: report=json.loads(pathlib.Path("/workspace/report.json").read_text())
 except Exception: report=None
print(json.dumps({"outputs":output,"report":report},allow_nan=False))
'''


def grade_workspace(workspace, task, stage, image):
    payload = {"policy": stage["policy"], "research": task["domain"] == "research",
               "cases": [{"request": c["request"], "evidence": c["evidence"], "policy": stage["policy"]} for c in stage["cases"]]}
    grading_stage = stage
    if stage["stage"] == 3:
        earlier = [dict(c, _policy=s["policy"]) for s in task["stages"][:-1] for c in s["cases"]]
        payload["cases"] += [{"request": c["request"], "evidence": c["evidence"], "policy": c["_policy"]} for c in earlier]
        grading_stage = dict(stage, cases=stage["cases"] + earlier)
    command = ["docker", "run", "--rm", "-i", "--network=none", "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=512m", "--cpus=1",
               "--mount", f"type=bind,src={Path(workspace).resolve()},dst=/workspace,readonly",
               image, "python", "-c", GRADE_DRIVER]
    try:
        p = subprocess.run(command, input=canonical(payload), capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(p.stderr)
        result = json.loads(p.stdout)
        grade = grade_outputs(grading_stage, result["outputs"], result["report"])
    except Exception as error:
        grade = {"full_pass": False, "passed": 0, "total": len(grading_stage["cases"]) + 2,
                 "checks": [], "error": f"{type(error).__name__}: {error}"}
    return grade


def validate_worker_controls(output, tasks, settings):
    """Run actual Python controls through the same isolated grading route."""
    rows = []
    prefix = "import json,math\n" + inspect.getsource(canonical) + "\n" + inspect.getsource(reference)
    for task in tasks:
        for stage in task["stages"]:
            with tempfile.TemporaryDirectory(prefix="memory-study-control-") as tmp:
                w = Path(tmp)
                install_stage(w, stage)
                good = prefix + f"\ndef evaluate(*args):\n return reference({task['family']!r},args[0],args[-1],args[1] if len(args)==3 else None)\n"
                (w / "work.py").write_text(good)
                visible = [c["expected"] for c in stage["cases"][:stage["visible_count"]]]
                write_json(w / "report.json", visible)
                reference_grade = grade_workspace(w, task, stage, settings.image)
                (w / "work.py").write_text("def evaluate(*args):\n return {}\n")
                incomplete = grade_workspace(w, task, stage, settings.image)
                (w / "work.py").write_text(f"def evaluate(*args):\n return {stage['cases'][0]['expected']!r}\n")
                constant = grade_workspace(w, task, stage, settings.image)
                rows.append({"task": task["id"], "stage": stage["stage"],
                             "reference_pass": reference_grade["full_pass"],
                             "incomplete_rejected": not incomplete["full_pass"],
                             "constant_rejected": not constant["full_pass"],
                             "reference_error": reference_grade.get("error")})
    write_json(Path(output) / "private/worker-control-results.json", rows)
    if not all(r["reference_pass"] and r["incomplete_rejected"] and r["constant_rejected"] for r in rows):
        raise ValueError("The isolated grader failed a reference or negative control")
    return rows


def install_stage(workspace, stage):
    for name, text in stage["files"].items():
        path = Path(workspace) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o666)


def checkpoint(workspace, label):
    for args in (["init", "-q"], ["config", "user.email", "study@localhost"], ["config", "user.name", "Study"],
                 ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", label]):
        subprocess.run(["git", "-c", f"safe.directory={Path(workspace).resolve()}", *args], cwd=workspace, check=True, capture_output=True)


def probe_inputs(task, stage, case, declared):
    inputs = {"request": case["request"], "policy": stage["policy"]}
    if "evidence" in declared:
        inputs["evidence"] = case["evidence"] if task["domain"] == "research" else []
    return inputs


def execute_trajectory(output, task, repeat, arm, settings, provider, stop_event=None):
    run = Path(output) / "runs" / task["id"] / f"repeat-{repeat}" / arm
    run.mkdir(parents=True, exist_ok=False)
    workspace = run / "workspace"
    workspace.mkdir(mode=0o777)
    workspace.chmod(0o777)
    stages = []
    for stage in task["stages"]:
        if stop_event is not None and stop_event.is_set():
            break
        install_stage(workspace, stage)
        checkpoint(workspace, f"Stage {stage['stage']} input")
        # This snapshot includes final input files and precedes the final receiver.
        if stage["stage"] == 3 and arm == "models":
            shutil.copytree(workspace, run / "last-handoff-donor")
        with DockerTools(workspace, arm, settings.image) as tools:
            packet = tools.execute("_packet")
            if not packet["ok"]:
                raise RuntimeError(packet["error"])
            result = run_stage(provider, tools, stage["files"]["TASK.md"], packet["result"], run / f"stage-{stage['stage']}", settings, stop_event)
            snapshot = tools.execute("_snapshot")
            record = snapshot.get("result", {}).get("model")
            if arm == "models" and record:
                definition = record["model"]
                target = "status" if task["domain"] == "research" else "needs_review" if task["family"] == "source_dependencies" else "ok"
                declared = set(definition["inputs"])
                roots = {"request", "policy", "evidence"}
                if declared <= roots and target in {r["target"] for r in definition["rules"]}:
                    queries = []
                    for case in stage["cases"][stage["visible_count"]:]:
                        inputs = probe_inputs(task, stage, case, declared)
                        queries.append({"inputs": inputs, "target": target})
                    # Fixed interpreter only; no hidden outputs enter agent context.
                    predictions = tools.execute("_probe", {"queries": queries})
                    result["model_probes"] = {"target": target, "predictions": predictions,
                        "expected": [c["expected"][target] for c in stage["cases"][stage["visible_count"]:]]}
                else:
                    result["model_probes"] = {"status": "unprobed", "reason": "Model uses other inputs or targets; no feature extractor was validated"}
        checkpoint(workspace, f"Stage {stage['stage']} output")
        grade = grade_workspace(workspace, task, stage, settings.image)
        result.update(stage=stage["stage"], grade=grade, memory=snapshot)
        print(f"Stage done: {task['id']} repeat={repeat} method={arm} stage={stage['stage']} stop={result['stop']} pass={grade['full_pass']} tokens={result['tokens']}", flush=True)
        write_json(run / f"stage-{stage['stage']}" / "result.json", result)
        stages.append(result)
        if not result["usage_complete"] or result["stop"] == "completion_protocol_failure":
            break
    complete = len(stages) == 3 and stages[-1]["grade"]["full_pass"]
    model_transfer = arm != "models" or len(stages) == 3 and all(
        s["memory"].get("ok") and s["memory"].get("result", {}).get("model") and
        s["memory"]["result"]["checks"].get("status") == "tested" for s in stages)
    known = bool(stages) and all(s["usage_complete"] for s in stages)
    row = {"task": task["id"], "domain": task["domain"], "family": task["family"], "repeat": repeat,
           "arm": arm, "comparison": "main", "complete": complete, "isolated": True,
           "run_valid": all(s["stop"] != "completion_protocol_failure" for s in stages),
           "model_transfer_complete": bool(model_transfer),
           "every_stage_passed": len(stages) == 3 and all(s["grade"]["full_pass"] for s in stages),
           "usage_complete": known, "tokens": sum(s["tokens"] for s in stages) if known else None,
           "known_tokens": sum(s["known_tokens"] for s in stages), "stages": stages}
    seen, reads, repeats_read = set(), 0, 0
    for path in sorted(run.glob("stage-*/run.jsonl")):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            events = [event]
            if event.get("type") == "tool" and event.get("action") == "batch" and event["result"].get("ok"):
                events = [{"type": "tool", "action": c["action"], "arguments": c["arguments"], "result": c["response"]}
                          for c in event["result"]["result"]["calls"]]
            for item in events:
                if item.get("type") == "tool" and item.get("action") == "read" and item["result"].get("ok"):
                    text = item["result"]["result"].get("content", "")
                    key = (item["arguments"].get("path"), hashlib.sha256(text.encode()).hexdigest())
                    reads += 1
                    repeats_read += key in seen
                    seen.add(key)
    row["repeated_identical_file_reads"] = repeats_read
    row["file_reads"] = reads
    row["elapsed_seconds"] = sum(s["elapsed_seconds"] for s in stages)
    write_json(run / "trajectory.json", row)
    return row


def ablations(output, tasks, selected, settings, provider):
    rows = []
    for task_id in selected:
        task = tasks[task_id]
        donor = Path(output) / "runs" / task_id / "repeat-0/models/last-handoff-donor"
        if not donor.exists():
            rows.append({"task": task_id, "comparison": "execution", "status": "unavailable", "reason": "No donor workspace"})
            continue
        with DockerTools(donor, "models", settings.image) as tools:
            snap = tools.execute("_snapshot")
        model = snap.get("result", {}).get("model")
        if not model:
            rows.append({"task": task_id, "comparison": "execution", "status": "unavailable", "reason": "Agent did not create a model"})
            continue
        # Both receivers see byte-identical raw evidence AND the same full model.
        common = Path(output) / "ablations" / task_id / "common"
        shutil.copytree(donor, common)
        body = canonical({"stored_model": model, "checks": snap["result"]["checks"]})
        (common / "TRANSFER_MODEL.json").write_text(body)
        export = canonical(snap["result"])
        (common / "TRANSFER_EVIDENCE.json").write_text(export)
        for i, arm in enumerate(("written", "models") if selected.index(task_id) % 2 == 0 else ("models", "written")):
            folder = common.parent / arm
            shutil.copytree(common, folder / "workspace")
            workspace = folder / "workspace"
            with DockerTools(workspace, arm, settings.image) as tools:
                text = task["stages"][-1]["files"]["TASK.md"] + "\nTRANSFER_MODEL.json and TRANSFER_EVIDENCE.json contain the same transferred rules and complete stored memory in both methods."
                result = run_stage(provider, tools, text, "Read TRANSFER_MODEL.json and TRANSFER_EVIDENCE.json.", folder / "stage-3", settings)
            grade = grade_workspace(workspace, task, task["stages"][-1], settings.image)
            row = {"task": task_id, "arm": arm, "comparison": "execution", "status": "run", "stage": result, "grade": grade,
                   "common_digest": digest(body), "donor": str(donor)}
            append_jsonl(Path(output) / "records.jsonl", row)
            rows.append(row)
            if not result["usage_complete"]:
                return rows
    return rows


def report(output, preflight_result=None):
    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    path = output / "records.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    result = summarize(rows, phase=manifest["phase"], expected_task_ids=manifest["task_ids"], repeats=manifest["repeats"])
    result["preflight"] = preflight_result
    result["execution_comparison"] = [r for r in rows if r.get("comparison") == "execution"]
    result["execution_analysis"] = execution_analysis(result["execution_comparison"])
    main = [r for r in rows if r.get("comparison") == "main"]
    result["resource_details"] = {arm: {"elapsed_seconds": sum(r.get("elapsed_seconds",0) for r in main if r["arm"]==arm),
        "repeated_identical_file_reads": sum(r.get("repeated_identical_file_reads",0) for r in main if r["arm"]==arm),
        "unsupported_conclusions": sum(s["grade"].get("unsupported_conclusions",0) for r in main if r["arm"]==arm for s in r["stages"]),
        "missed_source_changes": sum(s["grade"].get("missed_source_changes",0) for r in main if r["arm"]==arm for s in r["stages"])} for arm in ARMS}
    paired = {(r["task"],r["repeat"],r["arm"]):r for r in main}
    breakeven = []
    for r in main:
        if r["arm"] != "models" or not r["usage_complete"]:
            continue
        for baseline in ("notebook","notes"):
            other = paired.get((r["task"],r["repeat"],baseline))
            if not other or not other["usage_complete"]:
                continue
            cumulative_new, cumulative_old, first = 0,0,None
            for n,(a,b) in enumerate(zip(r["stages"],other["stages"]),1):
                cumulative_new += a["tokens"]
                cumulative_old += b["tokens"]
                if first is None and cumulative_new <= cumulative_old:
                    first=n
            breakeven.append({"task":r["task"],"repeat":r["repeat"],"baseline":baseline,
                              "first_cumulative_cost_crossing_stage":first,"both_correct":r["complete"] and other["complete"]})
    result["break_even"] = breakeven
    result["model_details"] = [{"task":r["task"],"repeat":r["repeat"],"stage":s["stage"],
        "checks":s["memory"].get("result",{}).get("checks"),
        "model":s["memory"].get("result",{}).get("model"),
        "unseen_probes":s.get("model_probes")} for r in main if r["arm"]=="models" for s in r["stages"]]
    result["manifest_hash"] = digest(manifest)
    result["limits"] = list(dict.fromkeys(result.get("limits", []) + manifest["limits"]))
    if preflight_result and not preflight_result["ready"]:
        result["conclusion"] = "inconclusive"
        result["reasons"] = result.get("reasons", []) + preflight_result["errors"]
    write_json(output / "report.json", result)
    text = f"# Model-transfer study\n\nConclusion: **{result['conclusion']}**.\n\n"
    text += f"Phase: {manifest['phase']}. Frozen tasks: {len(manifest['task_ids'])}.\n\n"
    if result.get("reasons"):
        text += "The study does not establish a performance gain.\n\n" + "\n".join(f"- {reason}" for reason in result["reasons"]) + "\n\n"
    if result.get("comparisons"):
        text += "| Baseline | Token ratio | Ratio interval | Added-failure upper bound |\n|---|---:|---|---:|\n"
        for name, c in result["comparisons"].items():
            text += f"| {name} | {c['token_ratio']} | {c['ratio_interval']} | {c['additional_failure_upper']} |\n"
        text += "\nPilot continuation: " + str(result["pilot_continue"]) + ".\n\n"
    for domain, data in result.get("by_domain", {}).items():
        text += f"{domain.capitalize()} results:\n\n| Method | Tokens per completed task | Completed tasks |\n|---|---:|---:|\n"
        for arm in ARMS:
            values = data[arm]
            text += f"| {arm} | {values['tokens_per_completed_task']} | {values['completed_tasks']} |\n"
        text += "\nDomain conclusion: " + data["conclusion"] + ".\n\n"
        for baseline, values in data["comparisons"].items():
            text += f"- {baseline}: ratio {values['token_ratio']}; interval {values['ratio_interval']}; added-failure upper bound {values['additional_failure_upper']}.\n"
        text += "\n"
    execution = result["execution_analysis"]
    text += f"Written-rule comparison: {execution['paired_tasks']} complete pairs. This is a separate receiver-cost comparison.\n\n"
    text += "Limits:\n\n" + "\n".join(f"- {s}" for s in result["limits"]) + "\n"
    (output / "report.md").write_text(text)
    return result


def execution_analysis(rows):
    """Keep final receivers separate from full-trajectory cost."""
    pairs = {}
    for row in rows:
        if row.get("status") == "run":
            pairs.setdefault(row["task"], {})[row["arm"]] = row
    paired = [p for p in pairs.values() if set(p)=={"models","written"} and
              all(r["stage"]["usage_complete"] for r in p.values())]
    def cost_ratio(draw):
        totals = {a:sum(p[a]["stage"]["tokens"] for p in draw) for a in ("models","written")}
        passes = {a:sum(p[a]["grade"]["full_pass"] for p in draw) for a in totals}
        if not all(passes.values()) or not totals["written"]:
            return None
        return (totals["models"]/passes["models"])/(totals["written"]/passes["written"])
    rng = random.Random(1327)
    samples = [cost_ratio(rng.choices(paired,k=len(paired))) for _ in range(10000)] if paired else []
    defined = sorted(x for x in samples if x is not None)
    extra = sum(not p["models"]["grade"]["full_pass"] and p["written"]["grade"]["full_pass"] for p in paired)
    return {"paired_tasks":len(paired),"token_ratio":cost_ratio(paired),
            "ratio_interval":[defined[int(0.025*len(defined))],defined[min(len(defined)-1,int(0.975*len(defined)))]] if defined else None,
            "undefined_bootstrap_samples":len(samples)-len(defined),
            "additional_failure_tasks":extra,
            "additional_failure_upper":binomial_upper(extra,len(paired)) if paired else None,
            "limits":["Receiver tokens only; sender costs stay in the main comparison",
                      "Same initial evidence and model; executable receivers can also revise models",
                      "Unavailable donors and incomplete pairs are reported, not replaced"]}


def handoff_check(output):
    """Unscored full handoffs, separate from pilot tasks and performance claims."""
    output = Path(output)
    manifest, tasks = verify(output)
    if manifest["phase"] != "handoff":
        raise ValueError("Handoff check requires its own frozen work samples")
    settings = Settings(**manifest["settings"])
    readiness = preflight(output, settings)
    if not readiness["ready"]:
        result = {"ready": False, "passed": False, "preflight": readiness, "rows": []}
        write_json(output / "handoff-result.json", result)
        return result
    settings = replace(settings, image=readiness["image_id"])
    validate_worker_controls(output, tasks, settings)
    provider, stop = CodexDecisions(settings), Event()
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute_trajectory, output, task, 0, "models", settings, provider, stop) for task in tasks]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            append_jsonl(output / "records.jsonl", row)
    verify(output)
    passed = all(r["usage_complete"] and r["run_valid"] and r["model_transfer_complete"]
                 and r["every_stage_passed"] and all(s["stop"] == "finished" for s in r["stages"]) for r in rows)
    result = {"ready": True, "passed": passed, "scored": False,
              "purpose": "Full three-stage model handoffs; no token-saving estimate",
              "tokens": sum(r["tokens"] for r in rows) if all(r["usage_complete"] for r in rows) else None,
              "known_tokens": sum(r["known_tokens"] for r in rows), "preflight": readiness, "rows": rows}
    write_json(output / "handoff-result.json", result)
    return result


def run(output, pilot=None):
    output = Path(output)
    manifest, tasks = verify(output)
    settings = Settings(**manifest["settings"])
    if manifest["phase"] == "confirmation":
        if not pilot:
            raise ValueError("Confirmation requires a completed pilot report")
        previous = json.loads((Path(pilot) / "report.json").read_text())
        pm, _ = verify(pilot)
        if not previous.get("pilot_continue") or pm["settings"] != manifest["settings"]:
            raise ValueError("Pilot gate failed or model/settings differ")
    readiness = preflight(output, settings)
    if not readiness["ready"]:
        return report(output, readiness)
    # Every scored container uses the same immutable image, even if its tag moves.
    settings = replace(settings, image=readiness["image_id"])
    validate_worker_controls(output, tasks, settings)
    provider = CodexDecisions(settings)
    by_id = {t["id"]: t for t in tasks}
    blocks = []
    for _, group in itertools.groupby(manifest["schedule"], key=lambda r: (r["task"], r["repeat"])):
        blocks.append(list(group))
    stop_dispatch = Event()
    record_lock = Lock()
    def block(orders):
        rows = []
        for order in orders:
            if stop_dispatch.is_set():
                break
            print(f"Run {order['task']} repeat={order['repeat']} method={order['arm']}", flush=True)
            row = execute_trajectory(output, by_id[order["task"]], order["repeat"], order["arm"], settings, provider, stop_dispatch)
            with record_lock:
                append_jsonl(output / "records.jsonl", row)
            rows.append(row)
            if not row["usage_complete"] or not row["run_valid"]:
                stop_dispatch.set()
                break
        return rows
    failed = False
    with ThreadPoolExecutor(max_workers=manifest["execution"]["parallel_task_blocks"]) as pool:
        # Dispatch only one bounded batch. A metering fault stops later batches.
        for start in range(0, len(blocks), 3):
            futures = [pool.submit(block, b) for b in blocks[start:start+3]]
            for future in as_completed(futures):
                try:
                    rows = future.result()
                except Exception as error:
                    append_jsonl(output / "faults.jsonl", {"error": f"{type(error).__name__}: {error}"})
                    failed = True
                    stop_dispatch.set()
                    continue
                for row in rows:
                    failed |= not row["usage_complete"] or not row["run_valid"]
            if failed:
                break
    if failed:
        return report(output, readiness)
    ablations(output, by_id, manifest["ablation_tasks"], settings, provider)
    verify(output)
    return report(output, readiness)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "verify", "build", "preflight", "host-preflight", "handoff-check", "run", "report"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("pilot", "confirmation", "handoff"), default="pilot")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning", default="high")
    parser.add_argument("--pilot", type=Path)
    args = parser.parse_args()
    settings = Settings(model=args.model, reasoning=args.reasoning)
    if args.operation == "prepare":
        result = prepare(args.output, args.phase, settings)
        print(json.dumps({"phase": result["phase"], "tasks": len(result["task_ids"]), "trajectories": len(result["schedule"])}))
    elif args.operation == "build":
        args.output.mkdir(parents=True, exist_ok=True)
        build_image(args.output / "image-build.log")
    elif args.operation == "verify":
        verify(args.output)
        print("Frozen inputs and source verified")
    elif args.operation in ("preflight", "host-preflight"):
        m, _ = verify(args.output)
        result = preflight(args.output, Settings(**m["settings"]), host_model=args.operation == "host-preflight")
        report(args.output, result)
        print(json.dumps(result))
    elif args.operation == "handoff-check":
        result = handoff_check(args.output)
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    elif args.operation == "run":
        print(json.dumps(run(args.output, args.pilot)))
    else:
        existing = args.output / "preflight/result.json"
        readiness = json.loads(existing.read_text()) if existing.exists() else None
        print(json.dumps(report(args.output, readiness)))


if __name__ == "__main__":
    main()
