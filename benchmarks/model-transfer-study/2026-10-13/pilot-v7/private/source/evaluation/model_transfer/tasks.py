"""Constructed engineering tasks and private, deterministic checks.

These are controlled work samples, not published scientific measurements or an
external benchmark. The task author also implements the reference functions;
controls and independent worker isolation do not imply independent authorship.
Nothing in this module is copied into the worker image.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

FAMILIES = ("state_transitions", "source_dependencies", "configuration_changes",
            "data_validation", "source_comparison", "calibration_limits",
            "conflicting_evidence", "revised_conclusions")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def equivalent(actual, expected):
    """Numeric JSON equality with Boolean type preserved and finite values only."""
    if type(actual) is bool or type(expected) is bool:
        return type(actual) is type(expected) and actual == expected
    if type(actual) in (int, float) and type(expected) in (int, float):
        return math.isfinite(actual) and math.isfinite(expected) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(equivalent(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(equivalent(a, e) for a, e in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def reference(family, request, policy, evidence=None):
    """The private oracle. Operates on inputs, not worker-authored test results."""
    if family == "state_transitions":
        start, end = request.get("from"), request.get("to")
        edge = policy["edges"].get(f"{start}>{end}")
        if edge is None:
            return {"ok": False, "value": None, "reason": "blocked"}
        if edge.get("role") and request.get("role") != edge["role"]:
            return {"ok": False, "value": None, "reason": "permission"}
        if edge.get("retry"):
            n = request.get("attempts")
            if type(n) is not int or not 0 <= n < policy["retry_limit"]:
                return {"ok": False, "value": None, "reason": "retry_limit"}
        return {"ok": True, "value": end, "reason": "ok"}
    if family == "source_dependencies":
        before, after = request.get("saved", {}), request.get("current", {})
        declared = request.get("declared", [])
        ignored = set(policy["ignored"])
        missing = {"kind": "missing"}
        changed = sorted({p for p in declared if p not in ignored and
                          canonical(before.get(p, missing)) != canonical(after.get(p, missing))})
        return {"needs_review": bool(changed), "changed": changed}
    if family == "configuration_changes":
        config = dict(policy["defaults"])
        for layer in request.get("layers", []):
            for key, value in layer.items():
                if key not in policy["types"]:
                    return {"ok": False, "config": {}, "error": "unknown_key"}
                if value is None and policy["null_resets"]:
                    config[key] = policy["defaults"][key]
                else:
                    config[key] = value
        for key, value in request.get("environment", {}).items():
            if not policy["environment"] or key not in policy["types"]:
                return {"ok": False, "config": {}, "error": "environment"}
            typ = policy["types"][key]
            try:
                if typ == "boolean":
                    if value not in ("true", "false"):
                        raise ValueError()
                    config[key] = value == "true"
                elif typ == "integer":
                    if not isinstance(value, str) or not value.lstrip("-").isdigit():
                        raise ValueError()
                    config[key] = int(value)
                else:
                    config[key] = value
            except (ValueError, AttributeError, TypeError):
                return {"ok": False, "config": {}, "error": "environment"}
        for key, typ in policy["types"].items():
            valid = {"boolean": type(config[key]) is bool,
                     "integer": type(config[key]) is int,
                     "string": type(config[key]) is str}[typ]
            if not valid:
                return {"ok": False, "config": {}, "error": "type"}
        return {"ok": True, "config": config, "error": None}
    if family == "data_validation":
        rows, result, errors, seen = request.get("rows", []), [], [], set()
        for i, row in enumerate(rows):
            label, amount = row.get("id"), row.get("amount")
            error = None
            if not isinstance(label, str) or not label:
                error = "id"
            elif type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
                error = "amount"
            elif row.get("unit", policy["base_unit"]) not in policy["units"]:
                error = "unit"
            elif policy["unique"] and label in seen:
                error = "duplicate"
            if error:
                errors.append({"row": i, "cause": error})
            else:
                seen.add(label)
                result.append({"id": label, "amount": amount * policy["units"][row.get("unit", policy["base_unit"])]})
        return {"ok": not errors, "records": [] if errors else result, "errors": errors}
    # Research: compare eligible source claims without inventing absent data.
    conditions = request.get("conditions", {})
    absent = sorted(k for k in policy["required_conditions"] if k not in conditions)
    if absent:
        return {"status": "unknown", "value": None, "sources": [], "unknowns": absent}
    candidates = [r for r in evidence or [] if r["claim"] == request["claim"]
                  and not r.get("withdrawn", False)
                  and (not policy["require_verified"] or r.get("verified", False))
                  and all(r.get("conditions", {}).get(k) == conditions[k]
                          for k in policy["required_conditions"])]
    superseded = {s for r in candidates for s in r.get("supersedes", [])}
    candidates = [r for r in candidates if r["source"] not in superseded]
    ids = sorted(r["source"] for r in candidates)
    if not candidates:
        return {"status": "unknown", "value": None, "sources": [], "unknowns": ["no matching evidence"]}
    values = {canonical(r["value"]) for r in candidates}
    if len(values) != 1:
        return {"status": "conflict", "value": None, "sources": ids, "unknowns": ["source disagreement"]}
    value = candidates[0]["value"]
    return {"status": "unsupported" if value is False else "supported", "value": value,
            "sources": ids, "unknowns": []}


def _inputs(family, variant, stage):
    """Produce a source update and both visible and unseen inputs."""
    p = {"variant": variant}
    e = None
    if family == "state_transitions":
        role = f"operator_{variant}"
        p.update(edges={"draft>ready": {}, "ready>running": {"role": role}, "running>done": {}}, retry_limit=2 + variant % 3)
        if stage >= 2:
            p["edges"]["failed>ready"] = {"retry": True}
        if stage == 3:
            p["edges"].update({"running>paused": {"role": role}, "paused>running": {"role": f"supervisor_{variant}"}})
        visible = [{"from": "draft", "to": "ready"}, {"from": "ready", "to": "running", "role": role}]
        hidden = [{"from": a, "to": b, "role": r, "attempts": n}
                  for a, b in (("ready", "running"), ("done", "ready"), ("failed", "ready"), ("paused", "running"), ("running", "paused"))
                  for r in (role, f"supervisor_{variant}", "outsider") for n in (0, p["retry_limit"], True)]
    elif family == "source_dependencies":
        names = [f"worker_{variant}.py", f"recipe_{variant}.json", f"new_{variant}.json"]
        p.update(ignored=[f"scratch_{variant}.txt"] if stage == 3 else [])
        missing, old, new = {"kind": "missing"}, {"kind": "file", "hash": "a" * 64}, {"kind": "file", "hash": "b" * 64}
        visible = [{"declared": names[:stage], "saved": {names[0]: old}, "current": {names[0]: old}}]
        hidden = []
        for name in names + [f"unseen_{variant}.cfg", f"scratch_{variant}.txt"]:
            for a, b in ((old, old), (old, new), (missing, old), (old, missing), (missing, missing)):
                hidden.append({"declared": [name, name], "saved": {name: a}, "current": {name: b, "irrelevant": new}})
        hidden += [{"declared": [names[0]], "saved": {}, "current": {names[0]: old}},
                   {"declared": [], "saved": {}, "current": {names[0]: new}}]
    elif family == "configuration_changes":
        p.update(defaults={"enabled": True, "retries": 3 + variant % 5, "label": f"job_{variant}"},
                 types={"enabled": "boolean", "retries": "integer", "label": "string"}, null_resets=stage >= 2, environment=stage == 3)
        visible = [{"layers": [{"enabled": False}, {"retries": 0}]}]
        hidden = [{"layers": layers, "environment": env}
                  for layers in ([], [{"enabled": False, "retries": 0, "label": ""}], [{"retries": None}], [{"retries": True}], [{"extra": 1}], [{"enabled": "false"}])
                  for env in ({}, {"enabled": "false"}, {"retries": "0"}, {"enabled": "FALSE"})]
    elif family == "data_validation":
        p.update(base_unit="nm", units={"nm": 1}, unique=stage == 3)
        if stage >= 2:
            p["units"]["um"] = 1000
        visible = [{"rows": [{"id": f"sample_{variant}", "amount": 4, "unit": "nm"}]}]
        hidden = [{"rows": rows} for rows in ([], [{"id": "a", "amount": 0}], [{"id": "a", "amount": False}],
                  [{"id": "a", "amount": -1}], [{"id": "", "amount": 1}], [{"id": "a", "amount": "5"}],
                  [{"id": "a", "amount": 1.25, "unit": "um"}], [{"id": "a", "amount": 1, "unit": "mm"}],
                  [{"id": "a", "amount": 1}, {"id": "a", "amount": 2}],
                  [{"id": "ok", "amount": 2}, {"id": "bad", "amount": -2}])]
    else:
        conditions = {"recipe": f"R{variant}", "material": "SiN", "regime": "dry"}
        p.update(required_conditions=["recipe", "material"] + (["regime"] if stage >= 2 else []), require_verified=stage >= 2)
        rate = 30 + variant % 11
        e = [{"source": "S1", "claim": "vertical_rate", "value": rate, "conditions": conditions, "verified": True},
             {"source": "U1", "claim": "vertical_rate", "value": 999, "conditions": dict(conditions, recipe="other"), "verified": True},
             {"source": "D1", "claim": "device_validated", "value": False, "conditions": conditions, "verified": True}]
        if family == "source_comparison" and stage >= 2:
            e.append({"source": "S2", "claim": "vertical_rate", "value": rate + 2, "conditions": conditions, "verified": True, "supersedes": ["S1"]})
        if family == "conflicting_evidence" and stage >= 2:
            e.append({"source": "X1", "claim": "vertical_rate", "value": rate + 9, "conditions": conditions, "verified": True})
        if family == "revised_conclusions" and stage == 3:
            e[0]["withdrawn"] = True
        if stage == 3:
            e.append({"source": "P1", "claim": "lateral_rate", "value": 0, "conditions": conditions, "verified": False})
        visible = [{"claim": "vertical_rate", "conditions": conditions}, {"claim": "device_validated", "conditions": conditions}]
        hidden = [{"claim": claim, "conditions": c}
                  for claim in ("vertical_rate", "lateral_rate", "device_validated", "loss_after_package")
                  for c in (conditions, {"recipe": conditions["recipe"]}, dict(conditions, recipe="other"), dict(conditions, regime="wet"))]
        # Probe new source names, independent conflicts, deletion, and source withdrawal.
        for altered in ([], [dict(e[0], source="unseen_source", withdrawn=False)],
                        [dict(e[0], source="A", withdrawn=False), dict(e[0], source="B", value=rate + 7, withdrawn=False)]):
            hidden.append({"claim": "vertical_rate", "conditions": conditions, "_evidence": altered})
    return p, e, visible, hidden


CONTRACTS = {
 "state_transitions": "Implement evaluate(request, policy). Return {ok, value, reason}. Look up from>to in policy.edges. Missing edges are blocked. Enforce each edge's role before retry checks. A retry needs an actual integer attempts (not bool), in [0, retry_limit). On success value is to and reason is ok. On failure value is null and reason is blocked, permission, or retry_limit. Do not modify inputs.",
 "source_dependencies": "Implement evaluate(request, policy). Return {needs_review, changed}. Compare canonical JSON source states for every unique declared path except policy.ignored. An absent state equals {kind:missing}. Compare complete states, not names or timestamps. Return sorted changed paths. Irrelevant changes do not require review. Do not modify inputs.",
 "configuration_changes": "Implement evaluate(request, policy). Start with policy.defaults and apply layers in order. Unknown keys fail with unknown_key. Null resets to defaults only when policy.null_resets is true. Environment overrides require policy.environment and known keys. Parse boolean only from exact true/false strings. Integer environment strings contain decimal digits with an optional leading minus, with no spaces, plus sign, decimal point, or exponent. Invalid boolean/integer environment values fail with environment. String environment values are copied unchanged. Check all value types only after all layers and environment overrides. Enforce exact policy types; booleans are not integers. Type failures use type. Return {ok, config, error}; failures have empty config, successes have null error. Preserve explicit false, zero, and empty strings.",
 "data_validation": "Implement evaluate(request, policy). Validate every row in input order, with at most one error per row. id must be a nonempty string. amount must be a finite nonnegative number, not bool. Unit defaults to base_unit and must be in policy.units. If policy.unique, reject IDs already accepted from an earlier row. Add an ID to the seen set only after that row passes every check. Error priority is id, amount, unit, duplicate. Return {ok, records, errors}; each error has exactly {row:index,cause}. Each accepted record has exactly {id:the_original_id,amount:the_input_amount_times_policy.units[unit]}. Do not include unit or other metadata in records. Any error makes the complete batch fail with records=[]; still collect errors for all rows. Otherwise ok=true and errors=[]. Do not modify inputs.",
 "research": "Implement evaluate(request, evidence, policy). Use only records whose claim matches, which are not withdrawn, and whose required conditions exactly match the request. If require_verified, use only verified records. Missing required request conditions yield unknown with their sorted names. Remove source IDs superseded by other eligible records. No eligible sources yield unknown with unknowns=['no matching evidence']. Disagreeing canonical JSON values yield conflict, null value, all eligible sorted source IDs, and unknowns=['source disagreement']. Agreement yields supported (unsupported for the Boolean false), that value, sorted source IDs, and empty unknowns. Return {status,value,sources,unknowns}. Do not treat absent calibration as zero. Do not treat source counts as authority. Do not modify inputs.",
}


def output_schema(family):
    """Public output contract. No private inputs or expected values are present."""
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    boolean = {"type": "boolean"}
    strings = {"type": "array", "items": {"type": "string"}}
    if family == "state_transitions":
        return obj({"ok": boolean, "value": {"type": ["string", "null"]},
                    "reason": {"enum": ["blocked", "permission", "retry_limit", "ok"]}})
    if family == "source_dependencies":
        return obj({"needs_review": boolean, "changed": strings})
    if family == "configuration_changes":
        return obj({"ok": boolean, "config": {"type": "object"},
                    "error": {"enum": [None, "unknown_key", "environment", "type"]}})
    if family == "data_validation":
        return obj({"ok": boolean,
                    "records": {"type": "array", "items": obj({"id": {"type": "string"}, "amount": {"type": "number"}})},
                    "errors": {"type": "array", "items": obj({"row": {"type": "integer", "minimum": 0},
                              "cause": {"enum": ["id", "amount", "unit", "duplicate"]}})}})
    return obj({"status": {"enum": ["unknown", "conflict", "supported", "unsupported"]},
                "value": {}, "sources": strings, "unknowns": strings})


def build_task(family, variant):
    stages = []
    for stage in range(1, 4):
        policy, evidence, visible, hidden = _inputs(family, variant, stage)
        files = {"policy.json": json.dumps(policy, indent=2), "requests.json": json.dumps(visible, indent=2),
                 "TASK.md": (f"Stage {stage}: {family}; work sample {variant}.\n" + CONTRACTS.get(family, CONTRACTS["research"]) +
                             "\nEvery result must follow OUTPUT_SCHEMA.json. Output objects have exactly the listed fields. Unknown research results have value=null and sources=[]. Use the API arguments supplied by the caller, including earlier policies and evidence; do not substitute current file globals. Write work.py with this API. Also write report.json as a JSON array of evaluate results for requests.json, using current policy.json and evidence.json when present. Keep prior supported behavior. Use actual local checks. Store findings and unresolved limits for the next agent.\n"),
                 "OUTPUT_SCHEMA.json": json.dumps(output_schema(family), indent=2)}
        if stage == 1:
            files["work.py"] = "# Complete the required API and report.\n"
        if evidence is not None:
            files["evidence.json"] = json.dumps(evidence, indent=2)
            files["SOURCES.md"] = "Constructed study records; not measured fabrication data.\n" + "\n".join(f"[{r['source']}] {canonical(r)}" for r in evidence)
        cases = []
        for request in visible + hidden:
            q = copy.deepcopy(request)
            actual_evidence = q.pop("_evidence", evidence)
            cases.append({"request": q, "evidence": actual_evidence,
                          "expected": reference(family, q, policy, actual_evidence)})
        stages.append({"stage": stage, "files": files, "policy": policy, "evidence": evidence,
                       "visible_count": len(visible), "cases": cases})
    return {"id": f"{family}-{variant:03d}", "family": family,
            "domain": "code" if family in FAMILIES[:4] else "research", "variant": variant,
            "stages": stages}


def task_pack(phase):
    if phase not in ("pilot", "confirmation"):
        raise ValueError("Unknown study phase")
    variants = [0] if phase == "pilot" else list(range(100, 112))
    return [build_task(family, v) for v in variants for family in FAMILIES]


def grade_outputs(stage, outputs, report):
    checks = [{"check": f"case_{i}", "passed": equivalent(actual, case["expected"])}
              for i, (actual, case) in enumerate(zip(outputs, stage["cases"]))]
    checks.append({"check": "all_cases_returned", "passed": len(outputs) == len(stage["cases"])})
    visible = [c["expected"] for c in stage["cases"][:stage["visible_count"]]]
    checks.append({"check": "current_source_report", "passed": equivalent(report, visible)})
    unsupported = sum(isinstance(a, dict) and a.get("status") == "supported" and
                      c["expected"].get("status") in ("unknown", "unsupported", "conflict")
                      for a, c in zip(outputs, stage["cases"]))
    missed_changes = sum(isinstance(a, dict) and a.get("needs_review") is False and
                        c["expected"].get("needs_review") is True for a, c in zip(outputs, stage["cases"]))
    return {"full_pass": all(c["passed"] for c in checks), "passed": sum(c["passed"] for c in checks),
            "total": len(checks), "checks": checks, "unsupported_conclusions": unsupported,
            "missed_source_changes": missed_changes}


def validate_controls(tasks):
    """Validate frozen cases and prove an incomplete/constant output fails."""
    rows = []
    for task in tasks:
        for s in task["stages"]:
            actual = [reference(task["family"], c["request"], s["policy"], c["evidence"]) for c in s["cases"]]
            report = actual[:s["visible_count"]]
            good = grade_outputs(s, actual, report)["full_pass"]
            bad = grade_outputs(s, [actual[0]] * len(actual), report)["full_pass"]
            missing = grade_outputs(s, [], None)["full_pass"]
            rows.append({"task": task["id"], "stage": s["stage"], "reference_pass": good,
                         "constant_control_rejected": not bad, "incomplete_control_rejected": not missing})
    if not all(r["reference_pass"] and r["constant_control_rejected"] and r["incomplete_control_rejected"] for r in rows):
        raise ValueError("Reference or incomplete control failed")
    return rows
