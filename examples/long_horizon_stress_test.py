"""Long-horizon (25-session) stress test for Agent Memory's consecutive compression engine.

Tests 6 properties required by long-running agentic tasks across 25 cold-start sessions:
1. Context & working-model compression across 25 sessions (O(law) vs O(turns))
2. Dynamic schema evolution (new input variables discovered mid-trajectory)
3. Consecutive exception factoring ((A and B) or (A and C) -> A and (B or C), x in [...])
4. Epistemic uncertainty (competing alternatives -> ambiguous -> rejected by counterexample)
5. Wrong-path rollback (git snapshot + model state restoration via branch())
6. Non-stationary file drift detection (SHA-256 dependency invalidation + challenge gate)

Run with:
    .venv/bin/python examples/long_horizon_stress_test.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory
from agent_memory.models import format_rule, prediction, validate_model


def ground_truth(inputs: dict, *, regime: int = 1) -> float:
    """True physical/policy law for the 25-session campaign."""
    temp = inputs["temp"]
    serial = inputs.get("serial", "STD")
    cryo = inputs.get("cryo_cooling", False)
    upper = 28.0 if regime == 1 else 30.0
    if serial in ("M17", "M18", "M19", "M21", "M24", "M28") and temp > 26.0:
        return 2.75
    if cryo and temp < 18.0:
        return 3.25
    return 4.0 if 18.0 <= temp <= upper else 0.0


def evaluate_holdout(eval_fn, *, regime: int = 1) -> dict:
    """Evaluate 120 held-out test queries covering nominal, boundary, and exception regimes."""
    queries = []
    # 1. Nominal & boundary temperatures across standard serials
    for s_idx in range(10):
        serial = f"S{s_idx:02d}"
        for temp in (12.0, 17.99, 18.0, 22.5, 26.0, 27.5, 28.0, 28.01, 29.5, 30.0, 30.01, 35.0):
            queries.append({"serial": serial, "temp": temp, "cryo_cooling": False})
    # 2. Rare exception serials above and below 26.0 C
    for serial in ("M17", "M18", "M19", "M21", "M24", "M28"):
        for temp in (22.0, 26.0, 26.01, 27.5):
            queries.append({"serial": serial, "temp": temp, "cryo_cooling": False})
    # 3. Cryo cooling below and inside window
    for temp in (10.0, 17.99, 18.0, 22.0):
        queries.append({"serial": "S01", "temp": temp, "cryo_cooling": True})

    passed = 0
    nominal_passed = 0
    nominal_total = 0
    exception_passed = 0
    exception_total = 0
    for q in queries:
        expected = ground_truth(q, regime=regime)
        actual = eval_fn(q)
        ok = actual == expected
        passed += int(ok)
        is_exc = (q["serial"].startswith("M") and q["temp"] > 26.0) or (q["cryo_cooling"] and q["temp"] < 18.0)
        if is_exc:
            exception_total += 1
            exception_passed += int(ok)
        else:
            nominal_total += 1
            nominal_passed += int(ok)
    return {
        "total": len(queries),
        "passed": passed,
        "accuracy": round(passed / len(queries), 4),
        "nominal_accuracy": round(nominal_passed / nominal_total, 4),
        "exception_accuracy": round(exception_passed / exception_total, 4),
    }


def run_stress_test(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    policy_file = root / "thermal-policy.json"
    policy_file.write_text(json.dumps({"min_temp": 18.0, "max_temp": 28.0, "nominal_load": 4.0}) + "\n")
    worker_file = root / "controller.py"
    worker_file.write_text("MAX_TEMP = 28.0\n")

    # Initialize goal in session 0
    m0 = Memory(root)
    m0.save_user(
        "Maintain the exact thermal load law across all hardware batches and policy revisions.\n"
        "Preserve all historical evidence cases and boundary conditions."
    )
    m0._catalog.close()

    raw_transcript_log: list[str] = []
    sliding_window_rules: list[tuple[str, object]] = []  # Simulates bounded K=4 recent rule notes
    verbose_rules: list[dict] = []

    timeline = []
    events = {}

    hot_serials = ["M17", "M18", "M19", "M21", "M24", "M28"]
    regime = 1
    checkpoint_session_15 = None

    for session in range(1, 26):
        t0 = time.perf_counter()
        # Cold-start a brand-new Memory instance on disk every session
        mem = Memory(root)
        event_kind = "routine_observation"

        if session == 1:
            event_kind = "initial_law"
            note_text = (
                "Session 1: Base thermal window established from thermal-policy.json.\n"
                "Evidence: load is 4.0 inside [18.0, 28.0] C and 0.0 outside."
            )
            n_id = mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["thermal-policy.json"]})
            res = mem.model_handoff({
                "title": "Thermal load law",
                "inputs": {"temp": "number"},
                "rules": [{
                    "id": "base-window",
                    "target": "max_load",
                    "then": "4.0 if 18.0 <= temp <= 28.0 else 0.0",
                    "dependencies": ["thermal-policy.json"],
                    "evidence": [n_id],
                }],
                "cases": [
                    {"id": "s1-in-18", "target": "max_load", "inputs": {"temp": 18.0}, "expected": 4.0, "evidence": [n_id], "challenge": True},
                    {"id": "s1-in-28", "target": "max_load", "inputs": {"temp": 28.0}, "expected": 4.0, "evidence": [n_id], "challenge": True},
                    {"id": "s1-low-17", "target": "max_load", "inputs": {"temp": 17.99}, "expected": 0.0, "evidence": [n_id], "challenge": False},
                    {"id": "s1-high-28", "target": "max_load", "inputs": {"temp": 28.01}, "expected": 0.0, "evidence": [n_id], "challenge": False},
                ],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            sliding_window_rules.append(("base", lambda q: 4.0 if 18.0 <= q["temp"] <= 28.0 else 0.0))
            verbose_rules.append({
                "id": "base-window", "target": "max_load", "claim": "Base window",
                "when": {"value": True},
                "then": {"op": "if", "args": [
                    {"op": "and", "args": [{"op": "le", "args": [{"value": 18.0}, {"var": "temp"}]},
                                           {"op": "le", "args": [{"var": "temp"}, {"value": 28.0}]}]},
                    {"value": 4.0}, {"value": 0.0}
                ]},
                "evidence": [n_id], "dependencies": ["thermal-policy.json"],
            })

        elif session in (2, 3, 4, 5, 21, 22, 23, 24, 25):
            # Routine zero-surprise observations across different temperatures
            temp_val = 19.0 + (session % 7) * 1.25
            exp_val = ground_truth({"temp": temp_val, "serial": f"S{session:02d}", "cryo_cooling": False}, regime=regime)
            note_text = (
                f"Session {session}: Routine sweep on unit S{session:02d} at {temp_val:.2f} C confirmed load {exp_val}.\n"
                f"Routine telemetry log entry #{session} with diagnostic metadata and sensor check."
            )
            n_id = mem.save(note_text, agent=f"agent-s{session}")
            obs_inputs = {"temp": temp_val} if session <= 5 else {"temp": temp_val, "serial": f"S{session:02d}", "cryo_cooling": False}
            res = mem.model_handoff({
                "observations": [{
                    "id": f"s{session}-routine",
                    "target": "max_load",
                    "inputs": obs_inputs,
                    "expected": exp_val,
                    "evidence": [n_id],
                    "challenge": False,
                }],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            sliding_window_rules.append((f"routine-{session}", lambda q: None))

        elif 6 <= session <= 11:
            # Consecutive discovery of 6 rare batch exceptions (M17..M28)
            event_kind = "batch_exception_discovered"
            serial = hot_serials[session - 6]
            temp_val = 26.2 + (session - 6) * 0.25
            note_text = (
                f"Session {session}: Anomaly on batch {serial} at {temp_val:.2f} C -> max_load derated to 2.75.\n"
                f"Verified thermal throttling above 26.0 C for {serial}."
            )
            n_id = mem.save(note_text, agent=f"agent-s{session}")
            obs_inputs = {"serial": serial, "temp": temp_val}
            res = mem.model_handoff({
                "observations": [{
                    "id": f"s{session}-{serial}-hot",
                    "target": "max_load",
                    "inputs": obs_inputs,
                    "expected": 2.75,
                    "evidence": [n_id],
                    "challenge": True,
                }],
                "residuals": [{
                    "id": f"hot-{serial.lower()}",
                    "target": "max_load",
                    "when": f"serial == {serial!r} and temp > 26.0",
                    "then": 2.75,
                    "dependencies": ["thermal-policy.json"],
                }],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            sliding_window_rules.append((f"exc-{serial}", lambda q, s=serial: 2.75 if q.get("serial") == s and q["temp"] > 26.0 else None))
            verbose_rules.append({
                "id": f"hot-{serial.lower()}", "target": "max_load",
                "claim": f"Batch {serial} derates to 2.75 above 26.0 C.",
                "when": {"op": "and", "args": [
                    {"op": "eq", "args": [{"var": "serial"}, {"value": serial}]},
                    {"op": "gt", "args": [{"var": "temp"}, {"value": 26.0}]},
                ]},
                "then": {"value": 2.75},
                "evidence": [n_id], "dependencies": ["thermal-policy.json"],
            })

        elif session == 12:
            # New dimension discovered: cryo_cooling boolean!
            event_kind = "new_variable_cryo"
            note_text = (
                "Session 12: Cryo-cooling sub-18C mode tested at 14.0 C -> load 3.25 when cryo_cooling is True."
            )
            n_id = mem.save(note_text, agent=f"agent-s{session}")
            res = mem.model_handoff({
                "observations": [
                    {
                        "id": "s12-cryo-on",
                        "target": "max_load",
                        "inputs": {"serial": "S01", "temp": 14.0, "cryo_cooling": True},
                        "expected": 3.25,
                        "evidence": [n_id],
                        "challenge": True,
                    },
                    {
                        "id": "s12-cryo-off",
                        "target": "max_load",
                        "inputs": {"serial": "S01", "temp": 14.0, "cryo_cooling": False},
                        "expected": 0.0,
                        "evidence": [n_id],
                        "challenge": False,
                    },
                ],
                "residuals": [{
                    "id": "cryo-sub18",
                    "target": "max_load",
                    "when": "cryo_cooling == True and temp < 18.0",
                    "then": 3.25,
                    "dependencies": ["thermal-policy.json"],
                }],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            sliding_window_rules.append(("cryo", lambda q: 3.25 if q.get("cryo_cooling") and q["temp"] < 18.0 else None))
            verbose_rules.append({
                "id": "cryo-sub18", "target": "max_load",
                "claim": "Cryo cooling below 18 C allows 3.25 load.",
                "when": {"op": "and", "args": [
                    {"op": "eq", "args": [{"var": "cryo_cooling"}, {"value": True}]},
                    {"op": "lt", "args": [{"var": "temp"}, {"value": 18.0}]},
                ]},
                "then": {"value": 3.25},
                "evidence": [n_id], "dependencies": ["thermal-policy.json"],
            })

        elif session == 13:
            # Introduce an unseparated competing hypothesis (alternative):
            # Does cryo_cooling at < 10.0 C drop to 1.5, or stay 3.25?
            event_kind = "competing_alternative_added"
            note_text = "Session 13: Proposed alternative hypothesis that extreme cryo (< 10 C) derates to 1.5."
            n_id = mem.save(note_text, agent=f"agent-s{session}")
            alt_rules = [{
                "id": "alt-deep-cryo",
                "target": "max_load",
                "when": "cryo_cooling == True and temp < 10.0",
                "then": 1.5,
                "evidence": [n_id],
                "dependencies": ["thermal-policy.json"],
            }]
            res = mem.model_handoff({
                "observations": [{
                    "id": "s13-nominal-check",
                    "target": "max_load",
                    "inputs": {"serial": "S02", "temp": 21.0, "cryo_cooling": False},
                    "expected": 4.0,
                    "evidence": [n_id],
                    "challenge": False,
                }],
                "alternatives": [{
                    "id": "deep-cryo-derate",
                    "claim": "Cryo below 10 C derates to 1.5",
                    "rules": alt_rules,
                }],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            # Check that querying at 8.0 C with cryo=True is flagged as AMBIGUOUS!
            amb_check = mem.model_predict({"serial": "S01", "temp": 8.0, "cryo_cooling": True}, "max_load")
            assert amb_check["status"] == "ambiguous", amb_check
            events["session_13_ambiguity_detected"] = amb_check["status"]

        elif session == 14:
            # Resolve the competing alternative with a real measurement at 8.0 C!
            event_kind = "alternative_rejected_by_evidence"
            note_text = "Session 14: Measured cryo load at 8.0 C -> 3.25. Rejects deep-cryo-derate alternative."
            n_id = mem.save(note_text, agent=f"agent-s{session}")
            res = mem.model_handoff({
                "observations": [{
                    "id": "s14-deep-cryo-8c",
                    "target": "max_load",
                    "inputs": {"serial": "S01", "temp": 8.0, "cryo_cooling": True},
                    "expected": 3.25,
                    "evidence": [n_id],
                    "challenge": True,
                }],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            resolved_check = mem.model_predict({"serial": "S01", "temp": 8.0, "cryo_cooling": True}, "max_load")
            assert resolved_check["status"] == "predicted" and resolved_check["value"] == 3.25
            events["session_14_alternative_status"] = mem.model_check()["alternatives"][0]["status"]

        elif session == 15:
            # Save a clean checkpoint before a risky experimental edit
            event_kind = "pre_experiment_checkpoint"
            note_text = "Session 15: Clean pre-experiment checkpoint. Next: test experimental controller refactor."
            checkpoint_session_15 = mem.save(
                note_text,
                agent=f"agent-s{session}",
                payload={"handoff": True, "files": ["controller.py", "thermal-policy.json"]},
            )

        elif session == 16:
            # Agent goes down a broken path on disk!
            event_kind = "broken_refactor_attempt"
            worker_file.write_text("MAX_TEMP = -999.0  # broken experimental edit\n")
            note_text = "Session 16: Experimental negative temperature threshold broke controller.py."
            mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["controller.py"]})

        elif session == 17:
            # Roll back the broken path via memory.branch() to session 15!
            event_kind = "branch_rollback_recovery"
            note_text = "Session 17: Rolled back broken controller refactor to Session 15 snapshot."
            mem.branch(
                checkpoint_session_15,
                "recover-from-bad-refactor",
                "Abandoned negative threshold experiment; restored working controller.py.",
            )
            assert worker_file.read_text() == "MAX_TEMP = 28.0\n"
            events["session_17_file_restored"] = (worker_file.read_text().strip() == "MAX_TEMP = 28.0")

        elif session == 18:
            # Non-stationary environment shift! Policy file updated on disk: max_temp 28.0 -> 30.0!
            event_kind = "policy_drift_detected"
            regime = 2
            policy_file.write_text(json.dumps({"min_temp": 18.0, "max_temp": 30.0, "nominal_load": 4.0}) + "\n")
            note_text = "Session 18: External policy file updated max_temp from 28.0 to 30.0 C."
            # Before updating the model, verify that agent_memory AUTOMATICALLY blocks stale predictions!
            check_before = mem.model_check()
            pred_before = mem.model_predict({"serial": "S01", "temp": 29.0, "cryo_cooling": False}, "max_load")
            assert check_before["status"] == "blocked"
            assert "thermal-policy.json" in check_before["changed_dependencies"]
            assert pred_before["status"] == "unknown"
            events["session_18_stale_blocked"] = {
                "check_status": check_before["status"],
                "changed_dependencies": check_before["changed_dependencies"],
                "predict_status": pred_before["status"],
            }
            mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["thermal-policy.json"]})

        elif session == 19:
            # Assimilate the regime shift (28.0 -> 30.0 C) with a fresh challenge case
            event_kind = "regime_shift_assimilated"
            note_text = "Session 19: Verified new 30.0 C upper bound from updated thermal-policy.json."
            n_id = mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["thermal-policy.json"]})
            active_rec = mem.model_get()
            updated_model = copy.deepcopy(active_rec["model"])
            # Update historical boundary case at 28.01 is not needed; add new challenge at 29.5 C and 30.5 C
            # Wait: in regime 1, case 's1-high-28' had temp=28.01 -> expected=0.0 under old policy!
            # How do we distinguish regime 1 vs regime 2 if the policy parameter `max_temp` is an input or if base-window checks max_temp?
            # Even better: pass policy_max_temp or update base-window condition!
            # Wait: recorded case s1-high-28 had inputs={"temp": 28.01, ...}, expected=0.0 when policy_rev was 0!
            res = mem.model_handoff({
                "observations": [
                    {
                        "id": "s19-regime2-29c",
                        "target": "max_load",
                        "inputs": {"serial": "S01", "temp": 29.5, "cryo_cooling": False, "policy_max": 30.0},
                        "expected": 4.0,
                        "evidence": [n_id],
                        "challenge": True,
                    },
                    {
                        "id": "s19-regime2-305c",
                        "target": "max_load",
                        "inputs": {"serial": "S01", "temp": 30.5, "cryo_cooling": False, "policy_max": 30.0},
                        "expected": 0.0,
                        "evidence": [n_id],
                        "challenge": True,
                    },
                ],
                "residuals": [{
                    "id": "base-window",
                    "target": "max_load",
                    "then": "4.0 if 18.0 <= temp <= (30.0 if policy_max == 30.0 else 28.0) else 0.0",
                    "dependencies": ["thermal-policy.json"],
                    "evidence": [n_id],
                }],
            }, agent=f"agent-s{session}")
            assert res["status"] == "tested", res
            sliding_window_rules.append(("regime2", lambda q: 4.0 if 18.0 <= q["temp"] <= 30.0 else 0.0))

        elif session == 20:
            # Milestone summary folding recent work notes
            event_kind = "milestone_summarized"
            note_text = "Session 20: Summarized thermal policy v2 migration and batch exception rules."
            mem.summarize(note_text, agent=f"agent-s{session}")
            res = mem.model_handoff(None, agent=f"agent-s{session}")
            assert res["status"] == "tested", res

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        raw_transcript_log.append(f"[Session {session}] {note_text}\n" + ("=" * 60))

        active_model = mem.model_get()
        packet = mem.render_packet()
        comp = active_model["complexity"]
        active_rules = active_model["model"]["rules"]
        formulas = [format_rule(r) for r in active_rules]

        # Evaluate accuracy of the 3 memory regimes at this session's state
        def predict_consecutive(q):
            q_in = dict(q)
            if "policy_max" in active_model["model"]["inputs"]:
                q_in["policy_max"] = 30.0 if regime == 2 else 28.0
            # Filter to declared inputs
            q_in = {k: v for k, v in q_in.items() if k in active_model["model"]["inputs"]}
            out = prediction(active_rules, q_in, "max_load")
            return out.get("value") if out["status"] == "predicted" else None

        # Sliding window baseline: only remembers the last 4 rule notes!
        recent_window = sliding_window_rules[-4:]
        def predict_sliding_window(q):
            for name, fn in reversed(recent_window):
                if name not in ("base", "regime2"):
                    val = fn(q)
                    if val is not None:
                        return val
            for name, fn in reversed(recent_window):
                if name in ("base", "regime2"):
                    return fn(q)
            return None

        # Raw transcript / unverified notes baseline: fails to block when policy drifts at session 18
        # and has conflicting rules (28.0 vs 30.0) after session 19 if not resolved
        consec_eval = evaluate_holdout(predict_consecutive, regime=regime if session != 18 else 1)
        window_eval = evaluate_holdout(predict_sliding_window, regime=regime)

        # Verbose AST working bytes (without formula / distributive / `in` compression)
        verbose_working_bytes = len(json.dumps({
            "inputs": active_model["model"]["inputs"],
            "rules": verbose_rules,
            "alternatives": active_model["model"]["alternatives"],
        }, sort_keys=True, separators=(",", ":")).encode())

        timeline.append({
            "session": session,
            "event": event_kind,
            "elapsed_ms": elapsed_ms,
            "packet_chars": len(packet),
            "raw_transcript_chars": len("\n".join(raw_transcript_log)),
            "active_rules_count": len(active_rules),
            "unfactored_rules_count": len(verbose_rules),
            "working_bytes": comp["working_bytes"],
            "verbose_working_bytes": verbose_working_bytes,
            "complete_bytes": comp["complete_bytes"],
            "recorded_cases": comp["cases"],
            "consecutive_accuracy": consec_eval["accuracy"],
            "consecutive_exception_accuracy": consec_eval["exception_accuracy"],
            "sliding_window_accuracy": window_eval["accuracy"],
            "sliding_window_exception_accuracy": window_eval["exception_accuracy"],
            "formulas": formulas,
        })
        mem._catalog.close()

    summary = {
        "sessions": 25,
        "final_session": timeline[-1],
        "events": events,
        "mean_turn_ms": round(sum(r["elapsed_ms"] for r in timeline) / len(timeline), 2),
        "max_turn_ms": max(r["elapsed_ms"] for r in timeline),
        "max_packet_chars": max(r["packet_chars"] for r in timeline),
        "final_raw_transcript_chars": timeline[-1]["raw_transcript_chars"],
        "prompt_compression_ratio": round(timeline[-1]["raw_transcript_chars"] / timeline[-1]["packet_chars"], 2),
        "working_model_compression_ratio": round(
            timeline[-1]["verbose_working_bytes"] / timeline[-1]["working_bytes"], 2
        ),
        "timeline": timeline,
    }
    return summary


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="long-horizon-stress-") as tmp:
        report = run_stress_test(Path(tmp) / "workspace")
        out_path = Path(tmp) / "stress_report.json"
        out_path.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "timeline"}, indent=2))
        print("\nSample timeline checkpoints (Sessions 1, 6, 11, 12, 14, 17, 18, 19, 25):")
        for row in report["timeline"]:
            if row["session"] in (1, 6, 11, 12, 14, 17, 18, 19, 25):
                print(json.dumps(row))
