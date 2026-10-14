# Private source review, 13 October 2026

Seven defects were reproduced and corrected. Fifteen regression test methods now pass. The full candidate suite passes: 274 passed, one skipped, one warning, and three subtests passed.

| Defect | Effect | Change |
| --- | --- | --- |
| Search limits global hits before it applies filters | A valid note can disappear behind hits from another branch or another ID | Apply branch, ID, text, payload, and date filters in the SQL search before LIMIT |
| Embedding storage accepts invalid shape or nonfinite values | Invalid values can enter the cache; a large valid vector can become a zero vector | Validate shape and finite values; scale before normalization; store normalized float32 values |
| Git diff omits new untracked files | A claim can appear current after its previously missing dependency is created | Check untracked paths for the explicit files named by candidate notes |
| Git cleans note whitespace | Saved code or literal text can change | Use verbatim Git commit cleanup and a versioned text format |
| A note contains a record separator or a null character | Record parsing or the commit command can fail | Store note text as ASCII JSON and decode it on read; read older affected commits individually |
| Text-mode subprocess capture changes CR and CRLF | Windows line endings change | Decode UTF-8 bytes without newline conversion; rebuild the old Git-derived cache |
| Git changes Unicode noncharacters or normalizes combining characters | The returned string can differ from the input | Encode all new note text as ASCII JSON before the Git commit |

The full LongMemEval retrieval attempt found the record-separator defect in real data. It stopped after 69 completed cases. The incomplete questions include `f9e8c073` and `c7dc5443`. Keep this failed run as evidence. Later runs use the corrected sources. These are separate runs. Full integrity checks then found line-ending conversion in five questions and Unicode changes, including noncharacters in two questions and normalization of combining characters in older notes. The final replay restores the source text and retains the earlier runs.

The old code produced eight failure entries in six regression methods. Three failure entries came from subtests in one method. Additional tests reproduced changed whitespace, a null-byte error, and a record-separator error. The evidence records include source hashes and loaded source paths. Do not combine their failure counts as independent tasks.

A deterministic 100-case text check also passes. It covers whitespace, control characters, quotes, reserved-looking text, and Unicode scalar values, including noncharacters. It is not an exhaustive Unicode or maximum-size test.

The new format keeps the public payload free of the internal format marker. Earlier notes retain their previous normalization behavior. Existing text lost during an earlier save cannot be reconstructed from Git. New text uses ASCII JSON in Git and returns the exact input text through the API. Notes in this format require the updated reader. The schema-5 cache rebuild retains embeddings and rebuilds their derived search index. Text that Git already changed cannot be recovered without its original input.

The stale check includes ignored files when a note explicitly names them. An untracked declared file has no saved Git version. The check marks its note stale as a conservative result. It does not detect unlisted dependencies.

The skipped check needs a Gemini API key. The warning concerns a Pydantic forward reference in the MCP lifespan code. These items remain open. An earlier sandbox run could not run local socket tests. The completed full suite had permission to use loopback sockets.

Evidence is in `benchmarks/research/evidence/v3/`. The new tests are in `agent_memory/tests/test_reliability_audit.py`. These fixes improve the tested failure cases. They do not establish a general model performance gain.
