# Course Discovery Agent (M1)

LangGraph-based, stateful Course Discovery pipeline with:

- structured LLM gateway parsing
- parallel mock discovery workers
- deterministic dedup layer
- LLM digest synthesis
- mandatory human-in-the-loop pause before publish
- conditional routing for `PUBLISH | REWRITE | AUGMENT | RESET | DISCARD`

## Run

1. Install dependencies:

```bash
uv sync
```

2. Set your API key:

```bash
# PowerShell
$env:GOOGLE_API_KEY="your-key"
```

3. Start the demo:

```bash
python main.py
```

The app now auto-loads `.env` at startup, so setting `GOOGLE_API_KEY` in `.env` is enough.

The run pauses before `telegram_gate` (via `interrupt_before`), prints the digest for PM review, accepts feedback in terminal, then resumes from checkpoint with the same `thread_id`.

## Structured Logs

The app emits JSON structured logs to stdout.

- Configure level with `LOG_LEVEL` (default: `INFO`).
- Each log includes an `event` and `run_id` for correlation.
- Sensitive values are sanitized/truncated in log helpers.

## Project Layout

- `main.py` - thin CLI runner
- `course_discovery/app/cli.py` - CLI demo entrypoint and interrupt/resume loop
- `course_discovery/domain/state.py` - `AgentState` contract
- `course_discovery/domain/models.py` - Pydantic models (`SearchFilters`, `CourseCandidate`, `RoutingDecision`)
- `course_discovery/workflows/outer_graph.py` - full graph definition and compile settings
- `course_discovery/app/gateway.py` - LLM structured filter parsing
- `course_discovery/research_agent/search/mock_workers.py` - mock workers and aggregation
- `course_discovery/research_agent/cache/dedup.py` - URL + fuzzy + hash dedup
- `course_discovery/research_agent/synthesis/nodes.py` - LLM digest generation with graceful fallback
- `course_discovery/review/nodes.py` - interrupt target node
- `course_discovery/review/router.py` - feedback router + publish/discard stubs
