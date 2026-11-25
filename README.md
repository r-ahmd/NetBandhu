# NetBandhu

The subscriber's side of an ISP call centre: a cited answer in one turn, and no
bill credit without the billing supervisor.

Built from `proposal.pdf` in `../Industrial Training/`. All seven core concepts and
the electives it claims are implemented and demonstrated; where the proposal and
the measurement disagree, the measurement is recorded below.

## Run it

```bash
uv sync                       # Python 3.12, pinned
cp .env.example .env          # then paste your keys

uv run python -m kb.ingest    # build the knowledge base (57 chunks, 4 documents)
uv run pytest -q              # 20 tests: the money rules and the rails
uv run python demo.py         # the scripted demo, end to end
uv run python cli.py          # interactive; /approve /edit /reject /pending
uv run uvicorn api:app --port 8077     # then open http://127.0.0.1:8077
```

`demo.py` runs each phase in a **separate process** on purpose: that is the only
honest way to show a pause that outlives the program.

## What it proves

| Claim | Where | Verified |
|---|---|---|
| C1 structured output | `schemas.py` — `SlaVerdict` recomputes the credit and refuses a verdict whose amount disagrees | `tests/test_credits.py` |
| C2 model layer + fallback | `models.py` — OpenRouter primary, Gemini fallback, rate limiter, retries | `models.py` runs standalone |
| C3 tools + agent loop | `tools.py` five tools; `create_agent` over the read tools | `uv run python -m tools --selftest` |
| C4 conversation memory | `SqliteSaver`, one thread per subscriber | `demo.py` turns 2–5 |
| C5 safety rails + approval | `middleware.py` rails; `workflows/credit.py` `interrupt()` | `tests/test_rails.py`, demo [5]–[6] |
| C6 RAG with citations | `kb/` — `[SLA · 3.2]` labels, one fixed refusal | `uv run python -m evals.run_eval` |
| C7 graph orchestration | `workflows/graph.py` — router, four destinations, one pause | `uv run python workflows/graph.py` (mermaid) |
| E1 corrective RAG | `retrieve → grade → rewrite → retrieve`, cap 2 in state | `demo.py` turn 2 |
| E2 durable pause/resume | subgraph compiles without a saver, inherits the parent's | `demo.py` [5]–[6]: separate processes |
| E3 long-term memory | `memory.py` — Store keyed by subscriber | preference survives a new thread |
| Serving | `api.py` + `static/index.html` — SSE over HTTP | `uv run pytest tests/test_api.py` |

## Scorecard

`uv run python -m evals.run_eval` — 12 questions, 8 answerable and 4 not:

```
            refusal  citation  retrieval
all            1.00      1.00       1.00
answerable     1.00      1.00       1.00
unanswerable   1.00      1.00          —
```

Stable across three consecutive runs at `TEMPERATURE=0`.

## Measured, not assumed

Four things the proposal says that the machine disagreed with. Each is a decision
here, and the reason is in the code beside it.

1. **OpenRouter through `langchain-openrouter` hangs.** The provider's own SDK
   retries internally and ignores LangChain's `timeout`, so a stall never surfaced
   as an error — it hung past every limit. The same endpoint through `ChatOpenAI`
   with `base_url` answered in 1.7 s. `models.py` uses that path.
2. **The fallback is Gemini, not Groq.** The proposal names `groq:openai/gpt-oss-20b`.
   Measured here, Groq returned an **empty content string** for a trivial prompt. A
   fallback that answers nothing is worse than no fallback, so `google_genai:
   gemini-3.1-flash-lite` is the fallback and Groq stays available as a third key.
3. **Embeddings are fastembed, not Gemini.** The first ingest hit
   `429 RESOURCE_EXHAUSTED` — the free tier allows 100 embed requests per minute and
   the corpus is 57 chunks in one batch. Retrying on a 58-second timer in front of an
   audience is not a demo, so the default is `BAAI/bge-small-en-v1.5` on CPU: no key,
   no network after the first download. Gemini remains one env var away, and writes a
   different collection.
4. **`temperature=0` is not cosmetic.** At the provider default the same three
   questions answered correctly in one run and refused in the next, and the scorecard
   moved with nothing but the seed. A number that changes when nothing changed is not
   a measurement.

## Two bugs the demo found, kept as tests

- **A thread's state outlives its turn.** A follow-up turn inherited the previous
  turn's `answer`, and the router sent it straight to `respond` with yesterday's
  reply — "can I have the credit again?" got the old approval sentence. `intake` now
  clears the per-turn keys; `messages` and `subscriber_id` are *not* cleared, because
  they are the thread.
- **A rail that covered one of two paths is not a rail.** The content filter was a
  `@wrap_tool_call` hook, which fires only when the *agent* calls a tool. The credit
  desk calls `check_sla` directly from a node, so the first adversarial run walked
  past it into the credit branch. `screen()` is now a function that `intake` calls
  for every turn, and the middleware keeps it for tool calls.

## The money rule, in one place

SLA §3.2: `credit = monthly_fee / 24 × downtime_hours`, due only when downtime
exceeds four continuous hours (§3.1). The corpus says it, `settings` says it, and
`SlaVerdict` recomputes it and refuses to be constructed with any other number — so
the ৳267 on screen is a property of the program, not of the prompt.

The planted edge cases (`data/backend.yml`):

| Incident | Downtime | Plan | Credit |
|---|---|---|---|
| INC-8809 | 3 h 50 m | ৳1,200 | **none** — below the threshold, and the answer says why |
| INC-8812 | 5 h 20 m | ৳1,200 | **৳267** — the demo incident |
| INC-8815 | 4 h 10 m | ৳1,800 | **৳313** — just over the line |
| INC-8801 | 5 h 30 m | ৳1,800 | already credited → **৳0**, and SLA §3.5 is cited |

## The web surface

`api.py` is deliberately thin: the graph does the work, `workflows/events.py` owns
the vocabulary, and the API translates HTTP into calls on those two.

| Endpoint | Does |
|---|---|
| `GET /` | the chat page — one file, no build step |
| `POST /chat` | runs a turn, streams SSE until `done` **or** `interrupt` |
| `GET /pending/{thread}` | is this thread paused, and with what? the page asks on reload |
| `POST /decide` | resume with `approve`, `edit <amount>` or `reject <reason>` |
| `GET /health`, `/subscribers` | configuration, and the demo logins |

Two guards are HTTP-level, not prompt-level: a rejection without a reason is `422`,
and deciding a thread that is not paused is `409`.

### Test it by hand

**Testing by hand:** [API_TEST_CASES.md](API_TEST_CASES.md) — 25 cases for the
Swagger UI at `/docs`, each with the exact request body, the expected status *and
value*, and what it proves. 21 of them were executed against a live server and the
results are recorded in the file.

**Two things to know:** embedded Qdrant locks its folder, so the server and a test run
cannot hold the collection at the same time. Stop the server before `pytest`, or the
suite fails with *"Storage folder … already accessed by another instance"*.

And there is **no authentication**: `/subscribers` is a list of demo logins and
`/decide` accepts any caller. `--host 0.0.0.0` exposes that to your network. Fine for
a demo on a laptop; not a deployment.

## Layout

```
core/          config.py (typed settings), exceptions.py
schemas.py     every structured call, with the validators that enforce the rules
models.py      the model layer: fallback, rate limit, retries, ask_json, prompt stamps
prompts/       route.yml, rag.yml, credit.yml, memory.yml — text in YAML, versioned
corpus/        the four documents; source.py generates them, .md are what is indexed
data/          backend.yml — the mock records, with the edge cases above
tools.py       five tools; check_sla computes, propose_credit only proposes
kb/            store.py (Qdrant), ingest.py, rag.py (grade · rewrite · generate)
middleware.py  content filter, PII, authorise, limits, retry
memory.py      the Store, keyed by subscriber
workflows/     state.py, nodes.py, graph.py, credit.py, events.py (the event contract)
evals/         rag_eval.yml + run_eval.py
tests/         the money rules, the rails, and the HTTP contract
api.py         the HTTP surface: /chat (SSE), /decide, /pending
static/        index.html — the chat page, no build step
cli.py         chat, /pending, /approve, /edit, /reject
demo.py        the scripted demo
```
