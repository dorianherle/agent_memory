"""The tools the two agents are given.

The Agents SDK runs the tool loop; these are plain Python functions handed to
it. They are built by factories rather than declared at module level because
each one has to close over the repo it is allowed to touch.

Repo understanding goes through the codegraph CLI. It answers "who calls this"
and "what breaks if I change it" from a real index, which is the question that
costs the most to answer by reading files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents import function_tool

from controller.tests import run_pytest
from memory import Memory

CODEGRAPH = "codegraph"


def _cg(repo_path: Path, *args: str) -> str:
    """One codegraph subcommand. Its output is already written for a model."""
    result = subprocess.run([CODEGRAPH, *args, "-p", str(repo_path)],
                            capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or "(nothing found)"


def index(repo_path: str | Path) -> None:
    """Build the graph, or refresh it after the Worker edited files.

    Called by the controller between rounds. `sync` only walks what changed, so
    it stays cheap however long the run goes on.
    """
    repo_path = Path(repo_path)
    command = "sync" if (repo_path / ".codegraph").exists() else "init"
    subprocess.run([CODEGRAPH, command, str(repo_path)], capture_output=True, text=True)


def _reading(repo_path: Path) -> list:
    """Tools both agents get: read the code, and ask the graph about it."""

    @function_tool
    def read_file(path: str) -> str:
        """Read one file, relative to the project root.

        Args:
            path: Path inside the project, such as solver.py.
        """
        return (repo_path / path).read_text()

    @function_tool
    def list_files() -> str:
        """Every file in the project, one relative path per line."""
        paths = []
        for found in repo_path.rglob("*"):
            relative = found.relative_to(repo_path)
            hidden = any(part.startswith(".") or part == "__pycache__"
                         for part in relative.parts)
            if found.is_file() and not hidden:
                paths.append(str(relative))
        return "\n".join(sorted(paths))

    @function_tool
    def explore(query: str) -> str:
        """Start here instead of reading files blind.

        Returns the source of the symbols relevant to a description, plus the
        blast radius: what already depends on them, and whether tests cover it.

        Args:
            query: What you are looking for, in words. "the time stepping loop".
        """
        return _cg(repo_path, "explore", query)

    @function_tool
    def symbol(name: str) -> str:
        """One symbol's source plus its callers and callees.

        Follow the trail it prints by calling this again on a name it lists;
        that is cheaper and more complete than reading the files yourself.

        Args:
            name: A function or class name.
        """
        return _cg(repo_path, "node", name)

    @function_tool
    def find(search: str) -> str:
        """Find symbols by name when you are not sure what a thing is called.

        Args:
            search: Part of a function or class name.
        """
        return _cg(repo_path, "query", search)

    return [read_file, list_files, explore, symbol, find]


def worker_tools(repo_path: str | Path) -> list:
    """Read the code, change it, and run the suite."""
    repo_path = Path(repo_path)

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Write a file, replacing it whole. Creates it if it does not exist.

        Args:
            path: Path inside the project.
            content: The complete new contents of the file.
        """
        full = repo_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return f"wrote {path}"

    @function_tool
    def run_tests() -> str:
        """Run the whole test suite now and read what it says.

        Do this before you report, so your diagnosis describes the real failure
        rather than the one you expect.
        """
        return run_pytest(repo_path)[2]

    return _reading(repo_path) + [write_file, run_tests]


def thinker_tools(repo_path: str | Path, memory: Memory) -> list:
    """Read the code, and read the history that produced it."""
    repo_path = Path(repo_path)

    @function_tool
    def read_commit(commit: str) -> str:
        """A commit's message and the exact diff it introduced.

        The message is somebody's summary; the diff is what actually happened.
        Read the diff before you trust the summary.

        Args:
            commit: A commit SHA from the history you were shown.
        """
        return f"{memory.entry(commit)}\n\n{memory.diff(commit)}"

    @function_tool
    def checkout(commit: str) -> str:
        """Put the whole project back to how it looked at a commit.

        The graph is reindexed, so explore and symbol then describe that old
        state. Call checkout_exit when you are done, before you answer.

        Args:
            commit: A commit SHA from the history you were shown.
        """
        memory.inspect(commit)
        index(repo_path)
        return f"the project is now at {commit}. Call checkout_exit when done."

    @function_tool
    def checkout_exit() -> str:
        """Leave the old commit and return to the branch you started on."""
        memory.inspect_exit()
        index(repo_path)
        return f"back on {memory.branch_name()}"

    @function_tool
    def search_memory(query: str) -> str:
        """Ask the whole history a question in words, across every branch.

        Use it for "has anything like this been tried". For an exact answer
        about one test, the history you were shown already has it.

        Args:
            query: A question, such as "did we try an explicit stepper".
        """
        from memory.semantic import Search

        hits = Search(memory).find(query, k=5)
        return "\n".join(f"{score:.2f}  {entry}" for entry, score in hits) or "(nothing)"

    return _reading(repo_path) + [read_commit, checkout, checkout_exit, search_memory]
