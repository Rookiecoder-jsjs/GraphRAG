# RAG Evaluation Harness

A small, self-contained harness for measuring retrieval and (optionally)
answer quality. The goal is to give every change to the RAG pipeline a
**baseline number** so we can tell "this tweak made things better" from
"this tweak made things worse, but the smoke test still passed".

## Why it exists

Every change to query rewrite, chunk size, rerank top_k, embedding model,
or hybrid-search weights used to be evaluated by gut feel. With this
harness, before merging a change you can run a 5-second smoke test and see
`hit@5: 0.83 → 0.91`. After enough gold cases accumulate, it becomes a
proper regression gate.

## What's in here

```
backend/eval/
├── __init__.py           # package marker
├── metrics.py            # pure retrieval + answer metrics
├── runner.py             # async runner + CLI entry
├── test_eval.py          # 39 standalone tests (no pytest required)
├── README.md             # this file
└── gold/
    ├── 01_factual_lookup.json      # example: simple "what is X" question
    ├── 02_cross_chunk_synthesis.json  # example: needs multiple chunks
    └── 03_should_refuse.json       # example: model should decline
```

## Metrics

| Metric | Range | What it answers |
| --- | --- | --- |
| `hit@k` | 0/1 | Did we surface any relevant chunk in the top-k? |
| `mrr` | [0, 1] | How high is the first relevant chunk? |
| `precision@k` | [0, 1] | Of the top-k, what fraction is relevant? |
| `recall@k` | [0, 1] | Of all relevant chunks, what fraction did we find? |
| `ndcg@k` | [0, 1] | Combined precision-and-rank score; the single best summary |
| `keyword_coverage` | [0, 1] | For answer-side scoring (when you don't have chunk_ids) |

All metrics are pure functions in `metrics.py` — no I/O, no async, no
global state. Unit-tested in `test_eval.py`.

## Gold case schema

Each `gold/*.json` file:

```json
{
  "_comment": "Optional free-form note (preserved in reports).",
  "id": "factual-01",          // unique, used for sort + report rows
  "query": "What is X?",
  "expected_chunk_ids": [      // optional: list of chunk UUIDs
    "uuid-from-your-db"
  ],
  "expected_keywords": [       // optional: list of strings to find in answer
    "Paris"
  ],
  "difficulty": "easy",        // easy | medium | hard — for slicing reports
  "tags": ["factual"]          // free-form, for grouping
}
```

**You only need ONE of `expected_chunk_ids` / `expected_keywords`.**
- `chunk_ids` is the more precise signal (real retrieval eval). To find
  these, ask the question in the UI, look at the source card, copy the
  chunk_id from the URL or DB, paste it in.
- `keywords` is the cheap fallback (good enough for "does the model
  talk about X at all"). Pass `expected_chunk_ids: []` and the runner
  will skip chunk metrics and only score `keyword_coverage`.

## How to run

### 1. Unit tests (no infra required)

Standalone — uses a custom check() runner, no pytest needed:

```bash
cd backend
../.venv/Scripts/python.exe eval/test_eval.py
```

With pytest (recommended for IDE/CI):

```bash
pip install pytest pytest-asyncio
cd backend
../.venv/Scripts/python.exe -m pytest eval/test_eval.py -v
```

Expected: `All checks passed (39 tests).`

### 2. Live retrieval eval (no LLM, ~1 second per case)

Calls the real `build_rag_context` from `app.api.chat` against your running
Neo4j + ChromaDB. **Does not call the LLM**, so it's fast and cheap.

```bash
cd backend
../.venv/Scripts/python.exe -m eval.runner --user-id 1 --no-llm
```

Output (flat summary):
```
Cases: 3  elapsed: 0.42s
  hit@1                0.333
  hit@10               1.000
  mrr                  0.778
  ndcg@5               0.812
  precision@5          0.600
  recall@5             1.000
  ...
```

Add `--markdown` for a human-readable table, or `--json` for a structured
report you can feed into a CI bot.

### 3. Full RAG eval (with LLM, slower, more expensive)

Same as above but also calls the LLM to produce a final answer, then
scores `keyword_coverage` for refusal cases:

```bash
../.venv/Scripts/python.exe -m eval.runner --user-id 1
```

Each case now spends API credits. Use sparingly — the `--no-llm` mode is
plenty for catching retrieval regressions.

## How to add a new gold case

1. Upload a document via the UI.
2. Open the chat, ask the question, click the source chip to confirm
   which chunks are right.
3. Find the chunk UUIDs: easiest is to look at the JSON response of
   `POST /api/search` or query the `chunks` table directly:
   ```sql
   SELECT chunk_id, substr(content, 1, 80) FROM chunks WHERE user_id = 1;
   ```
4. Create `gold/04_my_new_case.json`:
   ```json
   {
     "id": "my-new-case",
     "query": "...",
     "expected_chunk_ids": ["paste-uuid-here"],
     "expected_keywords": [],
     "difficulty": "medium",
     "tags": ["factual"]
   }
   ```
5. Re-run the eval. The new case shows up in the report automatically.

## Workflow

Suggested:

- **Before any RAG-pipeline change**: run `--no-llm` to record baseline
  numbers. Write them down (or pipe `--json` somewhere).
- **After the change**: re-run. If `hit@5` drops, you broke retrieval.
  If `ndcg@5` improves, you made the ranking better. If both drop, revert.
- **Adding new features that touch retrieval** (e.g. graph-rag, query
  expansion): add 3-5 gold cases per feature that exercise the new
  capability, then verify the new path doesn't regress the existing
  cases.

## Extending

- **LLM-as-judge**: today the harness uses cheap keyword coverage for
  answer-side scoring. A future upgrade could call a stronger LLM
  (e.g. GPT-4) to grade answer faithfulness. Add a `judge.py` with an
  async function `judge(query, answer, sources) -> score`, and wire it
  via a third `answer_provider`-style callable in `runner.py`.
- **Per-difficulty slicing**: `aggregate()` doesn't slice by difficulty
  yet. The per-case rows are tagged though, so this is a 10-line
  post-processing job.
- **Threshold-based CI**: wrap the runner, parse `--json`, exit non-zero
  if any metric drops >X% from a baseline file. Worth doing once you
  have 20+ cases.
