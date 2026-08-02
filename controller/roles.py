"""The two agents, and the prompts the memory writes for them.

The Worker changes code. The Thinker decides where the next attempt starts.
Neither one is told the history directly: memory.prompt_for() selects it by
what is failing right now, so these prompts do not grow as the run goes on.

Not named agents.py: running a script inside this package puts the package
directory first on sys.path, and the file would then shadow the Agents SDK it
imports on the line below.
"""

from __future__ import annotations

import re

from agents import Agent
from pydantic import BaseModel

from controller.tools import thinker_tools, worker_tools
from memory import Memory, Tag

# `residual=1e-6`. Anything else the model writes on a metrics line is dropped
# rather than crashing a run that may be hours old.
_METRIC = re.compile(r"^(\w+)=(-?\d+\.?\d*(?:[eE][-+]?\d+)?)$")


class WorkerReport(BaseModel):
    """What the Worker hands back: the commit message, and why it still fails."""
    summary: str
    diagnosis: str
    metrics: list[str]

    @property
    def numbers(self) -> dict[str, float]:
        """The metrics that really are numbers, ready for record(metrics=)."""
        found = (_METRIC.match(pair.strip()) for pair in self.metrics)
        return {match.group(1): float(match.group(2)) for match in found if match}


class Pivot(BaseModel):
    """The Thinker's decision: where to start again, and what to try."""
    pivot_commit: str
    branch_name: str
    directive: str


WORKER = """You are the Worker. You change code until the tests pass.

Do not read files blind. Call explore with a description of what you need and
it returns the relevant source plus what already depends on it; symbol follows
the call graph from there.

Call run_tests before you finish, so what you report is what the suite really
said and not what you expect it to say.

Report three things:
- summary: what you changed and why. It becomes the commit message, and the
  diff beside it is the only record of this attempt.
- diagnosis: if anything still fails, explain it comprehensively. What you
  tried, what the failure actually is, and what you now believe is wrong. The
  next attempt may start from a different commit and see nothing but this.
- metrics: any numbers worth tracking, each as name=value. Pass and fail cannot
  tell nearly-there from diverged; these can.
"""

THINKER = """You are the Thinker. The Worker just failed. You decide where the
next attempt starts from and what it is told to do.

You are shown every dead end on every branch. Read the ones that resemble this
predicament with read_commit: the message is somebody's opinion, the diff is
what happened. checkout any commit to see the whole project as it was then and
explore it there. Call checkout_exit before you answer.

Return:
- pivot_commit: the SHA to branch from. Going backwards is not a defeat. A
  state with fewer tests passing is often the better place to start, and
  carrying on from a wrong turn is how a run dies quietly.
- branch_name: short, lowercase, hyphenated, names the hypothesis.
- directive: what to try, concretely, and why you believe it will work. Do not
  restate the goal and do not propose anything already ruled out. The next
  Worker sees this and little else.
"""


def worker_agent(repo_path, model: str) -> Agent:
    return Agent(
        name="Worker",
        instructions=WORKER,
        model=model,
        tools=worker_tools(repo_path),
        output_type=WorkerReport,
    )


def thinker_agent(repo_path, memory: Memory, model: str) -> Agent:
    return Agent(
        name="Thinker",
        instructions=THINKER,
        model=model,
        tools=thinker_tools(repo_path, memory),
        output_type=Pivot,
    )


def _goal(goal: str, tests: list[str]) -> list[str]:
    return [f"goal:  {goal}", f"tests to pass: {' '.join(tests)}", ""]


def worker_prompt(memory: Memory, goal: str, tests: list[str]) -> str:
    """The goal, the directive in full, and this branch's own commits.

    prompt_for() carries the branch, the failure signature, what is ruled out
    and the standing lessons, but only the subject line of the plan. The Worker
    needs the whole directive, so it is repeated here.
    """
    plan = memory.plan()
    lines = [
        *_goal(goal, tests),
        memory.prompt_for("worker"),
        "",
        "your directive for this attempt:",
        f"  {plan.message}" if plan else "  (none: decide for yourself)",
        "",
        "this branch so far, newest first:",
        *([f"  {entry}" for entry in memory.history()] or ["  (empty)"]),
    ]
    return "\n".join(lines)


def thinker_prompt(memory: Memory, goal: str, tests: list[str], output: str) -> str:
    """Everything the Thinker needs, with the SHAs it has to choose between.

    prompt_for("thinker") names the dead ends but not their commits, and a
    pivot is a commit, so both lists are given here with SHAs attached.
    """
    lines = [
        *_goal(goal, tests),
        memory.prompt_for("thinker"),
        "",
        "places you could branch from:",
        *_candidates(memory),
        "",
        "every dead end, on every branch:",
        *([f"  {e.id[:9]}  {e.subject}"
           for e in memory.history(tag=Tag.FAIL, all_branches=True, limit=0)] or ["  (none)"]),
        "",
        "what the suite just said:",
        output[-3000:],
    ]
    return "\n".join(lines)


def _candidates(memory: Memory) -> list[str]:
    """The best commit per distinct failure signature, plus the last good one.

    One elite per signature rather than one global best, so the odd state that
    pays off later is still on the list when the obvious basin runs dry.
    """
    entries = list(memory.elites().values())
    good = memory.last_passing()
    if good:
        entries.append(good)

    seen, lines = set(), []
    for entry in sorted(entries, key=lambda e: -len(e.passed)):
        if entry.id in seen:
            continue
        seen.add(entry.id)
        lines.append(f"  {entry.id[:9]}  {len(entry.passed)} passing, "
                     f"{len(entry.failed)} failing  {entry.subject}")
    return lines or ["  (nothing measured yet)"]
