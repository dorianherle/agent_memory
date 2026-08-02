"""The loop: a failing test suite in, a tree of branches out.

The Worker writes code, the suite judges it, and when it fails the Thinker picks
which commit the next attempt starts from. Nothing is thrown away: a dead end
is a [FAIL] commit on an abandoned branch, which is exactly what the Thinker
reads before choosing again.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents import Runner
from dotenv import load_dotenv

from controller.roles import (Pivot, thinker_agent, thinker_prompt,
                              worker_agent, worker_prompt)
from controller.tests import run_pytest
from controller.tools import index
from memory import Memory, Tag

# Anchored to this file, so the controller can be launched from anywhere. This
# is where OPENAI_API_KEY and GEMINI_API_KEY come from.
load_dotenv(Path(__file__).parent.parent / ".env")

# Any litellm/ model works; drop the prefix for OpenAI. It must be Gemini 3 or
# newer: 2.5 rejects a request that asks for tools and JSON at once, which is
# every request here, since both agents return a structured report.
MODEL = "litellm/gemini/gemini-3.6-flash"

# A coding turn is read, edit, test, read again. The SDK default of 10 stops
# the Worker mid-thought.
TURNS = 40


class Controller:
    """Runs the Worker/Thinker loop against one repository."""

    def __init__(self, repo_path: str | Path, goal: str, tests: list[str],
                 model: str = MODEL, max_rounds: int = 10) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.goal = goal
        self.tests = list(tests)
        self.max_rounds = max_rounds
        self.memory = Memory(self.repo_path)
        self.worker = worker_agent(self.repo_path, model)
        self.thinker = thinker_agent(self.repo_path, self.memory, model)

    def run(self) -> bool:
        """Work until the target tests pass, or until max_rounds is spent."""
        self._baseline()

        for round_number in range(self.max_rounds):
            print(f"\n--- round {round_number + 1} on {self.memory.branch_name()} ---")
            index(self.repo_path)

            report = self._ask(self.worker, worker_prompt(self.memory, self.goal, self.tests))
            passed, failed, output = run_pytest(self.repo_path)
            print(f"{report.summary.splitlines()[0]}")
            print(f"passed {len(passed)}, failing: {' '.join(failed) or 'nothing'}")

            self.memory.record(report.summary, passed=passed, failed=failed,
                               metrics=report.numbers)

            if not set(self.tests) - set(passed):
                self.memory.record(f"Goal reached: {self.goal}", Tag.MILESTONE,
                                   passed=passed, failed=failed)
                print("goal reached")
                return True

            # Recorded before the Thinker runs, so its own prompt_for() sees
            # this failure through ruled_out() just like every older one.
            self.memory.record(report.diagnosis, Tag.FAIL, passed=passed, failed=failed)

            pivot = self._ask(self.thinker, thinker_prompt(self.memory, self.goal,
                                                          self.tests, output))
            self._pivot(pivot)

        print(f"\nout of rounds after {self.max_rounds}")
        return False

    def _baseline(self) -> None:
        """Commit the untouched project, with what the suite says about it.

        Without this the first round's diff would be the whole repository, and
        there would be no earlier state for the Thinker to retreat to.
        """
        if self.memory.has_commits():
            return
        passed, failed, _ = run_pytest(self.repo_path)
        self.memory.record("Starting state", passed=passed, failed=failed)

    def _ask(self, agent, prompt: str):
        return Runner.run_sync(agent, prompt, max_turns=TURNS).final_output

    def _pivot(self, pivot: Pivot) -> None:
        """Open the branch the Thinker asked for and leave its directive on it."""
        # checkout detaches HEAD, and every memory write refuses to run there.
        if not self.memory.branch_name():
            self.memory.inspect_exit()

        branch = self._branch(pivot.branch_name)
        print(f"pivot to {branch} from {pivot.pivot_commit[:9]}")
        self.memory.branch_from(self._commit(pivot.pivot_commit), branch)
        self.memory.record(pivot.directive, Tag.PLAN)

    def _commit(self, commit: str) -> str:
        """The Thinker's SHA, or the last good state if it invented one."""
        try:
            return self.memory.resolve(commit)
        except ValueError:
            good = self.memory.last_passing()
            return good.id if good else "HEAD"

    def _branch(self, name: str) -> str:
        """A name git will take: no odd characters, and not one already in use."""
        name = re.sub(r"[^\w.-]+", "-", name).strip("-.") or "attempt"
        taken = self.memory.branches()
        unique, n = name, 2
        while unique in taken:
            unique, n = f"{name}-{n}", n + 1
        return unique
