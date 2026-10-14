from __future__ import annotations

import json
from pathlib import Path
import subprocess
from threading import Event

import pytest

from evaluation.model_transfer.analysis import binomial_upper, summarize
from evaluation.model_transfer.runner import Settings, agent_response, decode_completion, run_stage
from evaluation.model_transfer.study import StudyStop, prepare, probe_inputs, verify
from evaluation.model_transfer.tasks import (FAMILIES, build_task, equivalent, grade_outputs, output_schema,
                                            reference, task_pack, validate_controls)
from evaluation.model_transfer.worker import Notebook, WorkerTools, safe_path, operation_budget, operation_count
from agent_memory.models import prediction, reduce_structure, validate_model


def completion(action="finish", arguments=None, input_tokens=100, output_tokens=20):
    events = [{"type": "thread.started", "thread_id": "fresh"},
              {"type": "item.completed", "item": {"id": "message", "type": "agent_message",
               "text": json.dumps({"action": action, "arguments_json": json.dumps(arguments or {})})}},
              {"type": "turn.completed", "usage": {"input_tokens": input_tokens,
               "output_tokens": output_tokens, "cached_input_tokens": 60, "reasoning_output_tokens": 10}}]
    return "\n".join(json.dumps(e) for e in events)


def test_native_usage_does_not_count_cache_or_reasoning_twice():
    c = decode_completion(completion(), 0)
    assert c["ok"] and c["usage_complete"]
    assert c["usage"] == {"input_tokens": 100, "output_tokens": 20,
                          "cached_input_tokens": 60, "reasoning_output_tokens": 10}


@pytest.mark.parametrize("bad", ["", '{"type":"turn.completed","usage":{}}',
    '{"type":"turn.completed","usage":{"input_tokens":true,"output_tokens":3}}',
    '{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":3,"cached_input_tokens":4}}'])
def test_missing_or_invalid_usage_is_never_zero_cost(bad):
    c = decode_completion(bad, 0)
    assert not c["ok"] and not c["usage_complete"]


def test_failure_after_metered_turn_cannot_claim_complete_usage():
    c = decode_completion(completion() + '\n{"type":"turn.failed","error":{"message":"network"}}', 1)
    assert not c["usage_complete"]
    assert c["usage"]["input_tokens"] == 100


def test_native_tool_use_is_detected_without_double_counting():
    c = decode_completion(completion() + '\n{"type":"item.started","item":{"type":"command_execution","id":"a"}}'
                          + '\n{"type":"item.completed","item":{"type":"command_execution","id":"a"}}', 0)
    assert not c["ok"] and c["native_tool_calls"] == 1


def test_task_packs_disjoint_balanced_and_controls_reject_bad_outputs():
    pilot, confirm = task_pack("pilot"), task_pack("confirmation")
    assert len(pilot) == 8 and len(confirm) == 96
    assert not set(t["id"] for t in pilot) & set(t["id"] for t in confirm)
    assert sum(t["domain"] == "code" for t in confirm) == 48
    assert len(validate_controls(pilot + confirm)) == 312


def test_sources_change_across_stages_and_new_evidence_can_require_more_rules():
    task = build_task("conflicting_evidence", 0)
    assert task["stages"][0]["files"]["evidence.json"] != task["stages"][1]["files"]["evidence.json"]
    assert task["stages"][0]["cases"][0]["expected"]["status"] == "supported"
    assert task["stages"][1]["cases"][0]["expected"]["status"] == "conflict"
    later = build_task("revised_conclusions", 0)["stages"][-1]
    assert later["cases"][0]["expected"]["status"] == "unknown"
    assert any(c["request"]["claim"] == "lateral_rate" and c["expected"]["status"] == "unknown" for c in later["cases"])


def test_report_and_all_private_cases_are_required():
    stage = build_task("data_validation", 0)["stages"][-1]
    values = [c["expected"] for c in stage["cases"]]
    assert not grade_outputs(stage, values[:-1], values[:1])["full_pass"]
    assert not grade_outputs(stage, values, ["fabricated"])["full_pass"]
    assert grade_outputs(stage, values, values[:stage["visible_count"]])["full_pass"]


def test_grading_accepts_numeric_formatting_but_rejects_boolean_and_nonfinite_values():
    assert equivalent({"amount": 1.0}, {"amount": 1})
    assert not equivalent({"amount": False}, {"amount": 0})
    assert not equivalent(float("nan"), float("nan"))


def test_repaired_source_rule_keeps_all_eleven_observed_failures():
    root = Path(__file__).resolve().parents[2]
    model = validate_model(json.loads((root/"examples/source_dependency_model.json").read_text()))
    cases = json.loads((root/"evaluation/tests/fixtures/source_model_failures.json").read_text())["cases"]
    assert len(cases)==11
    for candidate in (model,reduce_structure(model)):
        for case in cases:
            result = prediction(candidate["rules"],case["inputs"],"needs_review")
            assert result["status"]=="predicted" and result["value"] is case["expected"]


def test_repaired_source_rule_uses_raw_inputs_on_new_work_samples():
    model = json.loads((Path(__file__).resolve().parents[2]/"examples/source_dependency_model.json").read_text())
    for variant in (7,19,83):
        for stage in build_task("source_dependencies",variant)["stages"]:
            for case in stage["cases"]:
                result = prediction(model["rules"],{"request":case["request"],"policy":stage["policy"]},"needs_review")
                assert result["status"]=="predicted" and result["value"] is case["expected"]["needs_review"]


def test_controls_do_not_accept_boolean_numbers_or_partial_batches():
    p = build_task("data_validation", 0)["stages"][-1]["policy"]
    r = reference("data_validation", {"rows": [{"id": "a", "amount": 2}, {"id": "b", "amount": False}]}, p)
    assert r == {"ok": False, "records": [], "errors": [{"row": 1, "cause": "amount"}]}


def test_notebook_has_full_retrieval_and_real_bm25(tmp_path):
    n = Notebook(tmp_path)
    note = n.call({"action": "save", "text": "Calibration rates require the same recipe.\nUnresolved: no lateral measurement."})
    n.call({"action": "save", "text": "Use Git for source snapshots."})
    assert n.call({"action": "find", "query": "lateral calibration"})[0]["id"] == note["id"]
    assert "Unresolved" in n.call({"action": "find", "ids": [note["id"]]})[0]["text"]


@pytest.mark.parametrize("arm", ["notebook", "notes", "written"])
def test_model_tools_are_disabled_in_controls(tmp_path, arm):
    with pytest.raises(ValueError, match="disabled"):
        WorkerTools(tmp_path, arm).memory({"action": "model_get"})


def test_tools_block_path_escape_and_symlink_escape(tmp_path):
    workspace = tmp_path / "worker"
    workspace.mkdir()
    secret = tmp_path / "private"
    secret.write_text("grader")
    (workspace / "link").symlink_to(secret)
    for path in ("../private", "link", str(secret)):
        with pytest.raises(ValueError):
            safe_path(workspace, path)


def test_tools_preserve_complete_output(tmp_path):
    t = WorkerTools(tmp_path, "notebook")
    r = t.execute("run", {"command": ["python3", "-c", "print('Z'*3000)"]})
    assert len(r["stdout"].strip()) == 3000


def test_batch_preserves_results_and_cannot_bypass_model_controls(tmp_path):
    tools = WorkerTools(tmp_path, "notebook")
    args = {"calls": [
        {"action": "write", "arguments": {"path": "a.txt", "content": "A" * 3000}},
        {"action": "read", "arguments": {"path": "a.txt"}},
        {"action": "memory", "arguments": {"action": "model_handoff"}},
        {"action": "write", "arguments": {"path": "not-executed", "content": "bad"}}]}
    result = tools.execute("batch", args)
    assert len(result["calls"]) == 3
    assert result["calls"][1]["response"]["result"]["content"] == "A" * 3000
    assert not result["calls"][2]["response"]["ok"]
    assert not (tmp_path / "not-executed").exists()
    assert operation_budget("batch", args) == 10
    assert operation_count("batch", args, {"ok": True, "result": result}) == 3
    with pytest.raises(ValueError):
        tools.execute("batch", {"calls": [{"action": "batch", "arguments": args}]})


def test_batch_budget_is_reserved_before_any_tool_executes(tmp_path):
    args = {"calls": [{"action": "read", "arguments": {"path": "a"}}] * 3}
    tools = FakeTools()
    result = run_stage(FakeProvider([("batch", args)]), tools, "task", "", tmp_path / "batch", Settings(tool_calls=2))
    assert result["stop"] == "tool_limit" and not tools.calls
    assert result["tokens"] == 120


def test_batch_operations_count_separately_from_completions(tmp_path):
    class BatchTools(FakeTools):
        def execute(self, action, args, timeout):
            return {"ok": True, "result": {"calls": [{**c, "response": {"ok": True, "result": {}}} for c in args["calls"]]}}
    args = {"calls": [{"action": "read", "arguments": {"path": "a"}}] * 3}
    result = run_stage(FakeProvider([("batch", args), ("finish", {})]), BatchTools(), "task", "", tmp_path / "batch", Settings(tool_calls=3))
    assert result["stop"] == "finished" and result["tool_calls"] == 3
    assert result["tokens"] == 240


def test_handoff_workflow_charges_actual_operations_and_reserves_maximum():
    args = {"action": "model_handoff", "payload": None}
    assert operation_budget("memory", args) == 7
    response = {"ok": True, "result": json.dumps({"status": "tested", "operations": [{}, {}, {}]})}
    assert operation_count("memory", args, response) == 3


@pytest.mark.parametrize("arm", ["notebook", "notes", "models"])
def test_memory_result_files_and_payload_files_preserve_data(tmp_path, arm):
    tools = WorkerTools(tmp_path, arm)
    (tmp_path / "payload.json").write_text('{"calibration":"unknown"}')
    result = tools.memory({"action": "save", "text": "Measured boundary case", "payload_file": "payload.json", "result_file": "saved.json"})
    stored = json.loads((tmp_path / "saved.json").read_text())
    assert stored == (json.loads(result) if isinstance(result, str) and result.startswith("{") else result)
    with pytest.raises(ValueError):
        tools.memory({"action": "history", "result_file": "../escape.json"})


def test_metered_invalid_decision_can_be_repaired_without_hiding_cost(tmp_path):
    class RepairProvider:
        def __init__(self):
            self.prompts = []
        def complete(self, prompt, output, timeout):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                events = completion()
                data = [json.loads(s) for s in events.splitlines()]
                data[1]["item"]["text"] = json.dumps({"action": "memory", "arguments_json": '{"action":"save"'})
                return decode_completion("\n".join(json.dumps(e) for e in data), 0)
            return decode_completion(completion(), 0)
    provider = RepairProvider()
    result = run_stage(provider, FakeTools(), "task", "", tmp_path / "repair", Settings())
    assert result["stop"] == "finished" and result["tokens"] == 240
    assert result["tool_calls"] == 0 and result["usage_complete"]
    assert "decision_error" in provider.prompts[1]


def test_file_based_model_handoff_keeps_complete_model_and_operations(tmp_path):
    from agent_memory.core import Memory
    note_id = Memory(tmp_path).save("Source comparison boundary observations")
    model = json.loads((Path(__file__).resolve().parents[2] / "examples/source_dependency_model.json").read_text())
    for rule in model["rules"]:
        rule["evidence"] = [note_id]
    for case in model["cases"]:
        case["evidence"] = [note_id]
    (tmp_path / "model.json").write_text(json.dumps(model))
    result = json.loads(WorkerTools(tmp_path, "models").memory({"action": "model_handoff", "payload_file": "model.json", "result_file": "handoff.json"}))
    assert result["status"] == "tested"
    assert json.loads((tmp_path / "handoff.json").read_text()) == result
    assert result["operations"][0]["arguments"]["model"] == model


def test_handoff_working_result_does_not_discard_the_full_run_audit(tmp_path):
    report = {"status": "tested", "operations": [{"action": "model_propose", "arguments": {"model": {"title": "M"}}, "ok": True, "result": "id"}]}
    response = {"ok": True, "result": json.dumps(report)}
    visible = agent_response("memory", {"action": "model_handoff"}, response)
    assert "model_sha256" in json.loads(visible["result"])["operations"][0]["arguments"]
    assert json.loads(response["result"]) == report
    assert agent_response("memory", {"action": "model_handoff", "full": True}, response) == response
    class WorkflowTools(FakeTools):
        def execute(self, action, args, timeout):
            return response
    result = run_stage(FakeProvider([("memory", {"action": "model_handoff"}), ("finish", {})]), WorkflowTools(), "task", "", tmp_path / "run", Settings())
    events = [json.loads(s) for s in (tmp_path / "run/run.jsonl").read_text().splitlines()]
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["result"] == response and tool["agent_result"] == visible
    assert result["tool_calls"] == 1


def test_handoff_checks_are_separate_from_scored_pilot(tmp_path):
    manifest = prepare(tmp_path / "handoff", "handoff", Settings(model="gpt-5.6-sol"))
    assert len(manifest["task_ids"]) == 2
    assert {r["arm"] for r in manifest["schedule"]} == {"models"}
    assert not set(manifest["task_ids"]) & {t["id"] for t in task_pack("pilot")}
    verify(tmp_path / "handoff")


def test_code_probe_uses_empty_evidence_array_instead_of_null():
    task = build_task("source_dependencies", 73)
    stage = task["stages"][0]
    assert stage["cases"][0]["evidence"] is None
    inputs = probe_inputs(task, stage, stage["cases"][0], {"request", "policy", "evidence"})
    assert inputs["evidence"] == []
    research = build_task("calibration_limits", 73)
    rs = research["stages"][0]
    assert probe_inputs(research, rs, rs["cases"][0], {"request", "policy", "evidence"})["evidence"] == rs["cases"][0]["evidence"]


def test_public_output_schemas_match_every_private_reference_shape():
    def conforms(value, schema):
        if "enum" in schema:
            return any(type(value) is type(x) and value == x for x in schema["enum"])
        types = schema.get("type", [])
        types = types if isinstance(types, list) else [types]
        kind = {type(None): "null", bool: "boolean", str: "string", int: "integer", float: "number", list: "array", dict: "object"}.get(type(value))
        if types and kind not in types and not (kind == "integer" and "number" in types):
            return False
        if "properties" in schema:
            if set(value) != set(schema["required"]):
                return False
            return all(conforms(value[k], s) for k, s in schema["properties"].items())
        if "items" in schema:
            return all(conforms(v, schema["items"]) for v in value)
        return True
    for task in task_pack("pilot"):
        schema = output_schema(task["family"])
        for stage in task["stages"]:
            assert json.loads(stage["files"]["OUTPUT_SCHEMA.json"]) == schema
            assert all(conforms(c["expected"], schema) for c in stage["cases"])
    assert not conforms({"ok": True, "records": [{"id": "a", "amount": 1, "unit": "ug"}], "errors": []}, output_schema("data_validation"))


def test_data_contract_defines_record_fields_and_accepted_id_tracking():
    text = build_task("data_validation", 2)["stages"][0]["files"]["TASK.md"]
    assert "Do not include unit or other metadata" in text
    assert "only after that row passes every check" in text
    assert "collect errors for all rows" in text


def test_controller_stop_file_blocks_new_calls_without_missing_usage(tmp_path):
    path = tmp_path / "study-stop.json"
    stop = StudyStop(path)
    assert not stop.is_set()
    path.write_text('{"reason":"worker_contract_fault"}')
    provider = FakeProvider([])
    result = run_stage(provider, FakeTools(), "task", "", tmp_path / "stopped", Settings(), stop)
    assert result["stop"] == "study_cancelled" and result["usage_complete"]
    assert result["tokens"] == 0 and not provider.prompts


class FakeProvider:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.prompts = []

    def complete(self, prompt, output, timeout):
        self.prompts.append(prompt)
        action, args = next(self.decisions)
        return decode_completion(completion(action, args), 0)


class FakeTools:
    arm = "notes"

    def __init__(self):
        self.calls = []

    def execute(self, action, args, timeout):
        self.calls.append((action, args))
        return {"ok": True, "result": "R" * 2500}


def test_tool_limit_is_enforced_before_execution_and_costs_include_all_calls(tmp_path):
    provider = FakeProvider([("read", {"path": "TASK.md"})] * 3)
    tools = FakeTools()
    r = run_stage(provider, tools, "task", "P" * 2000, tmp_path / "run", Settings(tool_calls=2))
    assert len(tools.calls) == 2 and r["stop"] == "tool_limit"
    assert r["tokens"] == 360
    assert "R" * 2500 in provider.prompts[-1]
    assert "P" * 1201 not in provider.prompts[0]
    log = [json.loads(l) for l in (tmp_path / "run/run.jsonl").read_text().splitlines()]
    assert sum(e["type"] == "completion" for e in log) == 3
    assert sum(e["type"] == "tool" for e in log) == 2


def test_finish_requires_a_metered_completion(tmp_path):
    r = run_stage(FakeProvider([("finish", {})]), FakeTools(), "task", "", tmp_path / "run", Settings())
    assert r["stop"] == "finished" and r["tokens"] == 120


def test_model_handoff_guard_requires_tested_model(tmp_path):
    class ModelTools(FakeTools):
        arm = "models"
        def execute(self, action, args, timeout):
            if action == "_snapshot":
                return {"ok": True, "result": {"model": None, "checks": {"status": "absent"}}}
            return super().execute(action,args,timeout)
    tools = ModelTools()
    r = run_stage(FakeProvider([("read",{"path":"TASK.md"}),("finish",{})]), tools,
                  "task", "", tmp_path / "run", Settings(tool_calls=1))
    assert r["stop"] == "model_handoff_failure" and r["tokens"] == 240


def test_tested_model_allows_handoff(tmp_path):
    class ModelTools(FakeTools):
        arm = "models"
        def execute(self, action, args, timeout):
            return {"ok": True, "result": {"model": {"id": "accepted"}, "checks": {"status": "tested"}}}
    r = run_stage(FakeProvider([("finish",{})]), ModelTools(), "task", "", tmp_path / "run", Settings())
    assert r["stop"] == "finished"


def test_budget_reserve_does_not_start_an_unmetered_last_call(tmp_path, monkeypatch):
    ticks = iter((0, 1, 2, 550, 551))
    monkeypatch.setattr("evaluation.model_transfer.runner.time.monotonic", lambda: next(ticks))
    provider = FakeProvider([("read", {"path":"TASK.md"})])
    result = run_stage(provider, FakeTools(), "task", "", tmp_path/"run", Settings())
    assert result["stop"] == "stage_budget_reserve"
    assert result["usage_complete"] and result["tokens"] == 120
    assert len(provider.prompts) == 1


def test_metering_fault_stops_peer_dispatch(tmp_path):
    stop = Event()
    class FaultProvider:
        def complete(self, prompt, output, timeout):
            return decode_completion('', 1)
    result = run_stage(FaultProvider(), FakeTools(), "task", "", tmp_path/"fault", Settings(), stop)
    assert result["tokens"] is None and stop.is_set()
    provider = FakeProvider([])
    peer = run_stage(provider, FakeTools(), "task", "", tmp_path/"peer", Settings(), stop)
    assert peer["stop"] == "study_cancelled"
    assert not provider.prompts


def records(n=96, repeats=1, new_tokens=70):
    out = []
    for i in range(n):
        for repeat in range(repeats):
            for arm in ("notebook", "notes", "models"):
                out.append({"task": f"t{i}", "domain": "code" if i % 2 else "research", "repeat": repeat,
                            "arm": arm, "tokens": new_tokens if arm == "models" else 100,
                            "complete": True, "usage_complete": True, "isolated": True})
    return out


def analyze(rows, n=96, repeats=1, phase="confirmation"):
    return summarize(rows, phase=phase, expected_task_ids=[f"t{i}" for i in range(n)], repeats=repeats, bootstrap=150)


def test_primary_measure_charges_failed_runs():
    rows = records()
    rows[2]["complete"] = False
    result = analyze(rows)
    assert result["comparisons"]["notebook"]["token_ratio"] == pytest.approx((96*70/95)/100)
    assert result["comparisons"]["notebook"]["additional_failure_tasks"] == 1


def test_confirmation_thresholds_and_pilot_uncertainty():
    assert analyze(records())["conclusion"] == "useful"
    assert analyze(records(new_tokens=49))["conclusion"] == "exceptional"
    assert analyze(records(new_tokens=90))["conclusion"] == "no demonstrated benefit"
    pilot = analyze(records(8,2), n=8,repeats=2,phase="pilot")
    assert pilot["conclusion"] == "inconclusive" and pilot["pilot_continue"]
    assert pilot["tasks"] == 8
    assert pilot["comparisons"]["notes"]["additional_failure_upper"] > 0.05


def test_unknown_usage_or_isolation_prevents_claims():
    rows = records()
    rows[0]["tokens"] = None
    assert analyze(rows)["conclusion"] == "inconclusive"
    rows = records()
    rows[0]["run_valid"] = False
    assert analyze(rows)["conclusion"] == "inconclusive"
    rows = records()
    rows[0]["isolated"] = False
    assert analyze(rows)["conclusion"] == "inconclusive"
    rows = records()
    rows[2]["model_transfer_complete"] = False
    assert analyze(rows)["conclusion"] == "inconclusive"


def test_domain_results_have_separate_cost_and_quality_bounds():
    result = analyze(records())
    for domain in ("code", "research"):
        detail = result["by_domain"][domain]
        assert detail["models"]["tokens_per_completed_task"] == 70
        assert detail["comparisons"]["notes"]["token_ratio"] == pytest.approx(0.7)
        assert detail["comparisons"]["notes"]["additional_failure_upper"] > 0.05
        assert detail["conclusion"] == "no demonstrated benefit"


def test_missing_matrix_or_duplicate_trials_cannot_improve_results():
    rows = records()
    assert analyze(rows[:-1])["conclusion"] == "inconclusive"
    with pytest.raises(ValueError, match="Duplicate"):
        analyze(rows + [rows[0]])


def test_exact_quality_bound_and_repeats_are_not_extra_tasks():
    assert binomial_upper(0, 96) == pytest.approx(1-0.025**(1/96))
    a = analyze(records(), repeats=1)
    b = analyze(records(repeats=2), repeats=2)
    assert a["comparisons"]["notes"]["additional_failure_upper"] == b["comparisons"]["notes"]["additional_failure_upper"]


def test_freeze_detects_tampering_and_schedule_is_paired(tmp_path):
    output = tmp_path / "study"
    m = prepare(output, "pilot", Settings())
    assert len(m["schedule"]) == 48 and len(m["ablation_tasks"]) == 8
    verify(output)
    pack = output / "private/task-pack.json"
    tasks = json.loads(pack.read_text())
    tasks[0]["stages"][0]["cases"][0]["expected"] = {"forged": True}
    pack.write_text(json.dumps(tasks))
    with pytest.raises(ValueError, match="changed"):
        verify(output)


def test_confirmation_ablation_selection_is_balanced_and_frozen(tmp_path):
    m = prepare(tmp_path / "confirm", "confirmation", Settings())
    assert len(m["schedule"]) == 288 and len(m["ablation_tasks"]) == 12
    assert sum(any(t.startswith(f) for f in FAMILIES[:4]) for t in m["ablation_tasks"]) == 6
