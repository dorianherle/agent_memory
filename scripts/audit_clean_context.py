"""Characterize fresh-chat behavior using disposable, synthetic repositories.

Run: .venv/bin/python scripts/audit_clean_context.py [--output observations.json]

This is an audit probe, not an acceptance suite: output describes current
behavior, including defects. No model calls, credentials, or user files are
used. All check/inspect operations run inside temporary repositories.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_memory import Memory, call_tool, wrap


@contextmanager
def repository():
    with tempfile.TemporaryDirectory(prefix="agent-memory-probe-") as folder:
        root = Path(folder)
        memory = Memory(root)
        try:
            yield memory, root
        finally:
            memory._catalog.close()


def observe() -> dict:
    results = {}

    with repository() as (memory, root):
        memory.save_user("Fix authentication timeouts")
        memory.save("Authentication fix verified")
        memory.summarize("Authentication task completed")
        fresh = Memory(root)
        fresh.save_user("Build invoice export")
        packet = fresh.prompt_payload()
        results["task_isolation"] = {
            "new_request": "Build invoice export",
            "goal": packet["goal"]["text"],
            "steering": packet["steering"]["text"],
        }
        fresh._catalog.close()

    with repository() as (memory, root):
        memory.save_user("Document the build")
        memory.save("Run make verify to validate the project")
        summary = memory.summarize("Build uses make verify")
        fresh = Memory(root)
        packet = fresh.render_state()
        results["completed_knowledge"] = {
            "summary_in_fresh_packet": summary[:8] in packet,
            "build_command_in_fresh_packet": "make verify" in packet,
            "summary_retrievable_by_id": bool(fresh.find(ids=summary)),
        }
        fresh._catalog.close()

    with repository() as (memory, root):
        source = root / "config.txt"
        source.write_text("timeout=30\n")
        memory.save("Initial configuration")
        observation = memory.save("config.txt specifies timeout=30")
        source.write_text("timeout=60\n")
        memory.save("Configuration changed")
        item = next(x for x in memory.prompt_payload()["findings"] if x["id"] == observation)
        results["read_only_observation_freshness"] = {
            "current_file": source.read_text().strip(),
            "old_note": item["text"],
            "marked_stale": item["stale"],
        }

    with repository() as (memory, root):
        source = root / "config.txt"
        source.write_text("timeout=30\n")
        observation = memory.save("config.txt specifies timeout=30")
        source.write_text("timeout=60\n")
        item = next(x for x in memory.prompt_payload()["findings"] if x["id"] == observation)
        results["unsaved_file_freshness"] = {
            "current_file": source.read_text().strip(),
            "marked_stale": item["stale"],
        }

    with repository() as (memory, _):
        memory.save("Authentication now uses a timeout of 30 seconds")
        results["offline_search"] = {
            "single_word_hits": len(memory.find(query="authentication")),
            "separated_word_hits": len(memory.find(query="authentication timeout")),
            "fts_hits_for_same_words": len(memory._catalog.fts("authentication timeout", 5)),
        }

    with repository() as (memory, _):
        for index in range(12):
            memory.save(f"Observation {index}\n" + "Synthetic historical detail. " * 100)
        summary = memory.summarize("Twelve observations summarized")
        expanded = call_tool(memory, "memory", {"action": "find", "ids": [summary]})
        results["summary_lookup_size"] = {
            "characters_returned_for_one_summary": len(expanded),
            "folded_observations_returned": expanded.count("Synthetic historical detail.") // 100,
            "approximate_tokens_len_div_4": (len(expanded) + 3) // 4,
        }

    with repository() as (memory, _):
        memory.save_user("Investigate")
        for index in range(60):
            memory.open_question(f"Question {index}: " + "x" * 85)
        packet = memory.render_state()
        results["packet_budget"] = {
            "open_questions": len(memory.open_questions()),
            "characters": len(packet),
            "approximate_tokens_len_div_4": (len(packet) + 3) // 4,
        }

    with repository() as (memory, root):
        source = root / "work.txt"
        source.write_text("old snapshot")
        checkpoint = memory.save("Old snapshot")
        source.write_text("new verified content")
        memory.check([
            sys.executable, "-c",
            "from pathlib import Path; assert Path('work.txt').read_text() == 'new verified content'",
        ])
        stored = memory._run("show", f"{memory.last_green()}:work.txt")
        results["green_checkpoint_identity"] = {
            "tested_content": source.read_text(),
            "green_snapshot_content": stored,
            "green_still_points_to_old_snapshot": memory.last_green() == checkpoint,
        }

    with repository() as (memory, root):
        source = root / "work.txt"
        source.write_text("old")
        old = memory.save("Old version")
        source.write_text("current")
        memory.save("Current version")
        memory.inspect(old)
        before = source.read_text()
        another_reader = Memory(root)
        results["second_client_during_inspect"] = {
            "before_second_client": before,
            "after_second_client": source.read_text(),
            "inspection_still_active": memory.inspecting() is not None,
        }
        if memory.inspecting() is not None:
            memory.inspect_done()
        another_reader._catalog.close()

    with repository() as (memory, root):
        (root / ".env").write_text("EXAMPLE=synthetic-only\n")
        (root / ".env.local").write_text("EXAMPLE=synthetic-only\n")
        memory.save("Synthetic snapshot exclusion probe")
        files = memory._run("ls-tree", "-r", "--name-only", "HEAD").splitlines()
        results["default_snapshot_exclusions"] = {
            "env_tracked": ".env" in files,
            "env_local_tracked": ".env.local" in files,
        }

    with repository() as (memory, root):
        requests = []

        def complete(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="Completed: build entrypoint is make verify", tool_calls=None,
            ))])

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
        client = wrap(fake_client, memory)
        client.chat.completions.create(messages=[
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier speculative reasoning"},
            {"role": "user", "content": "Current question"},
        ])
        fresh = Memory(root)
        results["wrapper_boundary"] = {
            "earlier_assistant_history_forwarded": any(
                x.get("content") == "Earlier speculative reasoning"
                for x in requests[0]["messages"] if isinstance(x, dict)
            ),
            "finished_answer_persisted": bool(fresh.find(text="Completed: build entrypoint")),
        }
        fresh._catalog.close()

    with repository() as (memory, _):
        memory.save_user("Improve login latency")
        base = memory.save("Baseline implementation")
        memory.branch(base, "retry", "Redis increased login latency under benchmark A.")
        memory.save_user("Build invoice export")
        results["cross_goal_failed_path"] = {
            "new_request": "Build invoice export",
            "injected_avoid": [x.message for x in memory.prompt_sections().get("avoid", [])],
        }

    with repository() as (memory, root):
        other = Memory(root)
        barrier = threading.Barrier(2)

        def capture(client, text):
            original_save = client.save

            def synchronized_save(*args, **kwargs):
                # Force both pre-save goal lookups to complete before either
                # write. This is a controlled, valid concurrent interleaving.
                barrier.wait(timeout=10)
                return original_save(*args, **kwargs)

            client.save = synchronized_save
            return client.save_user(text)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(capture, memory, "Goal A"), executor.submit(capture, other, "Goal B")]
            for future in futures:
                future.result(timeout=20)
        results["concurrent_goal_creation"] = {
            "records_marked_as_original_goal": len(memory.find(payload={"goal": True}, dedupe=False)),
            "expected_by_single_goal_contract": 1,
        }
        other._catalog.close()

    with repository() as (memory, _):
        class InvalidProvider:
            @property
            def fingerprint(self):
                raise ValueError("synthetic provider configuration error")

        memory._provider = InvalidProvider()
        error_text = None
        try:
            memory.save("Durable note before provider error")
        except ValueError as error:
            error_text = str(error)
        memory._provider = None
        results["save_acknowledgement"] = {
            "save_reported_error": error_text,
            "note_persisted_despite_error": bool(memory.find(text="Durable note before provider error")),
        }

    with repository() as (goal_a, root_a), repository() as (goal_b, root_b):
        (root_a / "work.txt").write_text("Goal A baseline")
        baseline = goal_a.save("Goal A baseline")
        (root_b / "work.txt").write_text("Goal B independent progress")
        b_head = goal_b.save("Goal B independent progress")
        (root_a / "work.txt").write_text("Goal A failed attempt")
        goal_a.save("Goal A failed attempt")
        goal_a.branch(baseline, "retry-a", "Goal A attempt failed its acceptance condition.")
        results["independent_git_graphs"] = {
            "different_git_directories": goal_a._git_dir != goal_b._git_dir,
            "goal_a_restored": (root_a / "work.txt").read_text() == "Goal A baseline",
            "goal_b_head_unchanged": goal_b._run("rev-parse", "HEAD") == b_head,
            "goal_b_worktree_unchanged": (root_b / "work.txt").read_text() == "Goal B independent progress",
            "goal_b_notes_absent_from_a": not bool(goal_a.find(text="Goal B", branch=None)),
        }

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    # Standalone probe: disable ambient remote providers and UI helper startup.
    for key in list(os.environ):
        if key.startswith("AGENT_MEMORY_") or key == "GEMINI_API_KEY":
            os.environ.pop(key)
    os.environ["AGENT_MEMORY_NO_REVEAL"] = "1"
    rendered = json.dumps(observe(), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
