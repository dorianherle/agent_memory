"""CLI: `agent-memory cursor install`."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent-memory",
        description="Git-backed memory for long-running agents",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cursor = sub.add_parser(
        "cursor",
        help="Cursor IDE integration (MCP tools + durability hooks)",
    )
    cursor_sub = cursor.add_subparsers(dest="cursor_command", required=True)

    install = cursor_sub.add_parser(
        "install",
        help="Wire MCP + hooks + rule into a project's .cursor/",
    )
    install.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project root (default: current directory)",
    )
    install.add_argument(
        "--python",
        default=None,
        help="Python executable for hooks/MCP (default: this interpreter)",
    )

    args = parser.parse_args(argv)

    if args.command == "cursor" and args.cursor_command == "install":
        from agent_memory.cursor.install import install_cursor

        written = install_cursor(args.project, python=args.python)
        root = Path(args.project).resolve()
        print(f"Installed agent-memory into {root / '.cursor'}")
        for label, path in written.items():
            print(f"  {label}: {path}")
        print()
        print("Next:")
        print('  pip install "agent-memory[cursor]"   # if you have not')
        print("  Reload MCP in Cursor Settings → Tools & MCP")
        print("  Start an Agent chat — hooks auto-save; use memory_* tools")
        return

    parser.error("unknown command")


if __name__ == "__main__":
    main()
