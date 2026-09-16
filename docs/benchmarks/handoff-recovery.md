# Fresh-agent handoff recovery

Measured 2026-09-16 against the working tree before and after the handoff changes.
The baseline fixtures were captured before edits, including the existing local
changes. This is a controlled recovery microbenchmark, not proof of successful
hours-long autonomous work.

## Result

Two repeated trials, eight independent cases per trial, same model (`grok-4.6`),
same instructions and source histories. Each model invocation began with fresh
context. The model was provided no source repository or reference answers and was
instructed to use no tools; each case had its own isolated history. Cases were batched in one prompt; labels kept their
answers and retrieval requests separate.

| Metric, per eight-case trial | Before | After |
|---|---:|---:|
| Exact case recovery | 5/8 both trials | 8/8 both trials |
| Requested note bodies | 10 / 11 | 0 / 0 |
| Memory evidence tokens, including fetched bodies | 759 / 773 | 547 / 547 |
| Visible model input + output tokens | 2,278 / 2,302 | 1,039 / 1,039 |
| Initial memory packet tokens | 426 | 547 |

Memory evidence fell **28.6%** on average; complete visible benchmark exchanges
fell **54.6%**. The initial packets grew **28.4%**, but avoided additional reads.
The complete exchange metric includes repeated context on follow-up calls, so
batching amplifies its savings; the evidence-only metric is less sensitive to
that experimental choice. No universal token reduction is promised.

Token counts use `cl100k_base` consistently for both arms. They are not the
provider's native tokenizer or billed usage. Hidden system prompts, hidden
reasoning, provider tool schemas, caches, and server-side overhead are unavailable
through the connector and excluded. No extra LLM is used by the implementation
to assemble packets; the model calls here are evaluation only.

## Cases and protocol

Each case has 24 unrelated earlier work notes and a short interrupted task:

1. Recover multiline user constraints: region and private access.
2. Recover an exact next test command from an auto-saved answer.
3. Retain a decision and rejected approach.
4. Recheck a measured claim after its underlying configuration changed.
5. Keep an explicit checkpoint despite a later incidental edit.
6. Abstain when latency was never measured.
7. Recover a simple timeout already present in a headline.
8. Recover a rollback command beyond the old 800-character body view.

The fixtures were authored to exercise defects identified in the source review.
They are a development suite, not a held-out or representative production
sample. Exact answers are scored deterministically, with no LLM judge.

The first request presents only the generated packet and asks for answers or
visible IDs whose bodies are missing. Requested bodies are served from captured
`memory_find(ids=...)` output. One follow-up supplies those bodies and asks for
final answers; missing facts must be marked unknown. Free-form search, workspace
inspection, arbitrary tool use, and open-ended research are intentionally outside
this experiment. These restrictions apply equally to both arms, but mean the
baseline could potentially recover more through longer live interaction.

Both measured repeats used the same explicit protocol: reads are requests for
missing information, not citations. The earlier trial-1 pilot was retained for
transparency; its after-model returned correct answers but put every reference
in `reads`. It is not included in the numerical comparison. Trials 2 and 3 were
rerun in both arms with the clarified instruction. The baseline remained at 5/8;
the improved arm used no reads and achieved 8/8.

The text cases do not test actual code execution, end-to-end task completion, a chain
of agent-written summaries, independent goal isolation, or concurrent edits.
They establish an improvement in one important part of long-task execution:
recovering the right next action and constraints after context loss.

## Executable successor-agent check

A separate coding task tested actual code changes. Both fresh agents received the
same incomplete `Deduplicator` implementation, the corresponding old/new memory
packet, and a file containing that arm's available note bodies. Agents could read,
edit and run their own checks inside separate temporary workspaces. Hidden tests
were outside those workspaces and were executed only after both agents finished.

The source user request specified tenant isolation, non-refreshing TTL, exact
expiry, empty-ID bypass, a global monotonic clock watermark, and cleanup on every
call. The baseline stored only the first line of that request; the improved store
preserved its full body. The improved agent could retrieve requirements that did
not fit in the bounded packet.

| Hidden executable checks | Old handoff | Improved handoff |
|---|---:|---:|
| Passed | 4/8 | 8/8 |

The old agent got ordinary duplicate detection, tenant isolation, TTL expiry and
non-refreshing duplicates right, but missed empty-ID bypass, global clock rollback,
and cleanup requirements. It explicitly deferred cleanup in its final response.
The improved agent implemented all eight checked behaviors.

This is **one synthetic coding task, one run per arm**, not a statistical claim
about general coding success. The initial partial implementation and checkpoint
were fixture-authored; this was not a chain of independently generated sender
summaries. The baseline code was loaded from the recorded Git revision using the
same fixture. Other pre-existing local edits were unrelated to this fixture.

The connector does not expose tool transcripts or native token usage for the
coding runs, so no coding-task token-savings claim is made. The token table above
applies only to the explicit text recovery experiment.

Artifacts: [baseline response](../../benchmarks/handoff/coding-before.json),
[improved response](../../benchmarks/handoff/coding-after.json),
[baseline implementation](../../benchmarks/handoff/coding-before-candidate.py),
[improved implementation](../../benchmarks/handoff/coding-after-candidate.py),
[baseline score](../../benchmarks/handoff/coding-before-score.json),
[improved score](../../benchmarks/handoff/coding-after-score.json).

Reproduce fixture preparation with `scripts/benchmark_coding_handoff.py`. Run a
fresh successor in each printed workspace using the matching recorded prompt.
Then grade with `scripts/grade_coding_handoff.py <workspace>/dedup.py`. Keep the
grader outside the successor's workspace.

## Implementation

- Full user prompts and completed answers are retained on disk; display limits
  no longer destroy evidence during saving.
- Goal, latest steering, transfer checkpoint and relevant body excerpts share a
  deterministic 1,200-character packet budget. Skipped entries remain searchable.
- `payload={"handoff": true}` pins a transfer checkpoint above incidental notes.
  `Next:`, rejected approaches, decisions, constraints and evidence are preferred
  when extracting complete lines. Excerpts never invoke an LLM.
- `payload.files` declares evidence dependencies. Packet stale warnings include
  unsaved tracked changes and note-only checkpoints with declared dependencies.
- `memory_find(ids=[...], full=True)` can retrieve evidence beyond the default
  800-character body view. Routine retrieval remains compact.
- Completed non-streaming and streaming OpenAI-compatible SDK answers are saved;
  an interrupted stream is not recorded as a completed answer.
- Retrieval expands its local candidate pool when transcript hits crowd out work
  notes. It does not expand the injected packet.
- Opening a memory client no longer restores another agent's active inspection.
  Recovery requires explicit `inspect_done`. Restores still change shared files;
  concurrent code writers need coordination or separate workspaces.

Initial one-shot packet construction medians were 126.7 ms before and 157.3 ms
after in these small temporary projects. This is a token-efficiency improvement,
not a demonstrated local CPU speedup. Stale checks add Git work. These timings
are observational, not a statistically controlled latency benchmark.

## Reproduce and inspect

Install the optional benchmark tokenizer with `pip install -e '.[benchmark]'`.
Its public vocabulary downloads once; later runs can be offline.

```sh
.venv/bin/python scripts/benchmark_handoff.py --output /tmp/handoff-current
.venv/bin/python scripts/benchmark_handoff.py --report benchmarks/handoff
.venv/bin/python -m pytest agent_memory/tests -q
```

- [Fixture generator and deterministic scorer](../../scripts/benchmark_handoff.py)
- [Machine-readable token and accuracy report](../../benchmarks/handoff/report.json)
- [Captured baseline cases and tool responses](../../benchmarks/handoff/before/cases.json)
- [Captured improved cases and tool responses](../../benchmarks/handoff/after/cases.json)
- [Baseline trial 2, complete prompts and response](../../benchmarks/handoff/before/trial-2.json)
- [Improved trial 2, complete prompt and response](../../benchmarks/handoff/after/trial-2-initial.json)
- [Baseline trial 3](../../benchmarks/handoff/before/trial-3.json)
- [Improved trial 3](../../benchmarks/handoff/after/trial-3.json)

To repeat live evaluation, submit the initial prompt saved in each trial file to
a fresh read-only model invocation. Supply only explicitly requested IDs from
that arm's `cases.json` in a second invocation with the initial transcript.
Never expose `gold.json` or the other arm. Save the raw responses before scoring.
The report command recomputes measurements from archived responses; it does not
make new model calls. Git IDs can vary between regenerated fixtures, so exact
packet token counts may differ by a few tokens without a content change.

The next stronger evaluation should run several actual coding tasks through
multiple independent agent handoffs, score executable outcomes, and use native
provider usage accounting. It should include a full-transcript baseline,
equal-token-budget ablations, and held-out tasks. That is still unproven here.

Full transcript storage does not increase optional embedding input: user prompts
and auto-saved answers embed only their former 100-character headline. Full
bodies remain available through local full-text search and explicit ID reads.
Work notes retain their normal semantic indexing.

## Regression validation

The full suite passed 218 tests with one live-provider test skipped. After the
final transcript-embedding guard, the affected handoff and semantic suites passed
26 tests with the same live-provider skip. `git diff --check` and compilation
checks passed. Regenerated final packets matched the evaluated packet content
after normalizing Git note IDs. Live Gemini quality was not evaluated.
