"""Dead-end demo: fail, branch with an autopsy, next prompt shows Avoid.

    .venv/bin/python -m agent_memory.demo_v1
"""

import tempfile
from pathlib import Path

from agent_memory import Memory


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        memory = Memory(Path(tmp))
        memory.save_user("Why are session lookups so slow?", agent="user")
        green = memory.save("Session lookup does a full table scan.")
        memory.save("Tried Redis; p95 got worse.")
        memory.branch(
            green,
            "retry",
            summary="Redis cache made p95 worse. Do not retry under this schema.",
        )
        memory.save("Added an index on sessions.user_id; p95 dropped 4x.")

        state = memory.render_state()
        assert "## Avoid / Previous failed paths" in state
        assert "Redis cache made p95 worse" in state
        assert "## Prior knowledge" not in state
        print(state)
        print("\nDead-end autopsy is in Avoid.")


if __name__ == "__main__":
    main()
