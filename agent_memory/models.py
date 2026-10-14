"""Git-backed task models with a small, pure rule interpreter.

Agents propose structure; this module checks syntax, replays evidence cases,
retains unresolved alternatives, and factors equivalent rules. A passing case
is a consistency check against supplied observations, not proof of a law.
No shell commands or generated Python run when a model is read or evaluated.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

KEY = "task_model"
EVENT = "model_event"
SCHEMA = 1
MAX_BYTES = 65536
MAX_NODES = 256
MAX_DEPTH = 16


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def union(*groups):
    return list(dict.fromkeys(item for group in groups for item in group))


class Unknown(ValueError):
    """An input or applicability condition is unavailable."""


def validate_expression(expr, *, depth=0, counter=None):
    counter = [0] if counter is None else counter
    counter[0] += 1
    if depth > MAX_DEPTH or counter[0] > MAX_NODES:
        raise ValueError("Expression exceeds the interpreter limit")
    if not isinstance(expr, dict):
        raise ValueError("An expression must be an object")
    if set(expr) == {"value"}:
        canonical(expr["value"])
        return
    if set(expr) == {"var"}:
        if not isinstance(expr["var"], str) or not expr["var"] or len(expr["var"]) > 120:
            raise ValueError("Invalid variable name")
        return
    op = expr.get("op")
    if op in ("all", "any"):
        if set(expr) != {"op", "items", "as", "test"}:
            raise ValueError("A collection rule needs items, as, and test")
        if not isinstance(expr["as"], str) or not expr["as"].isidentifier():
            raise ValueError("Invalid collection variable")
        children = [expr["items"], expr["test"]]
    else:
        arities = {"eq": 2, "ne": 2, "lt": 2, "le": 2, "gt": 2,
                   "ge": 2, "in": 2, "not": 1, "len": 1,
                   "add": 2, "sub": 2, "if": 3, "get": 3}
        if op not in (*arities, "and", "or") or set(expr) != {"op", "args"}:
            raise ValueError(f"Unsupported expression: {op!r}")
        children = expr["args"]
        if not isinstance(children, list) or not children:
            raise ValueError("args must be a nonempty list")
        if op in arities and len(children) != arities[op]:
            raise ValueError(f"Wrong argument count for {op}")
    for child in children:
        validate_expression(child, depth=depth + 1, counter=counter)


def evaluate(expr, context, *, budget=None):
    budget = [8192] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0:
        raise Unknown("Evaluation limit reached")
    if "value" in expr:
        return expr["value"]
    if "var" in expr:
        value = context
        for part in expr["var"].split("."):
            if not isinstance(value, dict) or part not in value:
                raise Unknown(f"Missing input: {expr['var']}")
            value = value[part]
        return value
    op = expr["op"]
    if op in ("all", "any"):
        items = evaluate(expr["items"], context, budget=budget)
        if not isinstance(items, list) or len(items) > 256:
            raise Unknown("Collection input must be a list of at most 256 items")
        values = [evaluate(expr["test"], {**context, expr["as"]: item}, budget=budget)
                  for item in items]
        if any(type(value) is not bool for value in values):
            raise Unknown("Collection predicate must return a Boolean")
        return all(values) if op == "all" else any(values)
    if op == "if":
        condition = evaluate(expr["args"][0], context, budget=budget)
        if type(condition) is not bool:
            raise Unknown("if needs a Boolean condition")
        return evaluate(expr["args"][1 if condition else 2], context, budget=budget)
    if op == "get":
        mapping = evaluate(expr["args"][0], context, budget=budget)
        key = evaluate(expr["args"][1], context, budget=budget)
        if not isinstance(mapping, dict) or not isinstance(key, str):
            raise Unknown("get needs an object and a string key")
        if key in mapping:
            return mapping[key]
        return evaluate(expr["args"][2], context, budget=budget)
    values = [evaluate(arg, context, budget=budget) for arg in expr["args"]]
    if op in ("and", "or", "not"):
        if any(type(value) is not bool for value in values):
            raise Unknown("Boolean operators need Boolean inputs")
        return all(values) if op == "and" else (any(values) if op == "or" else not values[0])
    if op in ("eq", "ne"):
        equal = canonical(values[0]) == canonical(values[1])
        return equal if op == "eq" else not equal
    if op == "in":
        if not isinstance(values[1], (list, str, dict)):
            raise Unknown("in needs a collection")
        return values[0] in values[1]
    if op == "len":
        if not isinstance(values[0], (list, str, dict)):
            raise Unknown("len needs a collection")
        return len(values[0])
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise Unknown("Numeric operators need finite numbers")
    if op == "add":
        return values[0] + values[1]
    if op == "sub":
        return values[0] - values[1]
    return {"lt": lambda: values[0] < values[1], "le": lambda: values[0] <= values[1],
            "gt": lambda: values[0] > values[1], "ge": lambda: values[0] >= values[1]}[op]()


def normalize(expr):
    """Pure factoring: flatten and deduplicate Boolean operands, not facts."""
    if "op" not in expr:
        return copy.deepcopy(expr)
    if expr["op"] in ("all", "any"):
        return {**expr, "items": normalize(expr["items"]), "test": normalize(expr["test"])}
    args = [normalize(arg) for arg in expr["args"]]
    if expr["op"] in ("and", "or"):
        flat = []
        for arg in args:
            flat.extend(arg["args"] if arg.get("op") == expr["op"] else [arg])
        unique = {canonical(arg): arg for arg in flat}
        args = [unique[key] for key in sorted(unique)]
        if len(args) == 1:
            return args[0]
    return {"op": expr["op"], "args": args}


def variables(expr, bound=()):
    if "var" in expr:
        root = expr["var"].split(".")[0]
        return set() if root in bound else {root}
    if "value" in expr:
        return set()
    if expr["op"] in ("all", "any"):
        return variables(expr["items"], bound) | variables(expr["test"], (*bound, expr["as"]))
    return set().union(*(variables(arg, bound) for arg in expr["args"]))


def reduce_structure(model):
    result = copy.deepcopy(model)
    groups = {}
    for rule in result["rules"]:
        rule["when"] = normalize(rule["when"])
        rule["then"] = normalize(rule["then"])
        key = canonical([rule["target"], rule["then"]])
        if key not in groups:
            groups[key] = rule
            continue
        first = groups[key]
        first["claim"] = f"Shared rule for {first['target']}; read retained conditions."
        first["when"] = normalize({"op": "or", "args": [first["when"], rule["when"]]})
        first["evidence"] = union(first["evidence"], rule["evidence"])
        first["dependencies"] = union(first["dependencies"], rule["dependencies"])
    rules = list(groups.values())
    removed = set()
    for i, first in enumerate(rules):
        if i in removed or set(first["then"]) != {"value"} or type(first["then"]["value"]) is not bool:
            continue
        for j in range(i + 1, len(rules)):
            second = rules[j]
            if j in removed or second["target"] != first["target"] or set(second["then"]) != {"value"}:
                continue
            if type(second["then"]["value"]) is not bool or second["then"]["value"] == first["then"]["value"]:
                continue
            negated = lambda condition: {"op": "not", "args": [condition]}
            if canonical(second["when"]) == canonical(negated(first["when"])):
                predicate, yes, no = first["when"], first["then"], second["then"]
            elif canonical(first["when"]) == canonical(negated(second["when"])):
                predicate, yes, no = second["when"], second["then"], first["then"]
            else:
                continue
            effect = {"op": "if", "args": [predicate, yes, no]}
            try:
                validate_expression(effect)
            except ValueError:
                continue
            # if preserves the Boolean type check of both original conditions.
            first["when"], first["then"] = {"value": True}, effect
            first["claim"] = f"Boolean decision for {first['target']}; read the retained predicate."
            first["evidence"] = union(first["evidence"], second["evidence"])
            first["dependencies"] = union(first["dependencies"], second["dependencies"])
            removed.add(j)
            break
    result["rules"] = [rule for i, rule in enumerate(rules) if i not in removed]
    return result


def complexity(model):
    # Evidence and old cases are retained but excluded from the working-rule size.
    working = {key: model[key] for key in ("inputs", "rules", "alternatives")}
    return {"rules": len(model["rules"]), "working_bytes": len(canonical(working).encode()),
            "complete_bytes": len(canonical(model).encode()), "cases": len(model["cases"])}


def _string(value, label, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    return value


def _strings(values, label):
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError(f"Invalid {label}")
    return union([_string(value, label) for value in values])


def validate_model(raw):
    if not isinstance(raw, dict):
        raise ValueError("model must be an object")
    model = copy.deepcopy(raw)
    if set(model) - {"schema", "title", "inputs", "rules", "cases", "alternatives", "unresolved", "resolutions"}:
        raise ValueError("Unknown model field")
    model["schema"] = model.get("schema", SCHEMA)
    if model["schema"] != SCHEMA:
        raise ValueError("Unsupported model schema")
    _string(model.get("title"), "model title", 100)
    inputs = model.get("inputs")
    if not isinstance(inputs, dict) or len(inputs) > 32:
        raise ValueError("inputs must declare at most 32 variables")
    for name, kind in inputs.items():
        if not name.isidentifier() or kind not in ("string", "number", "integer", "boolean", "array", "object"):
            raise ValueError("Invalid input type declaration")
    model.setdefault("alternatives", [])
    model.setdefault("unresolved", [])
    model.setdefault("resolutions", [])
    model["unresolved"] = _strings(model["unresolved"], "unresolved result")
    if not isinstance(model["resolutions"], list) or len(model["resolutions"]) > 256:
        raise ValueError("Invalid resolutions")
    resolved_results = set()
    for resolution in model["resolutions"]:
        if not isinstance(resolution, dict) or set(resolution) != {"result", "reason", "evidence"}:
            raise ValueError("A resolution needs result, reason, and evidence")
        if resolution["result"] not in model["unresolved"] or resolution["result"] in resolved_results:
            raise ValueError("A resolution must name a retained unresolved result")
        resolved_results.add(resolution["result"])
        _string(resolution["reason"], "resolution reason")
        resolution["evidence"] = _strings(resolution["evidence"], "resolution evidence")
        if not resolution["evidence"]:
            raise ValueError("A resolution needs evidence")

    def rules_valid(rules):
        if not isinstance(rules, list) or not rules or len(rules) > 64:
            raise ValueError("rules must contain between 1 and 64 rules")
        ids = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {"id", "target", "claim", "when", "then", "evidence", "dependencies"}:
                raise ValueError("Invalid rule fields")
            _string(rule.get("id"), "rule id", 80)
            if rule["id"] in ids:
                raise ValueError("Duplicate rule id")
            ids.add(rule["id"])
            _string(rule.get("target"), "target", 80)
            _string(rule.get("claim"), "claim", 240)
            rule.setdefault("when", {"value": True})
            validate_expression(rule["when"])
            validate_expression(rule.get("then"))
            if (variables(rule["when"]) | variables(rule["then"])) - inputs.keys():
                raise ValueError("A rule references an undeclared input")
            rule["evidence"] = _strings(rule.get("evidence", []), "evidence id")
            rule["dependencies"] = _strings(rule.get("dependencies", []), "dependency")
    rules_valid(model.get("rules"))
    if not isinstance(model["alternatives"], list) or len(model["alternatives"]) > 16:
        raise ValueError("Invalid alternatives")
    ids = set()
    for alternative in model["alternatives"]:
        if not isinstance(alternative, dict) or set(alternative) != {"id", "claim", "rules"}:
            raise ValueError("An alternative needs id, claim, and rules")
        _string(alternative["id"], "alternative id", 80)
        _string(alternative["claim"], "alternative claim")
        if alternative["id"] in ids:
            raise ValueError("Duplicate alternative id")
        ids.add(alternative["id"])
        rules_valid(alternative["rules"])
    cases = model.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > 256:
        raise ValueError("cases must contain between 1 and 256 evidence cases")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "target", "inputs", "expected", "evidence", "challenge"}:
            raise ValueError("Invalid case fields")
        _string(case.get("id"), "case id", 80)
        _string(case.get("target"), "case target", 80)
        if case["id"] in ids or "expected" not in case:
            raise ValueError("Duplicate case id or missing expected result")
        ids.add(case["id"])
        check_inputs(model, case.get("inputs"))
        case["evidence"] = _strings(case.get("evidence", []), "case evidence")
        if not case["evidence"]:
            raise ValueError("Each case needs a source evidence note")
        case.setdefault("challenge", False)
        if type(case["challenge"]) is not bool:
            raise ValueError("challenge must be a Boolean")
    if len(canonical(model).encode()) > MAX_BYTES:
        raise ValueError("Model exceeds 64 KiB; separate unrelated task models")
    return model


def check_inputs(model, inputs):
    if not isinstance(inputs, dict) or not model["inputs"].keys() <= inputs.keys():
        raise Unknown("Inputs must include the declared model variables")
    for name, kind in model["inputs"].items():
        value = inputs[name]
        good = {"string": type(value) is str, "boolean": type(value) is bool,
                "number": type(value) in (int, float), "integer": type(value) is int,
                "array": type(value) is list, "object": type(value) is dict}[kind]
        if not good:
            raise Unknown(f"Wrong input type for {name}")
    try:
        if len(canonical(inputs).encode()) > MAX_BYTES:
            raise Unknown("Input exceeds 64 KiB")
    except (ValueError, TypeError) as error:
        raise Unknown("Inputs must contain finite JSON values") from error


def prediction(rules, inputs, target):
    values, used = {}, []
    budget = [8192]
    try:
        for rule in rules:
            if rule["target"] != target:
                continue
            applies = evaluate(rule["when"], inputs, budget=budget)
            if type(applies) is not bool:
                raise Unknown("Rule condition must return a Boolean")
            if applies:
                value = evaluate(rule["then"], inputs, budget=budget)
                values[canonical(value)] = value
                used.append(rule["id"])
    except (Unknown, TypeError, OverflowError, ValueError) as error:
        return {"status": "unknown", "reason": str(error), "rules": used}
    if not values:
        return {"status": "unknown", "reason": "No applicable rule", "rules": []}
    if len(values) > 1:
        return {"status": "ambiguous", "values": list(values.values()), "rules": used}
    return {"status": "predicted", "value": next(iter(values.values())), "rules": used}


def latest(items):
    return next((item for item in reversed(items) if item.payload.get(EVENT) == "accepted"), None)


def compact_checks(report):
    result = {key: value for key, value in report.items() if key != "cases"}
    if "cases" in report:
        failed = [case for case in report["cases"] if not case["pass"]]
        result.update({"passed": len(report["cases"]) - len(failed), "total": len(report["cases"]),
                       "failed_cases": failed[:16], "omitted_failed_cases": max(0, len(failed) - 16)})
    return result


def compact_handoff(report):
    """Working result; full operation arguments remain available in the audit."""
    result = copy.deepcopy(report)
    if "checks" in result:
        result["checks"] = compact_checks(result["checks"])
    for operation in result.get("operations", []):
        arguments = operation.get("arguments", {})
        if "model" in arguments:
            model = arguments.pop("model")
            arguments["model_sha256"] = hashlib.sha256(canonical(model).encode()).hexdigest()
        if operation["action"] == "model_check" and operation.get("ok"):
            operation["result"] = compact_checks(operation["result"])
        if operation["action"] == "model_reduce" and operation.get("ok") and "checks" in operation["result"]:
            operation["result"]["checks"] = compact_checks(operation["result"]["checks"])
    return result


def fingerprint(root, name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Dependencies must be relative paths inside the workspace")
    if path.parts[0] in (".agent_memory", ".git", ".env"):
        raise ValueError("Memory internals and credentials cannot be dependencies")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Dependency leaves the workspace")
    if not resolved.exists():
        return {"state": "missing"}
    if not resolved.is_file():
        raise ValueError("Dependencies must name individual files")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return {"state": "file", "sha256": digest.hexdigest()}


def sources(model):
    rules = model["rules"] + [rule for alt in model["alternatives"] for rule in alt["rules"]]
    return union(*(rule["evidence"] for rule in rules), *(case["evidence"] for case in model["cases"]),
                 *(resolution["evidence"] for resolution in model["resolutions"]))


def dependencies(model):
    return union(*(rule["dependencies"] for rule in model["rules"]),
                 *(rule["dependencies"] for alt in model["alternatives"] for rule in alt["rules"]))


class TaskModels:
    def __init__(self, memory):
        self.memory = memory

    def _visible(self, ids=None):
        """Folded evidence remains visible; later branch effects do not enter inspection."""
        inspecting = self.memory.inspecting()
        if inspecting:
            from agent_memory.core import MemoryItem
            found = {}
            for record in self.memory._read_records([inspecting["id"]], []):
                if record["kind"] in ("delete", "supersede"):
                    found.pop(record["target_id"], None)
                if record["kind"] != "delete":
                    found[record["id"]] = MemoryItem(record["id"], record["message"],
                                                    record["payload"], record["datetime"])
            return [item for item in found.values() if ids is None or item.id in ids]
        self.memory._sync_catalog()
        rows = self.memory._catalog.select(branch=self.memory._home_branch(), ids=ids)
        return [self.memory._row_item(row) for row in reversed(rows)]

    def get(self, *, id=None, items=None):
        if id:
            full_id = self.memory._resolve(id)
            item = next(iter(self._visible([full_id])), None) or self.memory._item(id)
        elif self.memory.inspecting():
            item = latest(self._visible())
        else:
            self.memory._sync_catalog()
            rows = self.memory._catalog.select(branch=self.memory._home_branch(),
                payload={EVENT: "accepted"}, limit=1, sequence_order=True)
            item = self.memory._row_item(rows[0]) if rows else None
        if item is None:
            return None
        if item.payload.get(EVENT) not in ("proposed", "accepted"):
            raise ValueError("Entry is not a task model")
        return {"id": item.id, "event": item.payload[EVENT], **copy.deepcopy(item.payload[KEY])}

    def export(self, *, id=None):
        record = self.get(id=id)
        if record is None:
            return None
        # The interpreter retains fixtures in Git. Each agent receives the
        # working rules first and requests full=True only to inspect fixtures.
        return {"id": record["id"], "event": record["event"], "parent": record["parent"],
                "revision": record["revision"],
                "model": {key: value for key, value in record["model"].items() if key != "cases"},
                "case_count": len(record["model"]["cases"]), "complexity": record["complexity"],
                "checks": compact_checks(self.check(id=record["id"]))}

    def propose(self, model, *, base=None, agent=None, reason="Agent proposed a model revision"):
        head = self.memory._run("rev-parse", "HEAD", check=False)
        active = self.get()
        actual_base = active["id"] if active else None
        if active and base is None:
            raise ValueError("base is required when revising an active model; use model_get")
        if base is not None and self.memory._resolve(base) != actual_base:
            raise ValueError("Model base changed; read the current model before revising it")
        raw = copy.deepcopy(model)
        if active:
            previous = active["model"]
            raw.setdefault("alternatives", copy.deepcopy(previous["alternatives"]))
            raw.setdefault("unresolved", list(previous["unresolved"]))
            raw.setdefault("resolutions", copy.deepcopy(previous["resolutions"]))
            # Omitting old cases cannot make a failing reduction appear correct.
            new_cases = {case["id"]: case for case in raw.get("cases", [])}
            for case in previous["cases"]:
                if case["id"] in new_cases:
                    supplied = {**new_cases[case["id"]], "challenge": new_cases[case["id"]].get("challenge", False)}
                    if canonical(supplied) != canonical(case):
                        raise ValueError(f"Recorded case {case['id']} cannot be rewritten")
                else:
                    new_cases[case["id"]] = copy.deepcopy(case)
            raw["cases"] = list(new_cases.values())
            old_alts = {alt["id"]: alt for alt in previous["alternatives"]}
            new_alts = {alt["id"]: alt for alt in raw["alternatives"]}
            if any(key not in new_alts or canonical(new_alts[key]) != canonical(value)
                   for key, value in old_alts.items()):
                raise ValueError("Retain alternatives; evidence cases can reject them")
            if not set(previous["unresolved"]).issubset(raw["unresolved"]):
                raise ValueError("Retain unresolved results in this model lineage")
            if any(resolution not in raw["resolutions"] for resolution in previous["resolutions"]):
                raise ValueError("Retain prior resolutions and their evidence")
        model = validate_model(raw)
        evidence = sources(model)
        resolved = {note_id: self.memory._resolve(note_id) for note_id in evidence}
        source_items = self._visible(list(resolved.values()))
        allowed = {item.id for item in source_items if not item.payload.get(EVENT)}
        if any(note_id not in allowed for note_id in resolved.values()):
            raise ValueError("Evidence must be a visible note on the current branch")
        for rule in model["rules"] + [rule for alt in model["alternatives"] for rule in alt["rules"]]:
            rule["evidence"] = [resolved[note_id] for note_id in rule["evidence"]]
        for case in model["cases"]:
            case["evidence"] = [resolved[note_id] for note_id in case["evidence"]]
        for resolution in model["resolutions"]:
            resolution["evidence"] = [resolved[note_id] for note_id in resolution["evidence"]]
        # Explicit evidence dependencies also bind the candidate to its sources.
        by_id = {item.id: item for item in source_items}
        declared = union(dependencies(model), *(by_id[note_id].payload.get("files", [])
                                               for note_id in resolved.values()))
        captured = {name: fingerprint(self.memory._path, name) for name in declared}
        if active:
            changed = {name for name, saved in active["fingerprints"].items()
                       if captured.get(name) != saved}
            if changed:
                old_ids = {case["id"] for case in active["model"]["cases"]}
                new_notes = set(self.memory._run("rev-list", f"{active['id']}..{head}").splitlines())
                fresh_challenges = [case for case in model["cases"] if case["id"] not in old_ids
                    and case["challenge"] and set(case["evidence"]) & new_notes]
                if not fresh_challenges:
                    raise ValueError("Changed sources require a new challenge case with fresh test evidence")
        record = {"model": model, "parent": actual_base,
                  "revision": active["revision"] + 1 if active else 1,
                  "fingerprints": captured, "reason": reason, "complexity": complexity(model)}
        return self.memory.save(f"Task model proposed: {model['title']}",
                                {EVENT: "proposed", KEY: record}, agent=agent,
                                _expected_head=head, _model_record=True)

    def check(self, *, id=None, items=None):
        record = self.get(id=id, items=items)
        if record is None:
            return {"status": "absent"}
        model = record["model"]
        evidence_ids = sources(model)
        visible = {item.id: item for item in self._visible(evidence_ids)}
        missing = [note_id for note_id in evidence_ids if note_id not in visible]
        # Observations remain historical evidence when the observed system changes.
        # Applicability is bound to the proposal's declared source fingerprints.
        # Hiding/deleting a cited note still blocks use of its dependent model.
        changed = []
        for name, saved in record["fingerprints"].items():
            try:
                if fingerprint(self.memory._path, name) != saved:
                    changed.append(name)
            except (OSError, ValueError):
                changed.append(name)
        cases, coverage = [], set()
        for case in model["cases"]:
            result = prediction(model["rules"], case["inputs"], case["target"])
            passed = result["status"] == "predicted" and canonical(result["value"]) == canonical(case["expected"])
            if passed:
                coverage.update(result["rules"])
            cases.append({"id": case["id"], "pass": passed, "result": result,
                          "expected": case["expected"], "challenge": case["challenge"]})
        uncovered = [rule["id"] for rule in model["rules"] if rule["id"] not in coverage]
        alternatives = []
        for alternative in model["alternatives"]:
            rejected = []
            for case in model["cases"]:
                result = prediction(alternative["rules"], case["inputs"], case["target"])
                if result["status"] == "predicted" and canonical(result["value"]) != canonical(case["expected"]):
                    rejected.append(case["id"])
            alternatives.append({"id": alternative["id"], "status": "rejected" if rejected else "unseparated",
                                 "counterexamples": rejected})
        issues = []
        if missing or changed:
            issues.append("Evidence or dependency versions changed")
        if any(not case["pass"] for case in cases):
            issues.append("Recorded case failed")
        if uncovered:
            issues.append("A rule has no passing evidence case")
        targets = {rule["target"] for rule in model["rules"]}
        challenges = {case["target"] for case in model["cases"] if case["challenge"]}
        if targets - challenges:
            issues.append("Each prediction target needs a challenge case")
        resolved_results = {resolution["result"] for resolution in model["resolutions"]}
        return {"id": record["id"], "status": "blocked" if issues else "tested",
                "issues": issues, "cases": cases, "uncovered_rules": uncovered,
                "missing_evidence": missing,
                "changed_dependencies": changed, "alternatives": alternatives,
                "complexity": record["complexity"],
                "unresolved": [text for text in model["unresolved"] if text not in resolved_results]}

    def accept(self, id, *, agent=None):
        head = self.memory._run("rev-parse", "HEAD")
        proposal = self.get(id=id)
        if not self._visible([proposal["id"]]):
            raise ValueError("Proposal is not visible on the current branch")
        if proposal["event"] != "proposed":
            raise ValueError("Only a proposal can be accepted")
        active = self.get()
        if proposal["parent"] != (active["id"] if active else None):
            raise ValueError("Model base changed; revise the proposal against the current model")
        report = self.check(id=id)
        if report["status"] != "tested":
            raise ValueError("Model is blocked: " + "; ".join(report["issues"]))
        record = {key: copy.deepcopy(value) for key, value in proposal.items() if key not in ("id", "event")}
        record["proposal"] = proposal["id"]
        record["checks"] = report
        return self.memory.save(f"Task model active: {record['model']['title']}",
                                {EVENT: "accepted", KEY: record}, agent=agent,
                                _expected_head=head, _model_record=True)

    def reduce(self, *, agent=None):
        active = self.get()
        if active is None:
            raise ValueError("No active model to reduce")
        report = self.check(id=active["id"])
        if report["status"] != "tested":
            return {"status": "blocked", "id": active["id"], "checks": report}
        reduced = reduce_structure(active["model"])
        if canonical(reduced) == canonical(active["model"]):
            return {"status": "unchanged", "id": active["id"], "complexity": active["complexity"]}
        if complexity(reduced)["working_bytes"] >= active["complexity"]["working_bytes"]:
            return {"status": "unchanged", "id": active["id"], "reason": "Factoring did not reduce working size"}
        proposal = self.propose(reduced, base=active["id"], agent=agent,
                                reason="Factor shared effects and repeated Boolean expressions")
        return {"status": "proposed", "id": proposal, "before": active["complexity"],
                "after": complexity(reduced), "checks": self.check(id=proposal)}

    def handoff(self, model=None, *, base=None, agent=None):
        """Check, accept, and reduce a model without another agent decision.

        Omit model to reuse a currently valid model. Every public operation is
        retained, including a failed operation. Evidence is still supplied by
        the caller; this workflow creates no observations or reference answers.
        """
        operations = []

        def step(action, arguments, function):
            try:
                result = function()
            except Exception as error:
                operations.append({"action": action, "arguments": arguments,
                                   "ok": False, "error": f"{type(error).__name__}: {error}"})
                raise
            operations.append({"action": action, "arguments": arguments, "ok": True, "result": result})
            return result

        try:
            if isinstance(model, dict) and set(model) == {"observations"}:
                active = self.get()
                if active is None:
                    raise ValueError("Observations require an active model")
                observations = model["observations"]
                if not isinstance(observations, list) or not observations:
                    raise ValueError("Observations must be a nonempty list of recorded cases")
                updated = copy.deepcopy(active["model"])
                updated["cases"] = copy.deepcopy(observations)
                model = updated
            if model is not None:
                proposal = step("model_propose", {"base": base, "model": model},
                                lambda: self.propose(model, base=base, agent=agent))
            else:
                active = self.get()
                if active is None:
                    raise ValueError("No active model to reuse")
                proposal = active["id"]
            checks = step("model_check", {"id": proposal}, lambda: self.check(id=proposal))
            if checks["status"] != "tested":
                return {"status": "blocked", "checks": checks, "operations": operations}
            if model is not None:
                step("model_accept", {"id": proposal}, lambda: self.accept(proposal, agent=agent))
            reduction = step("model_reduce", {}, lambda: self.reduce(agent=agent))
            if reduction["status"] == "proposed":
                reduced = reduction["id"]
                checks = step("model_check", {"id": reduced}, lambda: self.check(id=reduced))
                if checks["status"] != "tested":
                    return {"status": "blocked", "checks": checks, "operations": operations}
                step("model_accept", {"id": reduced}, lambda: self.accept(reduced, agent=agent))
            elif reduction["status"] == "blocked":
                return {"status": "blocked", "checks": reduction["checks"], "operations": operations}
            active = self.get()
            case = next(c for c in active["model"]["cases"] if c["challenge"])
            predicted = step("model_predict", {"target": case["target"], "inputs": case["inputs"]},
                             lambda: self.predict(case["inputs"], case["target"]))
            passed = predicted["status"] == "predicted" and canonical(predicted["value"]) == canonical(case["expected"])
            return {"status": "tested" if passed else "blocked", "id": active["id"],
                    "prediction_case": case["id"], "operations": operations}
        except Exception as error:
            return {"status": "blocked", "error": f"{type(error).__name__}: {error}", "operations": operations}

    def predict(self, inputs, target, *, id=None):
        record = self.get(id=id)
        if record is None:
            return {"status": "unknown", "reason": "No active model"}
        if record["event"] != "accepted":
            return {"status": "unknown", "reason": "Model is a proposal"}
        report = self.check(id=record["id"])
        if report["status"] != "tested":
            return {"status": "unknown", "reason": "Model needs review", "checks": compact_checks(report)}
        try:
            check_inputs(record["model"], inputs)
        except Unknown as error:
            return {"status": "unknown", "reason": str(error)}
        result = prediction(record["model"]["rules"], inputs, target)
        rejected = {alt["id"] for alt in report["alternatives"] if alt["status"] == "rejected"}
        for alternative in record["model"]["alternatives"]:
            if alternative["id"] in rejected:
                continue
            other = prediction(alternative["rules"], inputs, target)
            if other["status"] == "ambiguous" or (other["status"] == "predicted" and
                    (result["status"] != "predicted" or canonical(other["value"]) != canonical(result["value"]))):
                return {"status": "ambiguous", "reason": "An unresolved alternative differs",
                        "model": result, "alternative": alternative["id"], "prediction": other}
            if other["status"] == "unknown" and other["reason"] != "No applicable rule":
                return {"status": "unknown", "reason": "An alternative could not be evaluated"}
        return {"model_id": record["id"], **result}

    def view(self, *, items=None):
        record = self.get(items=items)
        if record is None:
            return None
        report = self.check(id=record["id"], items=items)
        covered = sources(record["model"]) if report["status"] == "tested" else []
        return {"id": record["id"], "revision": record["revision"], "status": report["status"],
                "rules": [{"id": rule["id"], "claim": rule["claim"]} for rule in record["model"]["rules"]],
                "covered": covered, "alternatives": sum(alt["status"] == "unseparated" for alt in report["alternatives"]),
                "unresolved": len(report["unresolved"]), "issues": report["issues"],
                "complexity": report["complexity"]}
