"""Official ZexueHe/MemoryArena adapter for `agent_memory`.

Drop-in memory backend implementing the `MemoryArena` (`arXiv:2602.16313`)
`memory/memory_systems/` interface (`add_chunk`, `wrap_user_prompt`):
  https://github.com/ZexueHe/MemoryArena/tree/main/memory

Usage in `ZexueHe/MemoryArena/memory/server.py`:
    from memory_systems.agent_memory_system import AgentMemorySystem
    MEMORY_FACTORIES["agent_memory"] = AgentMemorySystem
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import tempfile
from typing import Optional

from agent_memory import Memory
from agent_memory.tools import call_tool


class AgentMemorySystem:
    """Consecutive-compression + single-call `full=True` FTS5 memory system for MemoryArena."""

    def __init__(
        self,
        user_id: Optional[str] = None,
        mode: str = "single_call_full",
        workspace_root: Optional[str | Path] = None,
    ):
        self.user_id = user_id or "default"
        self.mode = mode
        self._owns_tempdir = workspace_root is None
        if workspace_root is None:
            self._root = Path(tempfile.mkdtemp(prefix=f"memoryarena_{self.user_id}_"))
        else:
            self._root = Path(workspace_root)
            self._root.mkdir(parents=True, exist_ok=True)
        self.mem = Memory(self._root / ".agent_memory")
        self._step_count = 0
        self._saved_backgrounds: set[str] = set()
        self._step_summaries: list[str] = []
        self._prev_summary_id: Optional[str] = None

    def _ingest_prompt_background(self, prompt: str) -> str:
        """Extract and persist any non-empty `### BACKGROUND:` section from `wrap_user_prompt`."""
        bg_match = re.search(
            r"###\s*BACKGROUND:\s*(.*?)(?=###\s*PROBLEM:|\Z)",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
        prob_match = re.search(
            r"###\s*PROBLEM:\s*(.*)",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if bg_match:
            bg_text = bg_match.group(1).strip()
            if (
                bg_text
                and bg_text.lower() != "no information provided."
                and bg_text not in self._saved_backgrounds
            ):
                self._saved_backgrounds.add(bg_text)
                self.mem.save(
                    f"Foundational Background Setup:\n{bg_text}",
                    {"goal": True, "step": self._step_count},
                    agent="memoryarena_agent",
                )
        return prob_match.group(1).strip() if prob_match else prompt.strip()

    def add_chunk(self, chunk: str) -> str:
        """Store completed session output and update the consecutive derivation summary."""
        text = (chunk or "").strip()
        if not text or text.startswith("Initial result: Empty"):
            return "ignored_empty"

        self._step_count += 1
        note_id = self.mem.save(
            f"Subtask {self._step_count} Solved Result:\n{text}",
            {"step": self._step_count},
            agent="memoryarena_agent",
        )
        self._step_summaries.append(f"Step {self._step_count}: {text[-450:]}")

        if self._step_count >= 2 and self.mode == "single_call_full":
            if self._prev_summary_id:
                self.mem.delete(self._prev_summary_id)
            self._prev_summary_id = self.mem.save(
                f"Consecutive Formal State (Steps 1..{self._step_count}):\n"
                + "\n".join(self._step_summaries),
                summary=True,
                agent="memoryarena_agent",
            )
        return note_id

    def wrap_user_prompt(self, prompt: str) -> str:
        """Retrieve `MEMORY STATE` + single-call `memory_find(query=..., full=True)`."""
        question = self._ingest_prompt_background(prompt)
        if self._step_count == 0 and len(self._saved_backgrounds) <= 1:
            # First session already has `### BACKGROUND:` inline in `prompt`
            return f"<memory_context>\nNone\n</memory_context>\nUser: {prompt}"

        packet = self.mem.render_packet(question)
        if self.mode == "passive_state":
            mem_ctx = packet
        else:
            found = call_tool(
                self.mem,
                "memory",
                {"action": "find", "query": question, "full": True},
            )
            mem_ctx = f"{packet}\n\n## Retrieved Memory Notes (memory_find query full=True)\n{found}"

        return f"<memory_context>\n{mem_ctx}\n</memory_context>\nUser: {prompt}"

    def close(self) -> None:
        if self._owns_tempdir and self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
