"""Local Git as the experiment memory.

No LLMs. Caller supplies commit text / tags / pivot targets;
this module only reads and writes Git state on disk.
"""

from __future__ import annotations

import fcntl
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator, Sequence

HIDDEN_DIR = ".agent_memory"
ARCHIVE_PREFIX = ".agent_memory_archive_"

# Written inside our git dir so we never operate on a repo we did not create.
MARKER = "AGENT_MEMORY"

# Where inspect() left the branch. Kept in the git dir rather than on self so
# any Memory object, or a later run after a crash, can undo the time travel.
RETURN_FILE = "AGENT_RETURN"

# One work tree, one HEAD: parallel agents must take turns to move it.
LOCK_FILE = "AGENT_LOCK"

# What _park_dirty() commits. A constant so the search index can skip them.
PARK_MESSAGE = "work in progress"

# One source of truth for what must stay out of both repos, ours and the user's.
# record() runs `add -A`, so anything missing here is one commit away from being
# permanent history, and one sync() away from GitHub.
_IGNORED = (f"{HIDDEN_DIR}/", f"{ARCHIVE_PREFIX}*/", ".env", ".codegraph/", ".pytest_cache/")

# %B is multi-line, so commits need an explicit separator. \x02 is not
# whitespace, so it survives the .strip() in _run().
_FIELD_SEP = "\x00"
_RECORD_SEP = "\x02"
_FORMAT = "--format=%H%x00%cI%x00%B%x02"


class Tag(Enum):
    CODE = "CODE"
    MILESTONE = "MILESTONE"
    FAIL = "FAIL"
    PLAN = "PLAN"        # the Thinker's directive for the next Worker
    LESSON = "LESSON"    # a belief distilled from several commits


# Built from Tag, so adding a tag above is the only change needed.
_TAG_PREFIX = re.compile(rf"^\[({'|'.join(t.value for t in Tag)})\]\s*(.*)$", re.DOTALL)


def _parse_message(raw: str) -> tuple[Tag | None, str]:
    match = _TAG_PREFIX.match(raw.strip())
    return (Tag(match.group(1)), match.group(2).strip()) if match else (None, raw.strip())


def _trailer(message: str, key: str) -> list[str]:
    """Names listed on a `Key: a b c` line of a commit body."""
    match = re.search(rf"^{key}:(.*)$", message, re.MULTILINE)
    return match.group(1).split() if match else []


def _numbers(message: str) -> dict[str, float]:
    """Values on a `Metrics: residual=1e-6 order=1.97` line.

    Pass/fail is a step function that cannot tell "nearly there" from "diverged".
    These are what let the Thinker see which direction is downhill.
    """
    pairs = (item.partition("=") for item in _trailer(message, "Metrics"))
    return {key: float(value) for key, _, value in pairs if value}


@dataclass
class CommitEntry:
    """One row in the experiment history."""
    id: str
    message: str
    tag: Tag | None
    datetime: datetime

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    # The test result is the only objective thing in the log. Everything the
    # LLM writes is opinion; these are measurements, so they can be counted,
    # compared and used to disprove a claim made earlier.
    @property
    def passed(self) -> list[str]:
        return _trailer(self.message, "Passed")

    @property
    def failed(self) -> list[str]:
        return _trailer(self.message, "Failed")

    @property
    def tests(self) -> list[str]:
        """Tests a [LESSON] claims cannot be made to pass this way."""
        return _trailer(self.message, "Test")

    @property
    def metrics(self) -> dict[str, float]:
        return _numbers(self.message)

    @property
    def signature(self) -> tuple[str, ...]:
        """The failing set: an exact fingerprint of the predicament.

        Two moments in the research are comparable when this matches, which is
        a far sharper retrieval key than how similar the prose reads.
        """
        return tuple(sorted(self.failed))

    @property
    def measured(self) -> bool:
        return bool(self.passed or self.failed)

    def __str__(self) -> str:
        tag_str = (self.tag.value if self.tag else "").ljust(9)
        return f"{tag_str}  {self.datetime:%Y-%m-%d %H:%M:%S}  {self.id[:7]}  {self.subject}"


def _parse_entry(chunk: str) -> CommitEntry:
    commit_id, iso, msg = chunk.split(_FIELD_SEP, 2)
    tag, message = _parse_message(msg)
    return CommitEntry(commit_id.strip(), message, tag, datetime.fromisoformat(iso))


@dataclass
class Episode:
    """One directive and what the tests said after the Worker followed it.

    The controller's real output is the prompt it writes for the next coder.
    Because a test run brackets every directive, each one is a labelled example
    of its own prompting, which is the only way it can get better at choosing.
    """
    plan: CommitEntry
    commits: list[CommitEntry]
    before: tuple[str, ...]  # what was failing when the directive was given
    gained: list[str]
    lost: list[str]

    @property
    def value(self) -> int:
        return len(self.gained) - len(self.lost)

    def __str__(self) -> str:
        score = f"+{len(self.gained)}/-{len(self.lost)}"
        return f"{score:7s} in {len(self.commits):2d} commits  {self.plan.subject}"


class Memory:
    """Git-backed experiment history.

    Iterating a Memory gives the newest `limit` commits on the current branch.
    history() takes that window explicitly, plus a tag filter and
    all_branches=True, which is how the Thinker sees dead ends it did not walk.
    """

    def __init__(self, repo_path: str | Path, limit: int = 20, auto_sync: bool = False) -> None:
        # Resolved: a relative --git-dir breaks under nested git calls.
        self.repo_path = Path(repo_path).resolve()
        self.limit = limit          # 0 means no cap
        self.auto_sync = auto_sync  # push on milestones and new branches
        self.git_dir = Path()       # real value set by _init()
        self._locked = False
        self._init()

    def __repr__(self) -> str:
        return "\n".join(str(entry) for entry in self) or "(empty memory)"

    # --- Core Git Execution (Secluded by default) ---

    def _find_git_dir(self) -> Path | None:
        """Our memory dir, or None if it does not exist yet.

        Only a dir carrying MARKER counts, so a plain .git belonging to the user's
        own project is never adopted, committed into, or wiped.
        """
        candidate = self.repo_path / HIDDEN_DIR
        return candidate.resolve() if (candidate / MARKER).exists() else None

    def _run(self, *args: str, check: bool = True) -> str:
        cmd = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.repo_path}", *args]
        res = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True)
        if check and res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}")
        return res.stdout.strip()

    def _init(self) -> None:
        """Find the memory, or create it on first use, and cache where it lives.

        The ignore rules are rewritten on every construction, not just at
        creation, so a memory made before a rule existed still picks it up.
        """
        found = self._find_git_dir()
        if found is not None:
            self.git_dir = found
        else:
            self.repo_path.mkdir(parents=True, exist_ok=True)
            self.git_dir = (self.repo_path / HIDDEN_DIR).resolve()
            # -b main: visualize() needs a predictable mainline whatever init.defaultBranch is.
            self._run("init", "-b", "main")
            (self.git_dir / MARKER).write_text("")
            # Without an identity git refuses to commit at all (bare container, CI).
            if not self._run("config", "user.email", check=False):
                self._run("config", "user.email", "agent@localhost")
                self._run("config", "user.name", "Agent Memory")

        # A custom --git-dir inside the work tree is not auto-ignored the way .git
        # is. Without the exclude, `add -A` would commit the memory internals (and
        # any archive left by clear()) as ordinary files.
        exclude = self.git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join((*_IGNORED, "__pycache__/")) + "\n")
        self._ignore_in_project_repo()

    def _ignore_in_project_repo(self) -> None:
        # info/exclude only tells our git to skip our dir. The project's own git
        # would still list .agent_memory as untracked, ready to push to GitHub.
        gitignore = self.repo_path / ".gitignore"
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        missing = [rule for rule in _IGNORED if rule not in existing.splitlines()]
        if missing:
            lead = "" if not existing or existing.endswith("\n") else "\n"
            gitignore.write_text(existing + lead + "\n".join(missing) + "\n", encoding="utf-8")

    # --- Reads ---

    def has_commits(self) -> bool:
        return bool(self._run("rev-parse", "--verify", "HEAD", check=False))

    def branch_name(self) -> str:
        return self._run("branch", "--show-current", check=False)

    def branches(self) -> list[str]:
        args = ("for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/")
        return self._run(*args, check=False).splitlines()

    def resolve(self, commit: int | str) -> str:
        if isinstance(commit, int):
            return self[commit].id
        # ^{commit} makes --verify fail loudly instead of echoing back a bad ref.
        full = self._run("rev-parse", "--verify", f"{commit}^{{commit}}", check=False)
        if not full:
            raise ValueError(f"unknown commit {commit!r}")
        return full

    def _log(self, *args: str) -> list[CommitEntry]:
        """The one place commits are read and parsed. Empty on an unborn HEAD."""
        raw = self._run("log", _FORMAT, *args, check=False)
        return [_parse_entry(chunk) for chunk in raw.split(_RECORD_SEP) if chunk.strip()]

    def _limit_args(self, limit: int | None) -> list[str]:
        limit = self.limit if limit is None else limit
        return [f"-{limit}"] if limit else []

    def history(self, tag: Tag | str | None = None, all_branches: bool = False,
                limit: int | None = None) -> list[CommitEntry]:
        """A window onto the log, newest first. limit=0 for the whole thing."""
        args = self._limit_args(limit)
        if all_branches:
            # --branches, not --all: --all also walks remote-tracking refs, so
            # every synced branch would come back twice.
            args.append("--branches")
        if tag:
            # Anchored, so a tag merely mentioned inside a message is not a match.
            args.append(rf"--grep=^\[{Tag(tag).value}\]")
        return self._log(*args)

    def __iter__(self) -> Iterator[CommitEntry]:
        return iter(self.history())

    def __len__(self) -> int:
        return len(self.history())

    def __getitem__(self, idx: int) -> CommitEntry:
        return self.history()[idx]

    def entry(self, commit: int | str) -> CommitEntry:
        """One commit, whatever branch it sits on."""
        return self._log("-1", self.resolve(commit))[0]

    def plan(self) -> CommitEntry | None:
        """The directive the Worker should be following, or None."""
        found = self.history(tag=Tag.PLAN, limit=1)
        return found[0] if found else None

    def lessons(self) -> list[CommitEntry]:
        """Live lessons, from every branch, never windowed out.

        A lesson carrying `Test: name` is the claim that the test cannot be made
        to pass that way. The day it passes anywhere the claim is disproven, so
        the lesson retires itself instead of misleading the Thinker forever.
        """
        entries = self.history(all_branches=True, limit=0)
        solved = {name for e in entries for name in e.passed}
        return [e for e in entries if e.tag == Tag.LESSON and not set(e.tests) & solved]

    # --- Test signal ---
    #
    # Everything below is derived from the Passed/Failed trailers. It is the
    # only part of the memory that does not depend on an LLM having written a
    # good sentence, which is why the Thinker should trust it first.

    def last_passing(self, *tests: str) -> CommitEntry | None:
        """The newest commit where these tests passed. Where to branch back to.

        With no arguments, the newest commit where nothing failed. This is the
        "return to the last good state" pivot, computed rather than remembered.
        """
        for entry in self.history(limit=0):
            if not entry.passed:
                continue  # no test data on this commit, so it proves nothing
            broken = set(entry.failed) & set(tests) if tests else entry.failed
            if not broken:
                return entry
        return None

    def frontier(self) -> CommitEntry | None:
        """The best state ever reached on any branch: most tests passing."""
        scored = [e for e in self.history(all_branches=True, limit=0) if e.passed]
        return max(scored, key=lambda e: len(e.passed), default=None)

    def attempts(self, test: str) -> list[CommitEntry]:
        """Every commit that failed one test, across all branches.

        The exact answer to "have we tried this before", for the cases where an
        exact answer exists. No embedding, no threshold, no guessing.
        """
        return [e for e in self.history(all_branches=True, limit=0) if test in e.failed]

    def regressions(self) -> list[str]:
        """Tests that passed earlier on this branch and fail now.

        The Goodhart alarm: an agent scored on tests will trade one for another,
        and every individual commit still looks like progress while it happens.
        """
        entries = [e for e in self.history(limit=0) if e.measured]
        if not entries:
            return []
        ever_passed = {name for e in entries for name in e.passed}
        return sorted(ever_passed & set(entries[0].failed))

    def stuck(self, n: int = 10) -> bool:
        """True when the last n test runs failed in exactly the same way.

        Silent looping is the way long autonomous runs actually die, and it is
        invisible commit by commit.
        """
        runs = [e for e in self.history(limit=0) if e.measured][:n]
        return len(runs) == n and len({e.signature for e in runs}) == 1

    def signature(self) -> tuple[str, ...]:
        """What is failing right now. The key everything below retrieves on."""
        return next((e.signature for e in self.history(limit=0) if e.measured), ())

    def elites(self) -> dict[tuple[str, ...], CommitEntry]:
        """The best commit per distinct failure signature, not one global best.

        A greedy frontier funnels the whole search into a single basin. Keeping
        an elite per signature preserves the odd states that pay off later, and
        the signature is a behaviour descriptor that costs nothing to compute.
        """
        best: dict[tuple[str, ...], CommitEntry] = {}
        for entry in self.history(all_branches=True, limit=0):
            if not entry.measured:
                continue
            held = best.get(entry.signature)
            if held is None or len(entry.passed) > len(held.passed):
                best[entry.signature] = entry
        return best

    # --- Learning from its own choices ---

    def episodes(self, all_branches: bool = True) -> list[Episode]:
        """Every directive paired with the test movement that followed it."""
        entries = self.history(all_branches=all_branches, limit=0)[::-1]  # oldest first
        out = []

        for i, entry in enumerate(entries):
            if entry.tag != Tag.PLAN:
                continue
            rest = entries[i + 1:]
            stop = next((j for j, e in enumerate(rest) if e.tag == Tag.PLAN), len(rest))
            run = rest[:stop]

            before = next((e for e in reversed(entries[:i]) if e.measured), None)
            after = next((e for e in reversed(run) if e.measured), None)
            if before is None or after is None:
                continue

            out.append(Episode(
                entry, run, before.signature,
                sorted(set(before.failed) - set(after.failed)),
                sorted(set(after.failed) - set(before.failed)),
            ))
        return out

    def advice(self, limit: int = 3) -> list[Episode]:
        """Directives that paid off last time the same tests were failing.

        Retrieval keyed on the failure signature rather than on prose, so this
        answers "what worked when we were stuck exactly here" with evidence.
        """
        here = set(self.signature())
        scored = [(len(here & set(ep.before)), ep) for ep in self.episodes()]
        useful = [(overlap, ep) for overlap, ep in scored if overlap and ep.value > 0]
        useful.sort(key=lambda pair: (-pair[0], -pair[1].value))
        return [ep for _, ep in useful[:limit]]

    def ruled_out(self, limit: int = 5) -> list[CommitEntry]:
        """Attempts that already failed the tests failing right now, any branch.

        The most valuable thing a prompt can carry and the cheapest to be sure
        of: no model has to agree with it for it to be true.
        """
        here = set(self.signature())
        seen: set[str] = set()
        out = []
        for entry in self.history(tag=Tag.FAIL, all_branches=True, limit=0):
            if here & set(entry.failed) and entry.subject not in seen:
                seen.add(entry.subject)
                out.append(entry)
        return out[:limit]

    def prompt_for(self, role: str = "worker") -> str:
        """The context block for the next agent, led by what is already ruled out.

        Selected by the current failure signature rather than by recency, so it
        stays the same size whether there are fifty commits underneath or fifty
        thousand. This is the memory's actual product: everything else exists
        to make this paragraph better than the last one.
        """
        plan, best = self.plan(), self.frontier()
        lines = [
            f"branch:      {self.branch_name()}",
            f"failing now: {', '.join(self.signature()) or 'nothing'}",
            f"best state:  {f'{len(best.passed)} passing at {best.id[:7]}' if best else '(no test data)'}",
            f"plan:        {plan.subject if plan else '(none)'}",
            "",
            "already ruled out here:",
            *([f"  {e.subject}" for e in self.ruled_out()] or ["  (nothing yet)"]),
            "",
            "standing lessons:",
            *([f"  {e.subject}" for e in self.lessons()] or ["  (none)"]),
        ]

        if role == "thinker":
            warnings = self.regressions()
            lines += [
                "",
                "what worked when stuck like this:",
                *([f"  {ep}" for ep in self.advice()] or ["  (no precedent)"]),
                "",
                f"regressions: {', '.join(warnings) or 'none'}",
                f"looping:     {self.stuck()}",
                f"open fronts: {len(self.elites())} distinct failure signatures explored",
            ]
        return "\n".join(lines)

    def diff(self, commit: int | str = 0) -> str:
        """The patch a commit introduced. Defaults to the newest one."""
        return self._run("show", self.resolve(commit), "--format=", check=False)

    def working_diff(self) -> str:
        """Uncommitted changes, for diagnosing a failure before it is recorded."""
        return self._run("diff", "HEAD", check=False)

    def visualize(self) -> str:
        """Clean per-branch history for humans (newest on top)."""
        if not self.has_commits():
            return "(empty memory)"

        head_branch = self.branch_name()
        branches = self.branches()
        if head_branch in branches:
            branches.insert(0, branches.pop(branches.index(head_branch)))

        mainline = next((b for b in ("main", "master") if b in branches), None)
        lines = [f"HEAD -> {head_branch or 'detached'}", ""]

        for branch in branches:
            lines.append(branch + ("  <- you are here" if branch == head_branch else ""))
            log_args = [branch]

            if mainline and branch != mainline:
                base = self._run("merge-base", branch, mainline, check=False)
                if base and base != self._run("rev-parse", branch, check=False):
                    lines.append(f"  branched from  {self._log('-1', base)[0]}")
                # Shared history already shows up under the mainline branch.
                log_args = [branch, "--not", mainline]

            entries = self._log(*log_args, *self._limit_args(None))
            lines += [f"  [{i}] {e}" for i, e in enumerate(entries)] or ["  (no unique commits)"]
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    # --- Sync ---

    def set_remote(self, url: str, name: str = "origin") -> None:
        """Point the agent repo at its own backup repo on GitHub.

        A different repo from the project's own remote: dead ends and
        micro-commits go here, clean code goes to the project's repo.
        """
        action = "set-url" if name in self._run("remote", check=False).splitlines() else "add"
        self._run("remote", action, name, url)

    def sync(self) -> None:
        """Push every experiment branch to the remote. Raises if the push fails."""
        if self._run("remote", check=False):
            self._run("push", "--all")

    # --- Writes / Navigation ---

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """One writer at a time: parallel agents share one work tree and one HEAD.

        Reentrant, because the navigation calls park dirty work through record().
        """
        if self._locked:
            yield
            return

        with open(self.git_dir / LOCK_FILE, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self._locked = True
            try:
                yield
            finally:
                self._locked = False

    def _require_attached(self) -> None:
        """Read straight from git, so a detached HEAD is caught whichever object made it."""
        if self.has_commits() and not self.branch_name():
            raise RuntimeError("HEAD is detached. Call inspect_exit() first.")

    def _park_dirty(self) -> None:
        """Never leave a branch holding uncommitted work: commit it where it is.

        A stash would be invisible in the log and easy to lose, and the point of
        the memory is that nothing the agent produced ever disappears.
        """
        if self._run("status", "--porcelain", check=False):
            self.record(PARK_MESSAGE)

    def record(self, message: str, tag: Tag | str = Tag.CODE,
               passed: Sequence[str] = (), failed: Sequence[str] = (),
               tests: Sequence[str] = (), metrics: dict[str, float] | None = None) -> CommitEntry:
        """Commit the work tree, and with it whatever the test suite just said.

        passed/failed are what the suite reported and metrics are its numbers.
        tests is for a [LESSON]: the tests it claims are unreachable, which is
        what later lets it be disproven.

        These go in trailers rather than a results file because %B already
        returns the whole body, so reading every result in history stays one call.
        """
        with self._lock():
            self._require_attached()
            tag = Tag(tag)
            body = f"[{tag.value}] {message}"
            if passed or failed:
                body += f"\n\nPassed: {' '.join(passed)}\nFailed: {' '.join(failed)}"
            if tests:
                body += f"\n\nTest: {' '.join(tests)}"
            if metrics:
                body += "\n\nMetrics: " + " ".join(f"{k}={v}" for k, v in metrics.items())

            self._run("add", "-A")
            # --allow-empty: a [FAIL] diagnosis is often message-only, with no file change.
            self._run("commit", "--allow-empty", "-m", body)

            if self.auto_sync and tag == Tag.MILESTONE:
                self.sync()
            return self.entry("HEAD")

    def inspect(self, commit: int | str) -> str:
        """Time travel: check the work tree out at an old commit.

        In-flight work is committed to the current branch first, so what you
        inspect is the historical state and nothing else.
        """
        with self._lock():
            self._require_attached()
            # Resolved before parking, or an int index would shift by the new commit.
            commit_id = self.resolve(commit)

            self._park_dirty()
            (self.git_dir / RETURN_FILE).write_text(self.branch_name())
            self._run("checkout", "--detach", commit_id)
            return self._run("rev-parse", "HEAD")

    def inspect_exit(self) -> str:
        with self._lock():
            return_file = self.git_dir / RETURN_FILE
            if not return_file.exists():
                raise RuntimeError("not currently inspecting a commit")

            target = return_file.read_text().strip()
            return_file.unlink()
            self._run("checkout", target)
            return self._run("rev-parse", "HEAD")

    def branch_from(self, commit: int | str, new_branch_name: str) -> str:
        """Pivot: start a sibling experiment from an earlier commit."""
        with self._lock():
            self._require_attached()
            commit_id = self.resolve(commit)

            self._park_dirty()
            self._run("checkout", "-b", new_branch_name, commit_id)

            # A new trajectory is worth backing up even though it is not a milestone.
            if self.auto_sync:
                self.sync()
            return self._run("rev-parse", "HEAD")

    def switch_to(self, branch: str) -> str:
        """Resume an existing experiment branch."""
        with self._lock():
            self._require_attached()
            self._park_dirty()
            self._run("checkout", branch)
            return self._run("rev-parse", "HEAD")

    def worktree(self, branch: str) -> Path:
        """A second checkout of one branch, so workers can explore in parallel.

        The single work tree is what forces hypotheses to be tried one at a
        time; this is the way around it. Same history, same locks, own files.
        """
        path = self.repo_path.parent / f"{self.repo_path.name}__{branch}"
        if not path.exists():
            self._run("worktree", "add", str(path), branch)
        return path

    def clear(self, archive: bool = True) -> None:
        """Wipe the experiment history. archive=True keeps the old memory as a backup."""
        if archive:
            self.git_dir.rename(self.repo_path / f"{ARCHIVE_PREFIX}{datetime.now():%Y%m%d_%H%M%S}")
        else:
            shutil.rmtree(self.git_dir)

        self._init()
