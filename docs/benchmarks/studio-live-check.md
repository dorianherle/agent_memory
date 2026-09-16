# Studio live handoff check

Date: 2026-09-16. Chrome, http://127.0.0.1:51799/, Grok 4.6 High Fast.
Isolated project: /private/tmp/agent-memory-studio-check-0916.

Two real successive agent turns were submitted through the Studio GUI. First
saved a checkpoint; second was asked to recover its fields without those values
in its question. Each turn consumes a separate standby agent handle.

| Check | Result |
| --- | --- |
| Connection | Connected, green success label |
| Checkpoint save | Note 77d9b5cc created |
| Successor recovery | CEDAR-7429, 37 entries, verify eviction order: all correct |
| Note details | Full checkpoint opened successfully in GUI |
| First turn | Agent wait 0 ms; send 4121.3 ms; first text 9578.5 ms; total 15.4 s |
| Successor | Agent wait 0 ms; send 1090.9 ms; first text 5610.1 ms; total 13.6 s |
| Focused source tests | 15 passed in 5.91 s |

These are single live samples, not latency percentiles or a token benchmark.
First text includes progress narration; it is not the completed answer. The
successor made an additional read despite receiving the compact checkpoint.
The running preview predates the final source-only completion-lock ordering
change; focused tests cover that change. No server restart was performed, to
preserve the session-only key. The original selected folder test_repro lacks
memory installation; the isolated folder was used to avoid changing it.

## Send readiness repair

The original test_repro folder lacked Cursor memory integration, which kept
Send disabled. Installed the integration there and verified a real Send click
returned SEND_OK in 8.1 seconds, with Send enabled again after completion.
Source now prepares missing memory integration on folder open, preserving
unrelated MCP entries; errors are returned as actionable JSON. Project changes
clear stale timing and draft UI, and polling reconciles readiness changes.
16 focused regression tests passed in 6.53 seconds; JavaScript syntax passed.
Automatic folder preparation requires the next backend restart; the active
folder repair and frontend updates are already live without losing the key.

## Response latency comparison

Same live connection and project, sequential single samples:
- Grok 4.6 High Fast greeting: first text 11,640.3 ms, finished 11.8 s.
- Composer 2.5 greeting: first text 5,064.6 ms, finished 5.3 s.
- Composer 2.5 fixed short reply: first text 5,656 ms, finished 5.9 s.
- Gemini 3.8 Flash: blocked by provider usage limit; not a timing success.
Agent wait was 0 ms throughout. Composer 2.5 is now selected in the live GUI.
This is a small sample, not a controlled model-quality or percentile benchmark.

Source now consumes SDK text-delta interaction events and suppresses the
matching prefix in completed assistant messages, with a regression test proving
text is emitted before completion without duplication. Focused suite: 17 passed
in 7.39 s. This backend improvement requires restart and has not been measured
live. Updated pending labels distinguish request sending from response wait.

## Selected-folder memory repair (2026-09-16)

The reported empty `test_repro` sidebar was a real notebook mismatch. The SDK
loaded the enclosing Git repository's MCP/hooks, while the overview read the
selected folder. Six snake-game notes existed in the parent notebook; the
selected folder had no commits.

Studio now explicitly loads selected-folder MCP servers, pins agent-memory's
root, and disables SDK settings discovery. Studio itself saves the exact user
message before sending, injects that notebook's current packet, and saves the
finished answer before completion. Failed/cancelled runs do not save errors as
answers. Project file instructions are requested explicitly before edits.

Recovered the six relevant snake-game notes to `test_repro`, preserving source
commit IDs in `recovered_from`. Original parent history and snapshots remain
untouched. Recovery creates selected-folder snapshots of the current files;
it does not pretend these are the historical parent snapshots.

Default is `grok-4.5`, with `effort=high` and `fast=true`, verified against the
live Cursor model catalog. Sidebar shows saved messages by default, counts all
notes including folded children, exposes goal/handoff details, searches pinned
goals, and reports memory-refresh errors locally. Initial memory loading no
longer waits for the model catalog. Completed assistant messages have paragraph
separation.

Validation:
- Full suite: 227 passed, 1 skipped (89.48 seconds); JavaScript syntax valid.
- Chrome: recovered goal and six saved notes visible; summary expansion, full
  note details, pinned-goal search, and Grok 4.5 Fast selection verified.
- Real Grok request saved the exact prompt in `test_repro` (now seven notes);
  parent HEAD stayed `36970be`. Provider quota error was not saved as an answer.
- Successful live model/tool response remains unverified: the restarted server
  uses the existing `.env` key, whose account reports a usage limit. The prior
  browser connection key was session-only and was not retained across restart.
  Reconnect the intended key in Settings to complete that live check.
