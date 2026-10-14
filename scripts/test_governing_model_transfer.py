"""Private, deterministic mechanism test. No LLM performance claim.

Freeze the protocol before execution. Each condition and handoff uses a fresh
Python process. The controller supplies observations; receivers do not receive
the hidden rule. All receivers use the same finite candidate language and
test-selection algorithm. An incremental notebook is an essential control.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SHAPES = ("linear", "square", "absolute", "cube", "triangle", "clamp2",
          "sign", "absolute_squared")
COEFFICIENTS = (-2, -1, 1, 2)
CONDITIONS = ("notes_replay", "notes_with_cache", "executable_transfer")
PROBES = list(itertools.product(range(-3, 4), range(-3, 4)))
EVALUATION = list(itertools.product((-8, -7, -6, -5, -4, 4, 5, 6, 7, 8),
                                   range(-5, 6)))
STAGES = ("initial_ambiguity", "discover", "transfer", "source_change")
DOMAIN = {"x": [-8, 8], "u": [-5, 5]}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def shape_expression(shape):
    u = ["var", "u"]
    if shape == "linear":
        return u
    if shape == "square":
        return ["pow", u, 2]
    if shape == "absolute":
        return ["abs", u]
    if shape == "cube":
        return ["pow", u, 3]
    if shape == "triangle":
        return ["div", ["mul", ["abs", u], ["add", ["abs", u], 1]], 2]
    if shape == "clamp2":
        return ["clamp", u, -2, 2]
    if shape == "sign":
        return ["sign", u]
    if shape == "absolute_squared":
        return ["pow", ["abs", u], 2]
    raise ValueError(shape)


def candidate(a, b, c, shape):
    return {"id": f"{a}:{b}:{c}:{shape}",
            "expression": ["add", ["mul", a, ["var", "x"]],
                           ["add", ["mul", b, shape_expression(shape)], c]]}


def universe():
    return [candidate(a, b, c, shape) for a, b, c, shape in
            itertools.product(COEFFICIENTS, COEFFICIENTS, (-1, 0, 1), SHAPES)]


def evaluate(expr, x, u):
    """Fixed expression interpreter. No eval, imports, or arbitrary code."""
    if type(expr) is int:
        return expr
    op, *args = expr
    if op == "var":
        return {"x": x, "u": u}[args[0]]
    values = [evaluate(arg, x, u) for arg in args]
    if op == "add":
        return values[0] + values[1]
    if op == "mul":
        return values[0] * values[1]
    if op == "pow":
        return values[0] ** values[1]
    if op == "abs":
        return abs(values[0])
    if op == "div":
        return values[0] // values[1]
    if op == "clamp":
        return max(values[1], min(values[2], values[0]))
    if op == "sign":
        return (values[0] > 0) - (values[0] < 0)
    raise ValueError(f"Unsupported operator: {op}")


def predict(packet, x, u, revision):
    if revision != packet["revision"]:
        return {"status": "unknown", "reason": "source_changed"}
    if type(x) is not int or type(u) is not int:
        return {"status": "unknown", "reason": "input_type"}
    if not (DOMAIN["x"][0] <= x <= DOMAIN["x"][1]
            and DOMAIN["u"][0] <= u <= DOMAIN["u"][1]):
        return {"status": "unknown", "reason": "outside_domain"}
    candidates = packet["candidates"]
    if not candidates:
        return {"status": "unknown", "reason": "contradiction"}
    values = {evaluate(rule["expression"], x, u) for rule in candidates}
    if len(values) != 1:
        return {"status": "unknown", "reason": "ambiguous"}
    return {"status": "predicted", "value": values.pop()}


def filter_rules(rules, observations, cost):
    for obs in observations:
        retained = []
        for rule in rules:
            cost["fit_relation_evaluations"] += 1
            if evaluate(rule["expression"], obs["x"], obs["u"]) == obs["y"]:
                retained.append(rule)
        rules = retained
    return rules


def choose_probe(rules, evidence, cost):
    used = {(obs["x"], obs["u"]) for obs in evidence}
    best = None
    for x, u in PROBES:
        if (x, u) in used:
            continue
        buckets = {}
        for rule in rules:
            cost["probe_relation_evaluations"] += 1
            y = evaluate(rule["expression"], x, u)
            buckets[y] = buckets.get(y, 0) + 1
        if len(buckets) < 2:
            continue
        # Minimax split; no access to the true rule or evaluation answers.
        key = (max(buckets.values()), -len(buckets), x, u)
        if best is None or key < best[0]:
            best = (key, [x, u])
    return best[1] if best else None


def packet_for(rules, revision, observations, trace, rejected=None):
    return {"schema": "governing-model-v1", "revision": revision,
            "domain": DOMAIN, "input_type": "integer", "units": "dimensionless",
            "status": "consistent" if rules else "contradicted",
            "candidates": rules, "rejected_candidates": rejected or [],
            "evidence_count": len(observations), "evidence_sha256": digest(observations),
            "reduction_trace": trace,
            "requirement": "Predict next_x inside the stated domain; retain ambiguity."}


def worker():
    request = json.loads(sys.stdin.readline())
    condition = request["condition"]
    all_rules = universe()
    by_id = {rule["id"]: rule for rule in all_rules}
    states = []
    cost = {"fit_relation_evaluations": 0, "probe_relation_evaluations": 0,
            "prediction_relation_evaluations": 0, "source_invalidations": 0}
    for row in request["rows"]:
        evidence = row["observations"]
        prior = row["prior"]
        changed = prior is not None and prior["revision"] != row["revision"]
        if changed:
            cost["source_invalidations"] += 1
        if condition == "notes_replay" or prior is None or changed:
            rules, seen, trace = list(all_rules), 0, [len(all_rules)]
        elif condition == "notes_with_cache":
            rules = [by_id[rule_id] for rule_id in prior["candidate_ids"]]
            seen, trace = prior["evidence_count"], list(prior["reduction_trace"])
        else:
            rules = prior["candidates"]
            seen, trace = prior["evidence_count"], list(prior["reduction_trace"])
        if prior is not None and not changed and condition != "notes_replay":
            if digest(evidence[:seen]) != prior["evidence_sha256"]:
                raise ValueError("Evidence prefix changed without a source revision")
        before = list(rules)
        rules = filter_rules(rules, evidence[seen:], cost)
        trace.append(len(rules))
        states.append({"id": row["id"], "revision": row["revision"],
                       "evidence": evidence, "rules": rules, "trace": trace,
                       "rejected": before if not rules else []})
    for _ in range(request["probe_budget"]):
        proposals = [{"id": state["id"], "probe": choose_probe(
            state["rules"], state["evidence"], cost)} for state in states]
        print(encoded({"type": "probes", "rows": proposals}), flush=True)
        answers = json.loads(sys.stdin.readline())
        by_task = {row["id"]: row["observation"] for row in answers["rows"]}
        for state in states:
            obs = by_task[state["id"]]
            if obs is not None:
                state["evidence"].append(obs)
                before = list(state["rules"])
                state["rules"] = filter_rules(state["rules"], [obs], cost)
                state["trace"].append(len(state["rules"]))
                if not state["rules"]:
                    state["rejected"] = before
    output = []
    for state in states:
        packet = packet_for(state["rules"], state["revision"], state["evidence"],
                            state["trace"], state["rejected"])
        predictions = []
        for x, u in EVALUATION:
            cost["prediction_relation_evaluations"] += len(state["rules"])
            predictions.append(predict(packet, x, u, state["revision"]))
        if condition == "notes_with_cache":
            # Same computed knowledge, stored as a cache in the notebook.
            transfer = {key: value for key, value in packet.items()
                        if key not in ("candidates", "rejected_candidates")}
            transfer["candidate_ids"] = [rule["id"] for rule in state["rules"]]
        elif condition == "notes_replay":
            transfer = {"revision": state["revision"]}
        else:
            transfer = packet
        output.append({"id": state["id"], "transfer": transfer, "model": packet,
                       "predictions": predictions, "observations": state["evidence"]})
    print(encoded({"type": "result", "rows": output, "cost": cost}), flush=True)


def reference(rule, x, u):
    """Controller-only oracle. Uses direct arithmetic, not the interpreter."""
    a, b, c, shape = rule
    if shape == "linear":
        h = u
    elif shape == "square":
        h = u * u
    elif shape == "absolute":
        h = abs(u)
    elif shape == "cube":
        h = u * u * u
    elif shape == "triangle":
        h = sum(range(1, abs(u) + 1))
    elif shape == "clamp2":
        h = -2 if u < -2 else (2 if u > 2 else u)
    elif shape == "sign":
        h = -1 if u < 0 else (1 if u > 0 else 0)
    elif shape == "absolute_squared":
        h = abs(u) * abs(u)
    else:
        raise ValueError(shape)
    return a * x + b * h + c


def changed_rule(rule):
    a, b, c, shape = rule
    return (-a, -b, -c, SHAPES[(SHAPES.index(shape) + 3) % len(SHAPES)])


def observation(rule, point):
    x, u = point
    return {"x": x, "u": u, "y": reference(rule, x, u)}


def run_receiver(request, truths):
    started = time.perf_counter()
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    process.stdin.write(encoded(request) + "\n")
    process.stdin.flush()
    probes = 0
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(process.stderr.read())
        message = json.loads(line)
        if message["type"] == "result":
            break
        responses = []
        for row in message["rows"]:
            probe = row["probe"]
            obs = observation(truths[row["id"]], probe) if probe else None
            probes += probe is not None
            responses.append({"id": row["id"], "observation": obs})
        process.stdin.write(encoded({"rows": responses}) + "\n")
        process.stdin.flush()
    process.stdin.close()
    returncode = process.wait(timeout=30)
    if returncode:
        raise RuntimeError(process.stderr.read())
    message["elapsed_seconds"] = time.perf_counter() - started
    message["probes"] = probes
    message["input_bytes"] = len(encoded(request).encode())
    return message


def freeze(output):
    output.mkdir(parents=True, exist_ok=False)
    protocol = {
        "protocol": "governing-model-transfer-v1", "date": "2026-10-13",
        "private": True, "receiver": "fixed deterministic algorithm; no LLM calls",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "conditions": list(CONDITIONS), "hidden_systems": 384,
        "language": {"a": list(COEFFICIENTS), "b": list(COEFFICIENTS),
                     "c": [-1, 0, 1], "shapes": list(SHAPES)},
        "stages": list(STAGES), "probe_budget_per_stage": [0, 2, 0, 2],
        "probe_points": PROBES, "evaluation_points": EVALUATION,
        "domain": DOMAIN, "units": "dimensionless",
        "passive_observations": "24 fixed points at transfer; no passive samples at active discovery",
        "selection": "minimize largest prediction bucket; then maximize bucket count; lexical tie",
        "costs": "fit, probe scoring, and prediction relation evaluations; environment calls; elapsed time",
        "primary_metric": "all 110 unseen predictions correct after each active stage and transfer",
        "limits": ["Known finite model language", "No measurement noise", "One-step transitions",
                   "No LLM task performance or token/cost estimate", "No complete path landscape",
                   "JSON size is not a minimum-description-length score",
                   "Source revision changes are announced to all conditions",
                   "Cached notes preserve equivalent computational knowledge"],
    }
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    print(encoded({"status": "frozen", "output": str(output),
                   "protocol_sha256": digest(protocol)}))


def run(output):
    protocol = json.loads((output / "protocol.json").read_text())
    if protocol["source_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("Source changed after protocol freeze")
    rules = list(itertools.product(COEFFICIENTS, COEFFICIENTS, (-1, 0, 1), SHAPES))
    assert len(rules) == protocol["hidden_systems"]
    # Check the expression interpreter against independent arithmetic before trials.
    interpreter_checks = 0
    for rule in rules:
        expression = candidate(*rule)["expression"]
        for x, u in itertools.product(range(-8, 9), range(-5, 6)):
            assert evaluate(expression, x, u) == reference(rule, x, u)
            interpreter_checks += 1
    records, full_results = [], {}
    task_ids = [f"system-{index:03}" for index in range(len(rules))]
    originals = dict(zip(task_ids, rules))
    current = originals
    base_points = [(0, 0), (1, 1), (2, 1)]
    passive_points = [point for point in PROBES if point not in base_points][:24]
    before_change = None
    for condition in CONDITIONS:
        states = {task_id: {"id": task_id, "revision": "v1", "prior": None,
                           "observations": [observation(originals[task_id], p)
                                            for p in base_points]} for task_id in task_ids}
        full_results[condition] = {}
        current = originals
        for stage_index, stage in enumerate(STAGES):
            if stage == "source_change":
                current = {task_id: changed_rule(rule) for task_id, rule in originals.items()}
                if condition == "executable_transfer":
                    before_change = {task_id: state["prior"] for task_id, state in states.items()}
                for task_id, state in states.items():
                    state["revision"] = "v2"
                    state["observations"] = [observation(current[task_id], p) for p in base_points]
            if stage == "transfer":
                # Passive samples are fixed without reference to the hidden answer.
                # Duplicate sample occurrences are retained and counted as observations.
                for task_id, state in states.items():
                    state["observations"].extend(observation(current[task_id], p)
                                                 for p in passive_points)
            request = {"condition": condition, "rows": list(states.values()),
                       "probe_budget": protocol["probe_budget_per_stage"][stage_index]}
            result = run_receiver(request, current)
            correct = wrong = unknown = successes = 0
            counts, packet_bytes, evidence_bytes, traces = [], [], [], []
            for row in result["rows"]:
                task_correct = 0
                for point, prediction in zip(EVALUATION, row["predictions"]):
                    if prediction["status"] != "predicted":
                        unknown += 1
                    elif prediction["value"] == reference(current[row["id"]], *point):
                        correct += 1
                        task_correct += 1
                    else:
                        wrong += 1
                successes += task_correct == len(EVALUATION)
                counts.append(len(row["model"]["candidates"]))
                packet_bytes.append(len(encoded(row["transfer"]).encode()))
                evidence_bytes.append(len(encoded(row["observations"]).encode()))
                traces.append(row["model"]["reduction_trace"])
                states[row["id"]]["prior"] = row["transfer"]
                states[row["id"]]["observations"] = row["observations"]
            record = {"condition": condition, "stage": stage, "tasks": len(rules),
                      "complete_prediction_tasks": successes, "correct": correct,
                      "wrong": wrong, "unknown": unknown,
                      "coverage": (correct + wrong) / (len(rules) * len(EVALUATION)),
                      "retained_candidates_mean": statistics.mean(counts),
                      "ambiguous_models": sum(count > 1 for count in counts),
                      "transfer_packet_bytes_mean": statistics.mean(packet_bytes),
                      "current_revision_evidence_bytes_mean": statistics.mean(evidence_bytes),
                      "evidence_observations": sum(len(row["observations"]) for row in result["rows"]),
                      "environment_probe_calls": result["probes"],
                      "elapsed_seconds": result["elapsed_seconds"],
                      "receiver_input_bytes": result["input_bytes"], **result["cost"]}
            record["total_relation_evaluations"] = sum(record[key] for key in
                ("fit_relation_evaluations", "probe_relation_evaluations", "prediction_relation_evaluations"))
            records.append(record)
            full_results[condition][stage] = result
            print(encoded({"condition": condition, "stage": stage,
                           "complete_tasks": successes, "wrong": wrong,
                           "unknown": unknown}), flush=True)
    # The cache and executable model must produce identical decisions and predictions.
    equivalence_checks = 0
    for stage in STAGES:
        reference_rows = full_results["notes_replay"][stage]["rows"]
        for condition in CONDITIONS[1:]:
            rows = full_results[condition][stage]["rows"]
            for first, second in zip(reference_rows, rows):
                assert first["predictions"] == second["predictions"]
                assert first["observations"] == second["observations"]
                assert first["model"]["candidates"] == second["model"]["candidates"]
                equivalence_checks += 1
    diagnostics = {"interpreter_reference_checks": interpreter_checks,
                   "condition_equivalence_checks": equivalence_checks,
                   "outside_domain_blocked": 0, "wrong_type_blocked": 0,
                   "source_change_blocked": 0, "contradictions_blocked": 0,
                   "contradicted_models_preserved": 0,
                   "unguarded_stale_wrong_predictions": 0,
                   "unguarded_stale_total_predictions": 0}
    transferred = full_results["executable_transfer"]["transfer"]["rows"]
    for row in transferred:
        model = row["model"]
        for x, u in ((9, 0), (0, 6), (-9, -6)):
            assert predict(model, x, u, "v1")["reason"] == "outside_domain"
            diagnostics["outside_domain_blocked"] += 1
        assert predict(model, 1.5, 0, "v1")["reason"] == "input_type"
        diagnostics["wrong_type_blocked"] += 1
        assert predict(model, 1, 1, "v2")["reason"] == "source_changed"
        diagnostics["source_change_blocked"] += 1
        evidence = list(row["observations"])
        bad = dict(evidence[0])
        bad["y"] += 1
        contradictory = filter_rules(model["candidates"], [bad],
                                    {"fit_relation_evaluations": 0})
        assert not contradictory
        packet = packet_for(contradictory, "v1", evidence + [bad], [0], model["candidates"])
        assert predict(packet, 1, 0, "v1")["reason"] == "contradiction"
        assert packet["rejected_candidates"] == model["candidates"]
        diagnostics["contradictions_blocked"] += 1
        diagnostics["contradicted_models_preserved"] += 1
        for point in EVALUATION:
            unsafe = predict(before_change[row["id"]], *point, "v1")
            if unsafe["status"] == "predicted":
                diagnostics["unguarded_stale_total_predictions"] += 1
                diagnostics["unguarded_stale_wrong_predictions"] += (
                    unsafe["value"] != reference(changed_rule(originals[row["id"]]), *point))
    # Check compatibility with the actual Agent Memory save/reopen/read path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent_memory.core import Memory
    sample = transferred[0]["model"]
    with tempfile.TemporaryDirectory(prefix="governing-memory-check-") as directory:
        memory = Memory(directory)
        note_id = memory.save("Executable model transfer check", payload={"governing_model": sample})
        reopened = Memory(directory)
        saved = reopened.find(ids=[note_id], max_tokens=None)[0].payload["governing_model"]
        assert saved == sample
        assert predict(saved, -8, -5, "v1") == predict(sample, -8, -5, "v1")
    diagnostics["agent_memory_roundtrip"] = "passed"
    # Raw evidence remains separate. Revision changes do not delete the old records.
    evidence_rows = full_results["executable_transfer"]
    raw_store = {stage: [{"id": row["id"], "revision": row["model"]["revision"],
                         "observations": row["observations"]} for row in batch["rows"]]
                 for stage, batch in evidence_rows.items()}
    (output / "raw-evidence.json").write_text(encoded(raw_store) + "\n")
    (output / "receiver-records.json").write_text(encoded(full_results) + "\n")
    results = {"protocol_sha256": digest(protocol), "records": records,
               "diagnostics": diagnostics,
               "full_evidence_store_bytes": (output / "raw-evidence.json").stat().st_size,
               "outcome": "No prediction advantage over notes; retained computation lowers repeated fitting work."}
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in output.iterdir() if path.is_file()}
    (output / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(encoded({"status": "complete", "diagnostics": diagnostics,
                   "result": str(output / "results.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze", "run", "worker"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.operation == "worker":
        worker()
    elif args.output is None:
        parser.error("--output is required")
    elif args.operation == "freeze":
        freeze(args.output.resolve())
    else:
        run(args.output.resolve())
