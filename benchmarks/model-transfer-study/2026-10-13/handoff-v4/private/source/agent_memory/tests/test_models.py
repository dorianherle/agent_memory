"""Task-model revisions on software evidence, not constructed arithmetic laws."""
import copy
import itertools
import json
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.models import TaskModels, Unknown, evaluate, fingerprint, prediction, reduce_structure, validate_expression
from agent_memory.tests.helpers import MemoryTestCase


def value(item):
    return {"value": item}


def var(name):
    return {"var": name}


def op(name, *args):
    return {"op": name, "args": list(args)}


class TaskModelTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        (self.path / "policy.txt").write_text("Review claims when source state changes.\n")
        self.evidence = self.memory.save("Source-state tests: unchanged, changed, and created files.")
        self.model = {
            "title": "Review claims after source changes",
            "inputs": {"artifact": "string", "before": "object", "after": "object"},
            "rules": [], "cases": [], "unresolved": ["Directory dependencies need a separate test"],
        }
        same = {"state": "file", "sha256": "a"}
        changed = {"state": "file", "sha256": "b"}
        for name in ("app.py", "config.json", "calibration.json"):
            self.model["rules"].append({
                "id": name, "target": "needs_review", "claim": f"Review {name} claims after a source change.",
                "when": op("eq", var("artifact"), value(name)),
                "then": op("ne", var("before"), var("after")),
                "evidence": [self.evidence], "dependencies": ["policy.txt"],
            })
            for label, after, expected in (("unchanged", same, False), ("changed", changed, True)):
                self.model["cases"].append({
                    "id": name + "-" + label, "target": "needs_review",
                    "inputs": {"artifact": name, "before": same, "after": after},
                    "expected": expected, "evidence": [self.evidence], "challenge": expected,
                })

    def accept(self, model=None, base=None):
        proposal = self.memory.model_propose(model or self.model, base=base)
        self.assertEqual(self.memory.model_check(proposal)["status"], "tested")
        return self.memory.model_accept(proposal)

    def test_handoff_keeps_all_operations_and_reuses_model_without_new_commits(self):
        result = self.memory.model_handoff(self.model)
        self.assertEqual(result["status"], "tested")
        self.assertTrue(all(o["ok"] for o in result["operations"]))
        self.assertLessEqual(len(result["operations"]), 7)
        self.assertIn("model_reduce", [o["action"] for o in result["operations"]])
        self.assertEqual(result["operations"][-1]["action"], "model_predict")
        head = self.memory._run("rev-parse", "HEAD")
        reused = Memory(self.path).model_handoff()
        self.assertEqual(reused["status"], "tested")
        self.assertEqual(reused["id"], result["id"])
        self.assertEqual([o["action"] for o in reused["operations"]],
                         ["model_check", "model_reduce", "model_predict"])
        self.assertEqual(self.memory._run("rev-parse", "HEAD"), head)

    def test_handoff_retains_failed_check_and_does_not_accept_stale_evidence(self):
        active = self.accept()
        (self.path / "policy.txt").write_text("Changed review policy")
        result = self.memory.model_handoff()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([o["action"] for o in result["operations"]], ["model_check"])
        self.assertEqual(result["checks"]["status"], "blocked")
        self.assertEqual(self.memory.model_get()["id"], active)
        broken = self.memory.model_handoff({"inputs": {}})
        self.assertEqual(broken["status"], "blocked")
        self.assertFalse(broken["operations"][0]["ok"])

    def test_dynamic_lookup_preserves_explicit_values_and_uses_a_lazy_default(self):
        expr = op("get", var("mapping"), var("key"), var("unavailable"))
        validate_expression(expr)
        for item in (False, 0, "", None):
            actual = evaluate(expr, {"mapping":{"a":item}, "key":"a"})
            self.assertEqual(actual, item)
            self.assertIs(type(actual), type(item))
        fallback = op("get", var("mapping"), var("key"), value({"kind":"missing"}))
        self.assertEqual(evaluate(fallback, {"mapping":{},"key":"a"}), {"kind":"missing"})
        for mapping, key in (([],"a"), ({},1)):
            with self.assertRaises(Unknown):
                evaluate(fallback, {"mapping":mapping,"key":key})
        with self.assertRaisesRegex(ValueError,"argument count"):
            validate_expression(op("get", value({}),value("a")))

    def test_raw_dependency_model_survives_git_handoff_and_unknown_paths(self):
        from pathlib import Path
        example = Path(__file__).resolve().parents[2]/"examples/source_dependency_model.json"
        model = json.loads(example.read_text())
        model["rules"][0]["evidence"] = [self.evidence]
        for case in model["cases"]:
            case["evidence"] = [self.evidence]
        accepted = self.accept(model)
        fresh = Memory(self.path)
        same = {"kind":"file","hash":"x"}
        query = {"request":{"declared":["never-seen.cfg"],"saved":{"never-seen.cfg":same},
                            "current":{"never-seen.cfg":same,"irrelevant":True}},"policy":{}}
        self.assertFalse(fresh.model_predict(query,"needs_review")["value"])
        query["request"]["current"]["never-seen.cfg"] = {"kind":"file","hash":"y"}
        self.assertTrue(fresh.model_predict(query,"needs_review")["value"])
        query["policy"]["ignored"] = ["never-seen.cfg"]
        self.assertFalse(fresh.model_predict(query,"needs_review")["value"])
        query["request"]["declared"] = ["a"]*257
        self.assertEqual(fresh.model_predict(query,"needs_review")["status"],"unknown")
        self.assertEqual(fresh.model_get()["id"],accepted)
        fresh._catalog.close()

    def test_transfer_reduction_reopen_and_full_git_evidence(self):
        first = self.accept()
        result = self.memory.model_reduce()
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["after"]["rules"], 1)
        self.assertLess(result["after"]["working_bytes"], result["before"]["working_bytes"])
        second = self.memory.model_accept(result["id"])
        fresh = Memory(self.path)
        record = fresh.model_get()
        self.assertEqual(record["revision"], 2)
        self.assertEqual(record["parent"], first)
        self.assertEqual(record["id"], second)
        self.assertEqual(len(record["model"]["cases"]), 6)
        self.assertEqual(len(record["model"]["rules"]), 1)
        self.assertEqual(fresh.find(ids=self.evidence)[0].message,
                         "Source-state tests: unchanged, changed, and created files.")
        self.assertEqual(fresh.model_get(first)["model"]["rules"], self.memory.model_get(first)["model"]["rules"])
        fresh._catalog.close()

    def test_factoring_preserves_predictions_and_conditions_on_unseen_inputs(self):
        accepted = self.accept()
        old = self.memory.model_get()["model"]
        reduced = reduce_structure(old)
        states = ({"state": "missing"}, {"state": "file", "sha256": "x"},
                  {"state": "file", "sha256": "y"})
        for artifact, before, after in itertools.product(
                ("app.py", "config.json", "calibration.json", "other.txt"), states, states):
            inputs = {"artifact": artifact, "before": before, "after": after}
            a = prediction(old["rules"], inputs, "needs_review")
            b = prediction(reduced["rules"], inputs, "needs_review")
            self.assertEqual(a["status"], b["status"])
            if a["status"] == "predicted":
                self.assertEqual(a["value"], b["value"])
        self.assertEqual(self.memory.model_get()["id"], accepted)

    def test_generalization_to_new_files_needs_a_revision_and_new_observation(self):
        first = self.accept()
        model = self.memory.model_get()["model"]
        model["rules"] = [{**model["rules"][0], "id": "changed-source", "when": value(True),
                            "claim": "Review a claim when its declared source state differs."}]
        (self.path / "new_config.txt").write_text("workers=6")
        before = fingerprint(self.path, "new_config.txt")
        (self.path / "new_config.txt").write_text("workers=18")
        after = fingerprint(self.path, "new_config.txt")
        evidence = self.memory.save("A changed new_config.txt needs a new claim check.")
        model["cases"].append({"id": "new-source", "target": "needs_review",
                               "inputs": {"artifact": "new_config.txt", "before": before, "after": after},
                               "expected": True, "evidence": [evidence], "challenge": True})
        second = self.accept(model, base=first)
        self.assertNotEqual(first, second)
        prediction = self.memory.model_predict({"artifact": "other.txt", "before": before, "after": after}, "needs_review")
        self.assertEqual(prediction["value"], True)
        self.assertEqual(len(self.memory.model_get()["model"]["cases"]), 7)

    def test_failed_revision_is_retained_without_replacing_active_model(self):
        first = self.accept()
        bad = self.memory.model_get()["model"]
        bad["rules"][0]["then"] = value(False)
        proposal = self.memory.model_propose(bad, base=first)
        self.assertEqual(self.memory.model_check(proposal)["status"], "blocked")
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.memory.model_accept(proposal)
        self.assertEqual(self.memory.model_get()["id"], first)
        self.assertEqual(self.memory.model_get(proposal)["event"], "proposed")

    def test_missing_cases_are_carried_forward_but_cannot_be_rewritten(self):
        first = self.accept()
        revised = self.memory.model_get()["model"]
        revised["cases"] = []
        proposal = self.memory.model_propose(revised, base=first)
        self.assertEqual(len(self.memory.model_get(proposal)["model"]["cases"]), 6)
        revised = self.memory.model_get()["model"]
        revised["cases"][0]["expected"] = True
        with self.assertRaisesRegex(ValueError, "cannot be rewritten"):
            self.memory.model_propose(revised, base=first)

    def test_unresolved_results_and_resolutions_keep_their_evidence(self):
        first = self.accept()
        revised = self.memory.model_get()["model"]
        revised["unresolved"] = []
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.memory.model_propose(revised, base=first)
        revised = self.memory.model_get()["model"]
        note = self.memory.save("Directory case remains outside the declared model domain.")
        revised["resolutions"] = [{"result": revised["unresolved"][0], "reason": "Individual files define this model domain.",
                                   "evidence": [note]}]
        self.accept(revised, base=first)
        self.assertEqual(self.memory.model_check()["unresolved"], [])
        self.assertEqual(self.memory.model_get()["model"]["resolutions"][0]["evidence"], [note])

    def test_missing_file_creation_blocks_prediction_until_new_revision(self):
        for rule in self.model["rules"]:
            rule["dependencies"] = ["calibration-policy.json"]
        accepted = self.accept()
        (self.path / "calibration-policy.json").write_text('{"rule":"changed"}')
        self.assertIn("calibration-policy.json", self.memory.model_check()["changed_dependencies"])
        inputs = self.model["cases"][0]["inputs"]
        self.assertEqual(self.memory.model_predict(inputs, "needs_review")["status"], "unknown")
        revised = self.memory.model_get()["model"]
        with self.assertRaisesRegex(ValueError, "fresh test evidence"):
            self.memory.model_propose(revised, base=accepted)
        self.assertEqual(self.memory.model_reduce()["status"], "blocked")
        evidence = self.memory.save("Rechecked the unchanged-source case against the new calibration policy.")
        revised["cases"].append({**revised["cases"][0], "id": "new-policy-check",
                                "evidence": [evidence], "challenge": True})
        self.accept(revised, base=accepted)
        self.assertEqual(self.memory.model_predict(inputs, "needs_review")["status"], "predicted")

    def test_changed_or_deleted_ignored_dependency_blocks_acceptance(self):
        (self.path / ".gitignore").write_text("policy.txt\n")
        proposal = self.memory.model_propose(self.model)
        (self.path / "policy.txt").unlink()
        self.assertEqual(self.memory.model_check(proposal)["status"], "blocked")
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.memory.model_accept(proposal)

    def test_evidence_deletion_blocks_use_and_keeps_model(self):
        accepted = self.accept()
        self.memory.delete(self.evidence)
        report = self.memory.model_check()
        self.assertEqual(report["missing_evidence"], [self.evidence])
        self.assertEqual(self.memory.model_get()["id"], accepted)

    def test_competing_proposals_cannot_overwrite_newer_model(self):
        first = self.accept()
        old = self.memory.model_get()["model"]
        a = self.memory.model_propose(old, base=first)
        b = self.memory.model_propose(old, base=first)
        current = self.memory.model_accept(a)
        with self.assertRaisesRegex(ValueError, "base changed"):
            self.memory.model_accept(b)
        self.assertEqual(self.memory.model_get()["id"], current)
        with self.assertRaisesRegex(ValueError, "base is required"):
            self.memory.model_propose(old)

    def test_branch_and_inspect_use_the_model_at_the_git_snapshot(self):
        first = self.accept()
        reduction = self.memory.model_reduce()
        second = self.memory.model_accept(reduction["id"])
        self.memory.inspect(first)
        self.assertEqual(self.memory.model_get()["id"], first)
        with self.assertRaises(RuntimeError):
            self.memory.model_propose(self.model, base=first)
        self.memory.inspect_done()
        self.assertEqual(self.memory.model_get()["id"], second)
        self.memory.branch(first, "older-model", "Inspect the earlier rule set.")
        self.assertEqual(self.memory.model_get()["id"], first)

    def test_unseparated_alternative_blocks_a_disagreeing_prediction(self):
        self.model["cases"] = [case for case in self.model["cases"] if case["inputs"]["artifact"] == "app.py"]
        self.model["rules"] = [self.model["rules"][0]]
        alternative_rule = copy.deepcopy(self.model["rules"][0])
        alternative_rule["when"] = value(True)
        alternative_rule["then"] = op("and", op("eq", var("artifact"), value("app.py")), alternative_rule["then"])
        self.model["alternatives"] = [{"id": "only-app", "claim": "Only app.py claims need review.", "rules": [alternative_rule]}]
        first = self.accept()
        generalized = self.memory.model_get()["model"]
        generalized["rules"][0]["when"] = value(True)
        self.accept(generalized, base=first)
        inputs = {"artifact": "config.json", "before": {"state": "missing"}, "after": {"state": "file"}}
        self.assertEqual(self.memory.model_predict(inputs, "needs_review")["status"], "ambiguous")
        current = self.memory.model_get()
        evidence = self.memory.save("A newly created config.json also changes a claim source.")
        current["model"]["cases"].append({"id": "config-counterexample", "target": "needs_review", "inputs": inputs,
                                          "expected": True, "evidence": [evidence], "challenge": True})
        self.accept(current["model"], base=current["id"])
        self.assertEqual(self.memory.model_check()["alternatives"][0]["status"], "rejected")
        self.assertEqual(self.memory.model_predict(inputs, "needs_review")["value"], True)

    def test_unknown_inputs_and_condition_domain_return_unknown(self):
        self.accept()
        self.assertEqual(self.memory.model_predict({}, "needs_review")["status"], "unknown")
        inputs = dict(self.model["cases"][0]["inputs"], artifact="unseen.txt")
        self.assertEqual(self.memory.model_predict(inputs, "needs_review")["reason"], "No applicable rule")

    def test_unsafe_expression_and_external_dependencies_are_rejected(self):
        for expr in ({"op": "exec", "args": [value("touch nope")]},
                     {"op": "eq", "args": []}):
            model = copy.deepcopy(self.model)
            model["rules"][0]["then"] = expr
            with self.assertRaises(ValueError):
                self.memory.model_propose(model)
        for path in ("../outside", "/etc/passwd", ".agent_memory/config"):
            model = copy.deepcopy(self.model)
            model["rules"][0]["dependencies"] = [path]
            with self.assertRaises(ValueError):
                self.memory.model_propose(model)

    def test_cross_branch_evidence_and_forged_acceptance_are_rejected(self):
        common = self.evidence
        side_note = self.memory.save("Side branch evidence.")
        self.memory.branch(common, "other", "Leave the side evidence.")
        self.model["cases"][0]["evidence"] = [side_note]
        with self.assertRaisesRegex(ValueError, "current branch"):
            self.memory.model_propose(self.model)
        with self.assertRaisesRegex(ValueError, "model_propose"):
            self.memory.save("Forged model", {"model_event": "accepted"})

    def test_acceptance_detects_a_concurrent_commit_during_checks(self):
        proposal = self.memory.model_propose(self.model)
        original = TaskModels.check
        def interrupted(service, **kwargs):
            result = original(service, **kwargs)
            service.memory.save("Concurrent worker changed memory.")
            return result
        with patch.object(TaskModels, "check", interrupted):
            with self.assertRaisesRegex(ValueError, "changed during model checks"):
                self.memory.model_accept(proposal)
        self.assertIsNone(self.memory.model_get())

    def test_lean_packet_retains_goal_steering_questions_and_raw_evidence(self):
        goal = self.memory.save_user("Implement exact note storage.\nKeep the API stable.")
        steering = self.memory.save_user("Also preserve old stores.")
        question = self.memory.open_question("Which older format needs migration?")
        self.memory.save("Next: test the migration reader", {"handoff": True})
        self.accept()
        packet = self.memory.render_packet("Source-state tests")
        self.assertIn("## Task model", packet)
        self.assertIn("tested", packet)
        self.assertIn(goal[:8], packet)
        self.assertIn(steering[:8], packet)
        self.assertIn(question[:8], packet)
        self.assertNotIn(self.evidence[:8], packet)
        self.assertLessEqual(len(packet), 1200)
        self.assertTrue(self.memory.find(ids=self.evidence))
        (self.path / "policy.txt").write_text("Changed policy")
        self.assertIn("blocked", self.memory.render_packet("Source-state tests"))

    def test_generic_tool_and_mcp_have_one_model_interface(self):
        result = json.loads(self.memory.call("memory", {"action": "model_propose", "payload": self.model}))
        proposal = result["id"]
        self.assertEqual(json.loads(self.memory.call("memory", {"action": "model_check", "id": proposal}))["status"], "tested")
        self.assertEqual(json.loads(self.memory.call("memory", {"action": "model_accept", "id": proposal}))["status"], "accepted")
        from agent_memory.cursor.mcp import _build_server
        self.assertIn("memory_model", _build_server()._tool_manager._tools)

    def test_challenge_and_case_coverage_are_required(self):
        model = copy.deepcopy(self.model)
        for case in model["cases"]:
            case["challenge"] = False
        proposal = self.memory.model_propose(model)
        self.assertEqual(self.memory.model_check(proposal)["status"], "blocked")
        model = copy.deepcopy(self.model)
        model["rules"].append({**model["rules"][0], "id": "unseen", "when": op("eq", var("artifact"), value("other.txt"))})
        proposal = self.memory.model_propose(model)
        self.assertIn("unseen", self.memory.model_check(proposal)["uncovered_rules"])

    def test_collection_rule_checks_dependency_sets_and_keeps_unknowns(self):
        rule = {"id": "dependencies", "target": "fresh", "when": value(True),
                "then": {"op": "all", "items": var("dependencies"), "as": "dep",
                         "test": op("eq", var("dep.saved"), var("dep.current"))}}
        self.assertEqual(prediction([rule], {"dependencies": [{"saved": "a", "current": "a"}]}, "fresh")["value"], True)
        self.assertEqual(prediction([rule], {"dependencies": [{"saved": "a", "current": "b"}]}, "fresh")["value"], False)
        self.assertEqual(prediction([rule], {"dependencies": [{"saved": "a"}]}, "fresh")["status"], "unknown")

    def test_summary_does_not_remove_active_model_or_its_evidence(self):
        accepted = self.accept()
        self.memory.summarize("Source review model tested; no implementation work remains.")
        self.assertEqual(self.memory.model_get()["id"], accepted)
        self.assertEqual(self.memory.model_check()["status"], "tested")
        self.assertIn("## Task model", self.memory.render_state())
        self.assertTrue(self.memory.find(ids=self.evidence))

    def test_alternatives_cannot_be_deleted_to_make_a_revision_appear_certain(self):
        self.model["alternatives"] = [{"id": "same", "claim": "Another explanation has the same predictions.",
                                       "rules": copy.deepcopy(self.model["rules"])}]
        first = self.accept()
        model = self.memory.model_get()["model"]
        model["alternatives"] = []
        with self.assertRaisesRegex(ValueError, "Retain alternatives"):
            self.memory.model_propose(model, base=first)

    def test_interpreter_limits_and_nonfinite_values_are_explicit(self):
        model = copy.deepcopy(self.model)
        model["rules"][0]["then"] = value(float("nan"))
        with self.assertRaises(ValueError):
            self.memory.model_propose(model)
        model = copy.deepcopy(self.model)
        expr = value(True)
        for _ in range(20):
            expr = op("not", expr)
        model["rules"][0]["then"] = expr
        with self.assertRaisesRegex(ValueError, "interpreter limit"):
            self.memory.model_propose(model)
        model = copy.deepcopy(self.model)
        model["rules"][0]["then"] = var("undeclared")
        with self.assertRaisesRegex(ValueError, "undeclared"):
            self.memory.model_propose(model)

    def test_mcp_model_tool_executes_the_full_lifecycle(self):
        import os
        from agent_memory.cursor.mcp import _build_server
        server = _build_server()
        fn = server._tool_manager._tools["memory_model"].fn
        with patch.dict(os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}):
            proposal = json.loads(fn(operation="propose", data=self.model))["id"]
            self.assertEqual(json.loads(fn(operation="check", id=proposal))["status"], "tested")
            accepted = json.loads(fn(operation="accept", id=proposal))["id"]
            self.assertEqual(json.loads(fn())["id"], accepted)
            result = json.loads(fn(operation="predict", data=self.model["cases"][0]["inputs"], target="needs_review"))
            self.assertEqual(result["value"], False)

    def test_tool_transfer_omits_fixtures_and_carries_them_into_next_revision(self):
        first = self.accept()
        lean = json.loads(self.memory.call("memory", {"action": "model_get"}))
        self.assertNotIn("cases", lean["model"])
        self.assertNotIn("cases", lean["checks"])
        self.assertEqual(lean["case_count"], 6)
        full = json.loads(self.memory.call("memory", {"action": "model_get", "full": True}))
        self.assertEqual(len(full["model"]["cases"]), 6)
        self.assertLess(len(json.dumps(lean)), len(json.dumps(full)))
        proposal = self.memory.model_propose(lean["model"], base=first)
        self.assertEqual(len(self.memory.model_get(proposal)["model"]["cases"]), 6)

    def test_normal_state_does_not_load_the_model_archive(self):
        first = self.accept()
        result = self.memory.model_reduce()
        second = self.memory.model_accept(result["id"])
        self.assertEqual([item.id for item in self.memory.state()], [self.evidence])
        self.assertEqual(self.memory.model_get()["id"], second)
        history = {item.id for item in self.memory.history()}
        self.assertIn(first, history)
        self.assertIn(second, history)

    def test_nested_json_types_do_not_merge_distinct_states(self):
        rule = {"id": "state", "target": "same", "when": value(True),
                "then": op("eq", var("before"), var("after"))}
        result = prediction([rule], {"before": {"enabled": True}, "after": {"enabled": 1}}, "same")
        self.assertEqual(result["value"], False)
        result = prediction([rule], {"before": {"x": 1, "y": 2}, "after": {"y": 2, "x": 1}}, "same")
        self.assertEqual(result["value"], True)

    def test_model_lineage_uses_commit_sequence_when_clock_moves_back(self):
        import os
        first = self.accept()
        model = self.memory.model_get()["model"]
        with patch.dict(os.environ, {"GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                                     "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00"}):
            second = self.accept(model, base=first)
        self.assertEqual(self.memory.model_get()["id"], second)
        self.assertEqual(self.memory.model_get()["parent"], first)
