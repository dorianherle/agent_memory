"""Git-backed task models with a small, pure rule interpreter.

Agents propose structure; this module checks syntax, replays evidence cases,
retains unresolved alternatives, and factors equivalent rules. A passing case
is a consistency check against supplied observations, not proof of a law.
No shell commands or generated Python run when a model is read or evaluated.
"""
from __future__ import annotations

import ast
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
_BOOL_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "not", "and", "or", "all", "any")
_CMP_OPS = {
    ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "lt", ast.LtE: "le",
    ast.Gt: "gt", ast.GtE: "ge", ast.In: "in",
}
_BINARY_SYMBOLS = {
    "eq": "==", "ne": "!=", "lt": "<", "le": "<=",
    "gt": ">", "ge": ">=", "in": "in", "add": "+", "sub": "-",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def union(*groups):
    return list(dict.fromkeys(item for group in groups for item in group))


class Unknown(ValueError):
    """An input or applicability condition is unavailable."""


def _dotted_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and node.attr.isidentifier():
        prefix = _dotted_name(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
    return None


def _from_ast(node, *, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("Expression exceeds the interpreter limit")
    if isinstance(node, ast.Constant):
        if type(node.value) not in (bool, int, float, str, type(None)):
            raise ValueError("Unsupported constant in formula")
        canonical(node.value)
        return {"value": node.value}
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_from_ast(el, depth=depth + 1) for el in node.elts]
        if any(set(item) != {"value"} for item in items):
            raise ValueError("List literals in formulas must contain constants")
        return {"value": [item["value"] for item in items]}
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):
            raise ValueError("Dict unpacking is not supported")
        keys = [_from_ast(k, depth=depth + 1) for k in node.keys]
        vals = [_from_ast(v, depth=depth + 1) for v in node.values]
        if any(set(k) != {"value"} or not isinstance(k["value"], str) for k in keys) or any(
            set(v) != {"value"} for v in vals
        ):
            raise ValueError("Dict literals must use constant string keys and constant values")
        return {"value": {k["value"]: v["value"] for k, v in zip(keys, vals)}}
    if isinstance(node, ast.Name):
        if node.id in ("true", "false", "null"):
            return {"value": {"true": True, "false": False, "null": None}[node.id]}
        return {"var": node.id}
    if isinstance(node, ast.Attribute):
        dotted = _dotted_name(node)
        if dotted:
            return {"var": dotted}
        raise ValueError("Attribute access is only supported on variable names")
    if isinstance(node, ast.BoolOp):
        op = "and" if isinstance(node.op, ast.And) else "or"
        return {"op": op, "args": [_from_ast(v, depth=depth + 1) for v in node.values]}
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return {"op": "not", "args": [_from_ast(node.operand, depth=depth + 1)]}
        if isinstance(node.op, ast.USub):
            inner = _from_ast(node.operand, depth=depth + 1)
            if set(inner) == {"value"} and type(inner["value"]) in (int, float):
                canonical(-inner["value"])
                return {"value": -inner["value"]}
            return {"op": "sub", "args": [{"value": 0}, inner]}
        raise ValueError("Unsupported unary operator")
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            return {"op": "add", "args": [_from_ast(node.left, depth=depth + 1), _from_ast(node.right, depth=depth + 1)]}
        if isinstance(node.op, ast.Sub):
            return {"op": "sub", "args": [_from_ast(node.left, depth=depth + 1), _from_ast(node.right, depth=depth + 1)]}
        raise ValueError("Unsupported binary operator")
    if isinstance(node, ast.Compare):
        parts = []
        left = _from_ast(node.left, depth=depth + 1)
        for op_node, right_node in zip(node.ops, node.comparators):
            right = _from_ast(right_node, depth=depth + 1)
            if isinstance(op_node, ast.NotIn):
                parts.append({"op": "not", "args": [{"op": "in", "args": [left, right]}]})
            elif type(op_node) in _CMP_OPS:
                parts.append({"op": _CMP_OPS[type(op_node)], "args": [left, right]})
            else:
                raise ValueError(f"Unsupported comparison operator: {type(op_node).__name__}")
            left = right
        return parts[0] if len(parts) == 1 else {"op": "and", "args": parts}
    if isinstance(node, ast.IfExp):
        return {"op": "if", "args": [
            _from_ast(node.test, depth=depth + 1),
            _from_ast(node.body, depth=depth + 1),
            _from_ast(node.orelse, depth=depth + 1),
        ]}
    if isinstance(node, ast.Call):
        if node.keywords:
            raise ValueError("Keyword arguments are not supported in formulas")
        if isinstance(node.func, ast.Name) and node.func.id == "len" and len(node.args) == 1:
            return {"op": "len", "args": [_from_ast(node.args[0], depth=depth + 1)]}
        if isinstance(node.func, ast.Name) and node.func.id == "get" and len(node.args) in (2, 3):
            default = _from_ast(node.args[2], depth=depth + 1) if len(node.args) == 3 else {"value": None}
            return {"op": "get", "args": [
                _from_ast(node.args[0], depth=depth + 1),
                _from_ast(node.args[1], depth=depth + 1),
                default,
            ]}
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) in (1, 2):
            default = _from_ast(node.args[1], depth=depth + 1) if len(node.args) == 2 else {"value": None}
            return {"op": "get", "args": [
                _from_ast(node.func.value, depth=depth + 1),
                _from_ast(node.args[0], depth=depth + 1),
                default,
            ]}
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in ("all", "any")
            and len(node.args) == 1
            and isinstance(node.args[0], (ast.GeneratorExp, ast.ListComp))
        ):
            comp = node.args[0]
            if len(comp.generators) != 1:
                raise ValueError("Collection rule supports a single comprehension clause")
            gen = comp.generators[0]
            if not isinstance(gen.target, ast.Name) or gen.is_async:
                raise ValueError("Invalid collection variable")
            test_expr = _from_ast(comp.elt, depth=depth + 1)
            if gen.ifs:
                conds = [_from_ast(if_node, depth=depth + 1) for if_node in gen.ifs]
                guard = conds[0] if len(conds) == 1 else {"op": "and", "args": conds}
                if node.func.id == "any":
                    test_expr = {"op": "and", "args": [guard, test_expr]}
                else:
                    test_expr = {"op": "or", "args": [{"op": "not", "args": [guard]}, test_expr]}
            return {
                "op": node.func.id,
                "items": _from_ast(gen.iter, depth=depth + 1),
                "as": gen.target.id,
                "test": test_expr,
            }
    raise ValueError(f"Unsupported formula syntax: {ast.dump(node)}")


def compile_expr(expr):
    """Compile a 1-line Python formula, scalar literal, or AST dict into canonical AST."""
    if type(expr) in (bool, int, float) or expr is None:
        canonical(expr)
        return {"value": expr}
    if isinstance(expr, str):
        text = expr.strip()
        if not text or len(text) > 4096:
            raise ValueError("Invalid formula expression")
        try:
            parsed = ast.parse(text, mode="eval")
        except SyntaxError as error:
            raise ValueError(f"Invalid formula syntax: {text!r}") from error
        compiled = _from_ast(parsed.body)
        validate_expression(compiled)
        return compiled
    if isinstance(expr, dict):
        if set(expr) in ({"value"}, {"var"}):
            return copy.deepcopy(expr)
        if expr.get("op") in ("all", "any") and set(expr) == {"op", "items", "as", "test"}:
            return {
                "op": expr["op"],
                "items": compile_expr(expr["items"]),
                "as": expr["as"],
                "test": compile_expr(expr["test"]),
            }
        if "op" in expr and set(expr) == {"op", "args"} and isinstance(expr["args"], list):
            return {"op": expr["op"], "args": [compile_expr(arg) for arg in expr["args"]]}
        return copy.deepcopy(expr)
    raise ValueError("An expression must be a formula string, literal, or expression object")


def format_expr(expr) -> str:
    """Render an expression tree as a sleek 1-line formula."""
    expr = compile_expr(expr)
    if "value" in expr:
        val = expr["value"]
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=True, sort_keys=True)
        return repr(val)
    if "var" in expr:
        return expr["var"]
    op = expr["op"]
    if op in ("all", "any"):
        return f"{op}({format_expr(expr['test'])} for {expr['as']} in {format_expr(expr['items'])})"
    args = expr["args"]
    if op == "not":
        inner = args[0]
        if isinstance(inner, dict) and inner.get("op") == "in":
            return f"{format_expr(inner['args'][0])} not in {format_expr(inner['args'][1])}"
        rendered = format_expr(inner)
        return f"not ({rendered})" if isinstance(inner, dict) and inner.get("op") in ("and", "or", "if", "eq", "ne", "lt", "le", "gt", "ge", "in", "add", "sub") else f"not {rendered}"
    if op in ("and", "or"):
        joiner = f" {op} "
        parts = []
        for arg in args:
            text = format_expr(arg)
            if isinstance(arg, dict) and arg.get("op") in ("or", "if") and op == "and":
                text = f"({text})"
            parts.append(text)
        return joiner.join(parts)
    if op == "if":
        return f"({format_expr(args[1])} if {format_expr(args[0])} else {format_expr(args[2])})"
    if op == "len":
        return f"len({format_expr(args[0])})"
    if op == "get":
        target = format_expr(args[0])
        if isinstance(args[0], dict) and args[0].get("op") in ("and", "or", "if", "eq", "ne", "lt", "le", "gt", "ge", "in", "add", "sub"):
            target = f"({target})"
        return f"{target}.get({format_expr(args[1])}, {format_expr(args[2])})"
    if op in _BINARY_SYMBOLS:
        return f"{format_expr(args[0])} {_BINARY_SYMBOLS[op]} {format_expr(args[1])}"
    return canonical(expr)


def format_rule(rule: dict) -> str:
    """Render a rule as a compact law: `target = then` or `target = then when cond`."""
    when_expr = compile_expr(rule.get("when", {"value": True}))
    then_text = format_expr(rule["then"])
    if when_expr == {"value": True}:
        return f"{rule['target']} = {then_text}"
    return f"{rule['target']} = {then_text} when {format_expr(when_expr)}"


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


def _is_bool_expr(expr) -> bool:
    if not isinstance(expr, dict):
        return False
    if "value" in expr:
        return type(expr["value"]) is bool
    return expr.get("op") in _BOOL_OPS


def evaluate(expr, context, *, budget=None):
    expr = compile_expr(expr)
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
        if isinstance(values[1], list):
            left_key = canonical(values[0])
            return any(canonical(item) == left_key for item in values[1])
        if isinstance(values[1], str) and not isinstance(values[0], str):
            raise Unknown("in on a string needs a string left operand")
        if isinstance(values[1], dict) and not isinstance(values[0], str):
            return False
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


def _eq_or_in_pair(arg):
    if not isinstance(arg, dict):
        return None
    if arg.get("op") == "eq":
        left, right = arg["args"]
        if set(right) == {"value"} and set(left) != {"value"}:
            return left, [right["value"]]
        if set(left) == {"value"} and set(right) != {"value"}:
            return right, [left["value"]]
    if arg.get("op") == "in":
        left, right = arg["args"]
        if set(right) == {"value"} and isinstance(right["value"], list) and set(left) != {"value"}:
            return left, right["value"]
    return None


def _factor_or(args):
    # 1. Distributive law: (A and B) or (A and C) -> A and (B or C)
    while len(args) > 1:
        counts = {}
        by_key = {}
        for idx, arg in enumerate(args):
            if isinstance(arg, dict) and arg.get("op") == "and":
                for item in arg["args"]:
                    key = canonical(item)
                    by_key[key] = item
                    counts.setdefault(key, []).append(idx)
        best = max(
            ((len(idxs), key, idxs) for key, idxs in counts.items() if len(idxs) >= 2),
            default=None,
        )
        if best is None:
            break
        _, common_key, matched_idxs = best
        matched_set = set(matched_idxs)
        remainders, others = [], []
        for idx, arg in enumerate(args):
            if idx not in matched_set:
                others.append(arg)
                continue
            rest = [item for item in arg["args"] if canonical(item) != common_key]
            remainders.append(rest[0] if len(rest) == 1 else {"op": "and", "args": rest})
        factored = normalize({
            "op": "and",
            "args": [by_key[common_key], normalize({"op": "or", "args": remainders})],
        })
        args = others + [factored]
    # 2. Set membership factoring: (x == a) or (x == b) -> x in [a, b]
    groups = {}
    for idx, arg in enumerate(args):
        pair = _eq_or_in_pair(arg)
        if pair is not None:
            lhs, vals = pair
            entry = groups.setdefault(canonical(lhs), {"lhs": lhs, "idxs": [], "vals": {}})
            entry["idxs"].append(idx)
            for val in vals:
                entry["vals"][canonical(val)] = val
    folded_idxs = set()
    folded_args = []
    for entry in groups.values():
        if len(entry["idxs"]) >= 2 and len(entry["vals"]) >= 2:
            folded_idxs.update(entry["idxs"])
            merged_vals = [entry["vals"][k] for k in sorted(entry["vals"])]
            folded_args.append({"op": "in", "args": [entry["lhs"], {"value": merged_vals}]})
    if folded_idxs:
        args = [arg for idx, arg in enumerate(args) if idx not in folded_idxs] + folded_args
    unique = {canonical(arg): arg for arg in args}
    return [unique[key] for key in sorted(unique)]


def normalize(expr):
    """Algebraic factoring: flatten, deduplicate, factor common conjuncts and sets."""
    expr = compile_expr(expr)
    if "op" not in expr:
        return copy.deepcopy(expr)
    if expr["op"] in ("all", "any"):
        return {**expr, "items": normalize(expr["items"]), "test": normalize(expr["test"])}
    args = [normalize(arg) for arg in expr["args"]]
    if expr["op"] == "not":
        inner = args[0]
        if set(inner) == {"value"} and type(inner["value"]) is bool:
            return {"value": not inner["value"]}
        if isinstance(inner, dict) and inner.get("op") == "not" and _is_bool_expr(inner["args"][0]):
            return inner["args"][0]
    if expr["op"] == "if" and _is_bool_expr(args[0]):
        if args[1] == {"value": True} and args[2] == {"value": False}:
            return args[0]
        if args[1] == {"value": False} and args[2] == {"value": True}:
            return normalize({"op": "not", "args": [args[0]]})
    if expr["op"] in ("and", "or"):
        flat = []
        for arg in args:
            flat.extend(arg["args"] if arg.get("op") == expr["op"] else [arg])
        unique = {canonical(arg): arg for arg in flat}
        args = [unique[key] for key in sorted(unique)]
        identity = {"value": True} if expr["op"] == "and" else {"value": False}
        if len(args) > 1 and identity in args:
            filtered = [arg for arg in args if arg != identity]
            args = filtered or [identity]
        if expr["op"] == "or" and len(args) > 1:
            args = _factor_or(args)
        if len(args) == 1:
            return args[0]
    return {"op": expr["op"], "args": args}


def variables(expr, bound=()):
    expr = compile_expr(expr)
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
        first["when"] = normalize({"op": "or", "args": [first["when"], rule["when"]]})
        first["claim"] = format_rule(first)[:240]
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
            if first["when"] == {"value": True} or second["when"] == {"value": True}:
                continue
            negated = lambda condition: normalize({"op": "not", "args": [condition]})
            if canonical(second["when"]) == canonical(negated(first["when"])):
                predicate, yes, no = first["when"], first["then"], second["then"]
            elif canonical(first["when"]) == canonical(negated(second["when"])):
                predicate, yes, no = second["when"], second["then"], first["then"]
            else:
                continue
            effect = normalize({"op": "if", "args": [predicate, yes, no]})
            try:
                validate_expression(effect)
            except ValueError:
                continue
            # if preserves the Boolean type check of both original conditions.
            first["when"], first["then"] = {"value": True}, effect
            first["claim"] = format_rule(first)[:240]
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
            rule.setdefault("when", {"value": True})
            rule["when"] = compile_expr(rule["when"])
            validate_expression(rule["when"])
            if "then" not in rule:
                raise ValueError("An expression must be an object")
            rule["then"] = compile_expr(rule["then"])
            validate_expression(rule["then"])
            if "claim" not in rule:
                rule["claim"] = format_rule(rule)[:240]
            _string(rule.get("claim"), "claim", 240)
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


def _conjuncts(expr) -> frozenset[str]:
    expr = normalize(expr)
    if expr == {"value": True}:
        return frozenset()
    if isinstance(expr, dict) and expr.get("op") == "and":
        return frozenset(canonical(arg) for arg in expr["args"])
    return frozenset({canonical(expr)})


def prediction(rules, inputs, target):
    matched, used = [], []
    budget = [8192]
    try:
        for rule in rules:
            if rule["target"] != target:
                continue
            when_expr = compile_expr(rule.get("when", {"value": True}))
            then_expr = compile_expr(rule["then"])
            applies = evaluate(when_expr, inputs, budget=budget)
            if type(applies) is not bool:
                raise Unknown("Rule condition must return a Boolean")
            if applies:
                value = evaluate(then_expr, inputs, budget=budget)
                used.append(rule["id"])
                matched.append((rule["id"], _conjuncts(when_expr), canonical(value), value))
    except (Unknown, TypeError, OverflowError, ValueError) as error:
        return {"status": "unknown", "reason": str(error), "rules": used}
    if not matched:
        return {"status": "unknown", "reason": "No applicable rule", "rules": []}
    # A strictly more specific condition (strict superset of conjuncts) overrides
    # a broader or unconditional default law without ambiguity.
    active = [
        item for item in matched
        if not any(item[1] < other[1] for other in matched)
    ]
    values = {item[2]: item[3] for item in active}
    active_ids = [item[0] for item in active]
    if len(values) > 1:
        return {"status": "ambiguous", "values": list(values.values()), "rules": active_ids}
    return {"status": "predicted", "value": next(iter(values.values())), "rules": active_ids}


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


_TYPE_NAMES = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def _expand_delta(raw, active):
    """Expand consecutive compression deltas (`observations` + `residuals`) against the active model."""
    if not isinstance(raw, dict) or not ({"observations", "residuals"} & set(raw)):
        return raw
    if active is None:
        raise ValueError("Observations and residuals require an active model")
    extra = set(raw) - {
        "observations", "residuals", "rules", "inputs",
        "alternatives", "unresolved", "resolutions", "title",
    }
    if extra:
        raise ValueError("Unknown delta model field")
    updated = copy.deepcopy(active["model"])
    if "title" in raw:
        updated["title"] = raw["title"]
    if "inputs" in raw and isinstance(raw["inputs"], dict):
        updated["inputs"].update(raw["inputs"])
    observations = raw.get("observations")
    if observations is not None:
        if not isinstance(observations, list) or not observations:
            raise ValueError("Observations must be a nonempty list of recorded cases")
        for obs in observations:
            if isinstance(obs, dict) and isinstance(obs.get("inputs"), dict):
                for key, val in obs["inputs"].items():
                    if key not in updated["inputs"] and type(val) in _TYPE_NAMES:
                        updated["inputs"][key] = _TYPE_NAMES[type(val)]
                        for prior in updated["cases"]:
                            prior["inputs"].setdefault(key, val)
        updated["cases"] = copy.deepcopy(observations)
    if "rules" in raw:
        updated["rules"] = copy.deepcopy(raw["rules"])
    residuals = raw.get("residuals")
    if residuals is not None:
        if not isinstance(residuals, list) or not residuals:
            raise ValueError("Residuals must be a nonempty list of rules")
        obs_list = observations or []
        by_id = {rule["id"]: idx for idx, rule in enumerate(updated["rules"])}
        for res in copy.deepcopy(residuals):
            if not isinstance(res, dict):
                raise ValueError("Invalid rule fields")
            if "evidence" not in res and obs_list:
                matching = union(*(
                    c.get("evidence", []) for c in obs_list
                    if isinstance(c, dict) and c.get("target") == res.get("target")
                )) or union(*(c.get("evidence", []) for c in obs_list if isinstance(c, dict)))
                if matching:
                    res["evidence"] = matching
            if res.get("id") in by_id:
                updated["rules"][by_id[res["id"]]] = res
            else:
                by_id[res.get("id")] = len(updated["rules"])
                updated["rules"].append(res)
    for key in ("alternatives", "unresolved", "resolutions"):
        if key in raw:
            updated[key] = copy.deepcopy(raw[key])
    return updated


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
        raw = _expand_delta(copy.deepcopy(model), active)
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
            if isinstance(model, dict) and ({"observations", "residuals"} & set(model)):
                active = self.get()
                if active is None:
                    raise ValueError("Observations and residuals require an active model")
                if base is None:
                    base = active["id"]
                model = _expand_delta(model, active)
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
                "rules": [{"id": rule["id"], "claim": rule["claim"], "formula": format_rule(rule)}
                          for rule in record["model"]["rules"]],
                "covered": covered, "alternatives": sum(alt["status"] == "unseparated" for alt in report["alternatives"]),
                "unresolved": len(report["unresolved"]), "issues": report["issues"],
                "complexity": report["complexity"]}
