"""CLI: `agent-memory cursor install`."""

from __future__ import annotations

import argparse
import sys
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
        help="Wire MCP + hooks + rule + SDK guide + Gemini into .cursor/",
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
        help="Gemini API key for semantic memory_find (or set GEMINI_API_KEY / .env)",
    )
    install.add_argument(
        "--skip-gemini",
        action="store_true",
        help="Explicitly skip embeddings (local FTS5 memory_find only)",
    )
    install.add_argument(
        "--no-interactive",
        action="store_true",
        help="Never prompt; require --gemini-api-key / env / .env or --skip-gemini",
    )

    args = parser.parse_args(argv)

    if args.command == "cursor" and args.cursor_command == "install":
        from agent_memory.cursor.install import GeminiSetupError, install_cursor

        try:
            written = install_cursor(
                args.project,
                python=args.python,
                gemini_api_key=args.gemini_api_key,
                skip_gemini=args.skip_gemini,
                interactive=False if args.no_interactive else None,
            )
        except GeminiSetupError as error:
            print(f"error: {error}", file=sys.stderr)
            sys.exit(2)

        root = Path(args.project).resolve()
        print(f"Installed agent-memory into {root / '.cursor'}")
        for label, path in written.items():
            print(f"  {label}: {path}")
        print()
        cred = written.get("credentials")
        has_gemini = bool(
            cred
            and cred.exists()
            and "GEMINI_API_KEY=" in cred.read_text()
            and cred.read_text().split("GEMINI_API_KEY=", 1)[-1].strip()
        )
        if has_gemini:
            print("Gemini embeddings: ON (semantic memory_find)")
            print('  pip install "agent-memory[cursor]"  # includes google-genai')
        else:
            print("Gemini embeddings: OFF (local FTS5 memory_find only)")
            print("  You chose --skip-gemini (or cleared credentials).")
        print()
        from agent_memory.cursor.install import sdk_next_steps_lines

        for line in sdk_next_steps_lines():
            print(line)
        return

    parser.error("unknown command")


if __name__ == "__main__":
    main()
