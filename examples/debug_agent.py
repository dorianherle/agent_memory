"""A real agent debugging a real bug, with agent_memory as its only memory.

There is no chat history. Every turn the prompt is rebuilt from
memory.state(), so reading a file makes the prompt grow and folding
those files behind a summary makes it shrink again. The full text stays
recoverable with investigate.

Needs OPENROUTER_API_KEY in .env. Run it from the repository root:

    python examples/debug_agent.py
"""

import json
import os
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from agent_memory import Memory
from agent_memory.prompt import SYSTEM_PROMPT, render
from buggy_checkout import FILES

MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.6")
MAX_TURNS = 40
NUDGE_AFTER = 4

SOURCE = "\n".join(FILES.values())
BUG_LINE = 'raw = f"{order.id}:{order.total}:{time.time()}"'

TASK = f"""
## Task

You are debugging a Python service. Incident 4412: some checkout orders
charge the customer twice. Find the root cause and write a fix plan.

Read the docs first and save your hypothesis as a note. Then read the
code and test that hypothesis against it. If a file disproves something
you already concluded, revise it so the correction is on the record.

Your answer must quote the exact source line that causes the double
charge, plus the other lines you want to change, copied character for
character. They are checked against the real files, so a summary is not
good enough: if a file is folded away, investigate the summary that
covers it and copy the real text back out.

Files you can read:
{chr(10).join(FILES)}
""".strip()

TOOLS = """
## How you work

Memory is the only thing that survives a turn. Reply with exactly one
JSON action and nothing else.

{"tool": "read", "file": "checkout/api.py"}
  Puts the whole file into Recent, where you can see it.
{"tool": "summary", "text": "one sentence", "covers": ["1a2b3c4d", "5e6f7a8b"]}
  Folds those entries away so your state gets short again.
{"tool": "investigate", "id": "1a2b3c4d"}
  Shows you the exact text folded behind that summary, once.
{"tool": "note", "text": "a conclusion or a hypothesis"}
{"tool": "revise", "id": "1a2b3c4d", "reason": "why that was wrong"}
  Use this when a later file disproves something you concluded earlier.
{"tool": "answer", "text": "root cause and fix plan", "quotes": ["GATEWAY_ATTEMPTS = 2"]}
  quotes holds the line that causes the bug plus the lines you change,
  three or more, each copied exactly from a file.

Read every file, summarizing as you go so your state stays short.
An investigate result is shown once, so save what you need with note.
Investigate each summary at most once, then answer.
Never state a value you have not read.
""".strip()


def load_env():
    path = Path(__file__).parent.parent / ".env"
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


def ask_model(prompt):
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def parse_action(reply):
    reply = reply.strip()
    if reply.startswith("```"):
        reply = reply.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        return None


def main():
    load_env()
    memory = Memory(tempfile.mkdtemp())
    full_ids = {}
    observation = "You have not read anything yet."
    read_files = set()
    investigated = set()
    stored = 0
    widest = 0
    revisions = 0
    turn = 0
    shown = ""

    for turn in range(1, MAX_TURNS + 1):
        state = memory.state()
        shown = render(state, NUDGE_AFTER)
        widest = max(widest, len(shown))
        unread = [name for name in FILES if name not in read_files]
        todo = (
            "Files still unread: " + ", ".join(unread) if unread
            else "Every file is read. Answer as soon as you are sure."
        )
        prompt = (
            f"{SYSTEM_PROMPT}\n\n{TASK}\n\n{TOOLS}\n\n"
            f"## Your state\n\n{shown}\n\n"
            f"## Progress\n\n{todo}\n\n"
            f"## Last observation\n\n{observation}"
        )
        print(f"\nturn {turn}: state {len(shown)} chars in {len(state)} entries, "
              f"memory holds {stored} chars")

        action = parse_action(ask_model(prompt))
        if action is None:
            print("  unparsed reply")
            observation = "Reply with one JSON action and nothing else."
            continue

        tool = action.get("tool")
        print(f"  {tool} {json.dumps(action)[:120]}")

        if tool == "read":
            name = action["file"]
            if name in read_files:
                observation = (
                    f"You already read {name}. It is still in memory, "
                    "so investigate the summary that covers it."
                )
            else:
                content = FILES.get(name, f"There is no file named {name}.")
                read_files.add(name)
                stored += len(content)
                item_id = memory.save(
                    f"{name}\n{content}", {"file": name}, allow_empty=True
                )
                full_ids[item_id[:8]] = item_id
                observation = f"{name} is now in Recent."

        elif tool == "note":
            stored += len(action["text"])
            item_id = memory.save(action["text"], allow_empty=True)
            full_ids[item_id[:8]] = item_id
            observation = "Saved."

        elif tool == "summary":
            covered = [full_ids[short] for short in action["covers"] if short in full_ids]
            item_id = memory.save(action["text"], covers=covered)
            full_ids[item_id[:8]] = item_id
            observation = f"{len(covered)} entries now sit behind this summary."

        elif tool == "investigate":
            found = memory.find(ids=full_ids.get(action["id"], "missing"))
            if not found:
                observation = "That id is not in your state."
            elif action["id"] in investigated:
                observation = "You already saw that one. Move on."
            else:
                investigated.add(action["id"])
                steps = memory.find(ids=found[0].payload.get("covers", []), k=50)
                observation = "Shown once, so save what you need.\n\n" + "\n\n".join(
                    f"[{s.id[:8]}] {s.message}" for s in steps
                )

        elif tool == "revise":
            revisions += 1
            memory = memory.branch(full_ids[action["id"]], f"revision-{revisions}")
            item_id = memory.save(action["reason"], {"tag": "hint"}, allow_empty=True)
            full_ids[item_id[:8]] = item_id
            observation = "You are back at that point on a new branch."

        elif tool == "answer":
            if unread:
                observation = "Not yet. Still unread: " + ", ".join(unread)
                continue
            quotes = action.get("quotes", [])
            wrong = [line for line in quotes if line.strip() not in SOURCE]
            if len(quotes) < 3 or wrong:
                observation = (
                    "Quote at least three real lines. These do not appear in "
                    "any file: " + " | ".join(wrong)
                )
                continue
            if not any(BUG_LINE in line for line in quotes):
                observation = (
                    "None of those is the line that causes the double charge. "
                    "Quote it exactly."
                )
                continue
            print("\n=== answer ===")
            print(action["text"])
            break

        else:
            observation = "Unknown tool."

    print("\n=== what memory did ===")
    print(f"turns: {turn}")
    print(f"files read: {len(read_files)} of {len(FILES)}")
    print(f"text kept in memory: {stored} chars")
    print(f"widest state ever shown: {widest} chars")
    print(f"state in the last prompt: {len(shown)} chars")
    print(f"revisions: {revisions}")
    print()
    print(render(memory.state(), NUDGE_AFTER))


if __name__ == "__main__":
    main()
