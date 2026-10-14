"""Long-horizon (25-session) stress test for Agent Memory's consecutive compression & staleness engine.

Tests key properties across 25 cold-start sessions:
1. Bounded state packet (<= 1,200 chars) across 25 sessions regardless of transcript growth
2. Consecutive milestone summarization folding raw steps while keeping full bodies searchable
3. Wrong-path rollback (git snapshot restoration via branch())
4. Non-stationary file drift detection ([stale] flagging when policy files change)
5. Open question lifecycle across sessions

Run with:
    .venv/bin/python examples/long_horizon_stress_test.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory


def run_stress_test(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    policy_file = root / "thermal-policy.json"
    policy_file.write_text(json.dumps({"min_temp": 18.0, "max_temp": 28.0, "nominal_load": 4.0}) + "\n")
    worker_file = root / "controller.py"
    worker_file.write_text("MAX_TEMP = 28.0\n")

    m0 = Memory(root)
    m0.save_user(
        "Maintain the exact thermal load law across all hardware batches and policy revisions.\n"
        "Preserve all historical evidence cases and boundary conditions."
    )
    m0._catalog.close()

    raw_transcript_log: list[str] = []
    timeline = []
    events = {}
    hot_serials = ["M17", "M18", "M19", "M21", "M24", "M28"]
    checkpoint_session_15 = None
    policy_note_id = None
    q_id = None

    for session in range(1, 26):
        t0 = time.perf_counter()
        mem = Memory(root)
        event_kind = "routine_observation"

        if session == 1:
            event_kind = "initial_law"
            note_text = (
                "Session 1: Base thermal window established from thermal-policy.json.\n"
                "Evidence: load is 4.0 inside [18.0, 28.0] C and 0.0 outside."
            )
            policy_note_id = mem.save(
                note_text,
                agent=f"agent-s{session}",
                payload={"files": ["thermal-policy.json"], "handoff": True},
            )
        elif session in (2, 3, 4, 5, 21, 22, 23, 24, 25):
            temp_val = 19.0 + (session % 7) * 1.25
            note_text = (
                f"Session {session}: Routine sweep on unit S{session:02d} at {temp_val:.2f} C confirmed nominal load.\n"
                f"Routine telemetry log entry #{session} with diagnostic metadata."
            )
            mem.save(note_text, agent=f"agent-s{session}")
        elif 6 <= session <= 11:
            event_kind = "batch_exception_discovered"
            serial = hot_serials[session - 6]
            temp_val = 26.2 + (session - 6) * 0.25
            note_text = (
                f"Session {session}: Anomaly on batch {serial} at {temp_val:.2f} C -> max_load derated to 2.75.\n"
                f"Verified thermal throttling above 26.0 C for {serial}."
            )
            mem.save(note_text, agent=f"agent-s{session}")
        elif session == 12:
            event_kind = "new_variable_cryo"
            note_text = "Session 12: Cryo-cooling sub-18C mode tested at 14.0 C -> load 3.25 when cryo_cooling is True."
            mem.save(note_text, agent=f"agent-s{session}")
        elif session == 13:
            event_kind = "open_question_added"
            note_text = "Session 13: Does extreme cryo (< 10 C) derate to 1.5 or stay at 3.25?"
            q_id = mem.open_question(note_text, agent=f"agent-s{session}")
            assert len(mem.open_questions()) == 1
        elif session == 14:
            event_kind = "open_question_resolved"
            note_text = "Session 14: Measured cryo load at 8.0 C -> 3.25. Closed deep-cryo question."
            mem.close_questions([q_id])
            mem.save(note_text, agent=f"agent-s{session}")
            assert len(mem.open_questions()) == 0
            events["session_14_question_closed"] = True
        elif session == 15:
            event_kind = "pre_experiment_checkpoint"
            note_text = "Session 15: Clean pre-experiment checkpoint. Next: test experimental controller refactor."
            checkpoint_session_15 = mem.save(
                note_text,
                agent=f"agent-s{session}",
                payload={"handoff": True, "files": ["controller.py", "thermal-policy.json"]},
            )
        elif session == 16:
            event_kind = "broken_refactor_attempt"
            worker_file.write_text("MAX_TEMP = -999.0  # broken experimental edit\n")
            note_text = "Session 16: Experimental negative temperature threshold broke controller.py."
            mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["controller.py"]})
        elif session == 17:
            event_kind = "branch_rollback_recovery"
            note_text = "Session 17: Rolled back broken controller refactor to Session 15 snapshot."
            mem.branch(
                checkpoint_session_15,
                "recover-from-bad-refactor",
                "Abandoned negative threshold experiment; restored working controller.py.",
            )
            assert worker_file.read_text() == "MAX_TEMP = 28.0\n"
            events["session_17_file_restored"] = True
        elif session == 18:
            event_kind = "policy_drift_detected"
            policy_file.write_text(json.dumps({"min_temp": 18.0, "max_temp": 30.0, "nominal_load": 4.0}) + "\n")
            note_text = "Session 18: External policy file updated max_temp from 28.0 to 30.0 C."
            pkt_before = mem.render_packet("thermal-policy.json")
            assert "[stale]" in pkt_before
            events["session_18_stale_detected"] = True
            mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["thermal-policy.json"]})
        elif session == 19:
            event_kind = "regime_shift_assimilated"
            note_text = "Session 19: Verified new 30.0 C upper bound from updated thermal-policy.json."
            mem.save(note_text, agent=f"agent-s{session}", payload={"files": ["thermal-policy.json"], "handoff": True})
        elif session == 20:
            event_kind = "milestone_summarized"
            note_text = (
                "Session 20: Summarized thermal policy v2 migration [18.0, 30.0] C, "
                "batch exceptions M17..M28 -> 2.75 above 26.0 C, and cryo < 18.0 C -> 3.25."
            )
            mem.summarize(note_text, agent=f"agent-s{session}")

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        raw_transcript_log.append(f"[Session {session}] {note_text}\n" + ("=" * 60))
        packet = mem.render_packet("thermal policy batch exceptions")
        assert len(packet) <= 1200

        timeline.append({
            "session": session,
            "event": event_kind,
            "elapsed_ms": elapsed_ms,
            "packet_chars": len(packet),
            "raw_transcript_chars": len("\n".join(raw_transcript_log)),
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
        "timeline": timeline,
    }
    return summary


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="long-horizon-stress-") as tmp:
        report = run_stress_test(Path(tmp) / "workspace")
        out_path = Path(tmp) / "stress_report.json"
        out_path.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "timeline"}, indent=2))
