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
        help="Wire MCP + hooks + rule + Gemini embeddings into .cursor/",
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
    install.add_argument(
        "--gemini-api-key",
        default=None,
        help="Gemini API key for semantic memory_find (or set GEMINI_API_KEY)",
    )
    install.add_argument(
        "--skip-gemini",
        action="store_true",
        help="Skip embeddings; memory_find(query=) falls back to substring",
    )
    install.add_argument(
        "--no-interactive",
        action="store_true",
        help="Never prompt (use flags/env only)",
    )

    args = parser.parse_args(argv)

    if args.command == "cursor" and args.cursor_command == "install":
        from agent_memory.cursor.install import install_cursor

        written = install_cursor(
            args.project,
            python=args.python,
            gemini_api_key=args.gemini_api_key,
            skip_gemini=args.skip_gemini,
            interactive=False if args.no_interactive else None,
        )
        root = Path(args.project).resolve()
        print(f"Installed agent-memory into {root / '.cursor'}")
        for label, path in written.items():
            print(f"  {label}: {path}")
        print()
        has_gemini = "credentials" in written and written["credentials"].exists()
        if has_gemini and written["credentials"].read_text().strip():
            print("Gemini embeddings: on (semantic memory_find)")
            print('  Also install: pip install "agent-memory[cursor,gemini]"')
        else:
            print("Gemini embeddings: off (substring memory_find only)")
            print("  Re-run with: agent-memory cursor install --gemini-api-key …")
        print()
        print("Next:")
        print("  Reload MCP in Cursor Settings → Tools & MCP")
        print("  Start an Agent chat — hooks auto-save; use memory_* tools")
        return

    parser.error("unknown command")


if __name__ == "__main__":
    main()
