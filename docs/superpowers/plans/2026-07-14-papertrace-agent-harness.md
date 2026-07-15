# PaperTrace Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-memory PaperTrace task runner with a persistent, observable, recoverable single-agent harness while preserving existing analysis results and UI workflows.

**Architecture:** A database-backed Run Store and single worker drive a deterministic research phase state machine. Each phase uses a bounded action loop, a typed tool registry, policies, and hooks; existing search, extraction, relation, timeline, review, and export functions remain the domain implementation. The API keeps legacy endpoints as compatibility adapters while the frontend gains run progress and trace controls.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite/PostgreSQL, pytest, Next.js 14, React 18, TypeScript, Tailwind CSS, Render, GitHub Actions.

---

## File Map

**Backend core**

- Create `backend/agent/__init__.py`: public Agent Harness exports.
- Create `backend/agent/schemas.py`: enums and Pydantic contracts for actions, tools, runs, and events.
- Create `backend/agent/models.py`: SQLAlchemy Run, Step, ToolCall, Artifact, and Event models.
- Create `backend/agent/repository.py`: all Agent persistence and state transitions.
- Create `backend/agent/policies.py`: bounded step, retry, repeat, timeout, and artifact policies.
- Create `backend/agent/hooks.py`: Hook protocol, HookBus, and built-in persistence/progress hooks.
- Create `backend/agent/tools.py`: Tool contract, registry, and adapters around existing PaperTrace functions.
- Create `backend/agent/model_provider.py`: DeepSeek structured-action provider and deterministic test provider.
- Create `backend/agent/phases.py`: phase order, tool allowlists, and required artifacts.
- Create `backend/agent/loop.py`: bounded per-phase action loop.
- Create `backend/agent/harness.py`: run lifecycle, checkpoint recovery, cancellation, and degradation.
- Create `backend/agent/worker.py`: embedded and standalone single worker.
- Modify `backend/database.py`: engine portability and Agent model registration.
- Modify `backend/main.py`: Agent API and legacy endpoint adapters.
- Modify `backend/requirements.txt`: PostgreSQL, migration, and test dependencies.

**Tests and evals**

- Create `backend/tests/conftest.py`: isolated SQLite database fixtures.
- Create `backend/tests/test_agent_repository.py`: persistence and state transition tests.
- Create `backend/tests/test_agent_runtime.py`: policy, Hook, ToolRegistry, loop, and recovery tests.
- Create `backend/tests/test_agent_api.py`: FastAPI compatibility and Agent endpoint tests.
- Create `backend/evals/cases/smoke.json`: deterministic research-run fixture.
- Create `backend/evals/cases/relations.json`: relation metric fixture.
- Create `backend/evals/datasets.py`, `metrics.py`, `fakes.py`, `runner.py`: offline and opt-in live eval runner.

**Frontend and delivery**

- Modify `frontend/lib/api.ts`: Agent statuses, events, trace, input, cancel, and retry API.
- Create `frontend/components/AgentTrace.tsx`: compact phase progress and trace UI.
- Modify `frontend/app/result/[taskId]/page.tsx`: poll Agent status/events and handle waiting, cancel, and retry.
- Modify `frontend/components/RecommendationPanel.tsx`: read backend-generated recommendation artifact instead of `/api/chat`.
- Modify `render.yaml`: PostgreSQL-backed web service and one worker.
- Create `.github/workflows/ci.yml`: backend tests/evals and frontend build.
- Modify `backend/.env.example`, `README.md`, and `DEPLOY.md`: local and production operations.

## Task 1: Persistent Run Store

- [ ] Write repository tests that create a Run, append ordered events, save a versioned Artifact, transition through `queued -> running -> waiting_input -> queued -> completed`, and recover stale `running` rows.
- [ ] Run `python -m pytest backend/tests/test_agent_repository.py -v`; expect failure because Agent models do not exist.
- [ ] Add Agent enums and ORM models with JSON payload columns, UTC timestamps, indexes on `(status, created_at)` and `(run_id, sequence)`, and uniqueness on Artifact `(run_id, kind, version)`.
- [ ] Add `AgentRepository` methods `create_run`, `get_run`, `claim_next_run`, `transition`, `append_event`, `save_artifact`, `latest_artifact`, `append_step`, `append_tool_call`, `recover_running`, `submit_input`, and `retry_run`.
- [ ] Make `database.py` use SQLite-only `check_same_thread`, normalize `postgres://` to `postgresql+psycopg://`, and import Agent models before `create_all`.
- [ ] Run the repository tests and `python -m compileall backend`; expect all to pass.
- [ ] Commit as `feat(agent): add persistent run store`.

## Task 2: Runtime Contracts, Policies, and Hooks

- [ ] Write tests for Pydantic Action parsing, phase-specific tool permissions, maximum steps, duplicate call rejection, Hook order, pause/reject decisions, and Hook state immutability.
- [ ] Run `python -m pytest backend/tests/test_agent_runtime.py -k "schema or policy or hook" -v`; expect failure.
- [ ] Implement `ToolAction`, `CompletePhase`, `RequestInput`, `AbortRun`, `ToolResult`, `RunPolicy`, `HookContext`, `HookDecision`, `AgentHook`, and ordered `HookBus`.
- [ ] Implement `BudgetHook`, `TraceHook`, `ProgressHook`, `PersistenceHook`, and `RecoveryHook` using repository methods rather than direct ORM mutation.
- [ ] Run focused runtime tests; expect all selected tests to pass.
- [ ] Commit as `feat(agent): add runtime contracts and hooks`.

## Task 3: Tool Registry and PaperTrace Adapters

- [ ] Write tests using fake tools to verify input/output validation, phase allowlists, argument hashing, result reuse, one retry, and normalized error codes.
- [ ] Add adapter tests with patched domain functions for search, extraction, relation analysis, synthesis, verification, and finalization.
- [ ] Run `python -m pytest backend/tests/test_agent_runtime.py -k tool -v`; expect failure.
- [ ] Implement `AgentTool`, `ToolContext`, `ToolRegistry`, and tools `search_papers`, `extract_claims`, `classify_relations`, `build_timeline`, `generate_review`, `recommend_directions`, `verify_evidence`, and `finalize_report`.
- [ ] Preserve current result shape in `final_report`: `task_id`, `query`, `papers`, `claims`, `matrix`, `stats`, `timeline`, and `data_fetched_at`.
- [ ] Move recommendation prompt construction to the backend adapter and return typed questions/methods.
- [ ] Run tool tests and existing compile checks; expect pass.
- [ ] Commit as `feat(agent): expose research tools`.

## Task 4: Model Provider, Phase Loop, and Harness

- [ ] Write deterministic tests where a fake provider emits tool actions followed by phase completion, requests user input, exceeds a policy, and resumes from saved artifacts.
- [ ] Run `python -m pytest backend/tests/test_agent_runtime.py -k "loop or harness or recovery" -v`; expect failure.
- [ ] Implement phase definitions for `PLAN`, `DISCOVER`, `EXTRACT`, `ANALYZE`, `SYNTHESIZE`, `VERIFY`, and `FINALIZE`, including required artifacts and tool allowlists.
- [ ] Implement `DeepSeekModelProvider.next_action()` with a strict JSON response schema and concise `rationale`; never request or store hidden chain-of-thought.
- [ ] Implement the bounded loop: invoke hooks, validate actions, enforce policy, execute tools, persist observations, complete phases, pause for input, and degrade from available artifacts when budget is exceeded.
- [ ] Implement `AgentHarness.run(run_id)` and checkpoint recovery; cancellation takes effect at the next step boundary and failed retry uses the same Run.
- [ ] Run runtime tests; expect pass.
- [ ] Commit as `feat(agent): implement bounded research loop`.

## Task 5: Worker and Agent API Migration

- [ ] Write FastAPI tests for run creation, status, incremental events, trace, result, input, cancel, retry, and 404/409 errors.
- [ ] Add compatibility tests proving `/api/analyze`, `/api/task/{id}`, `/api/result/{id}`, `/api/review/{id}`, and `/api/export/{id}` use persistent Agent data.
- [ ] Run `python -m pytest backend/tests/test_agent_api.py -v`; expect failure.
- [ ] Implement standalone polling worker and embedded worker lifecycle controlled by `AGENT_EMBEDDED_WORKER`.
- [ ] Add `/api/agent/runs` endpoints and change legacy endpoints into adapters over `AgentRepository`.
- [ ] Remove runtime dependence on `TASKS` and FastAPI `BackgroundTasks`; remove public `/api/chat` after RecommendationPanel migration.
- [ ] Run API and full backend tests; expect pass.
- [ ] Commit as `feat(api): serve persistent agent runs`.

## Task 6: Agent Trace Frontend

- [ ] Extend API types with `AgentStatus`, `AgentPhase`, `AgentEvent`, `AgentTraceStep`, and run control functions.
- [ ] Build `AgentTrace.tsx` with stable phase rows, status icons from lucide-react, current action, event summaries, waiting-input form, cancel, and retry controls.
- [ ] Update result-page polling to accept legacy and Agent states, incrementally fetch events with `after_seq`, load the result on `completed`, and render partial progress without layout shifts.
- [ ] Replace RecommendationPanel raw chat calls with data from the persisted final result/recommendation artifact.
- [ ] Run `npm run build` from `frontend`; expect TypeScript, lint, and static generation to pass.
- [ ] Commit as `feat(frontend): show agent execution trace`.

## Task 7: Eval Harness, CI, and Deployment

- [ ] Add deterministic smoke and relation cases with expected phase order, artifact kinds, completion state, relation labels, and citation coverage thresholds.
- [ ] Implement dataset loading, completion/tool/relation/citation metrics, fake provider/tool fixtures, JSON report output, and Markdown summary output.
- [ ] Run `python -m evals.runner --suite smoke`; expect exit code 0 without API keys.
- [ ] Add PostgreSQL driver, Alembic configuration and initial Agent migration; verify SQLite `upgrade head` on a temporary database.
- [ ] Add Render PostgreSQL, web, and worker services; set `AGENT_EMBEDDED_WORKER=false` in production.
- [ ] Add GitHub Actions jobs for `pytest`, offline smoke eval, `compileall`, `npm ci`, and `npm run build`.
- [ ] Update environment examples, README architecture/commands, and DEPLOY migration instructions.
- [ ] Commit as `chore: add agent evals and deployment`.

## Task 8: End-to-End Verification and Publish

- [ ] Run `python -m pytest backend/tests -v` from repository root; expect all tests pass.
- [ ] Run `python -m evals.runner --suite smoke`; expect a passing JSON and Markdown report.
- [ ] Run `python -m compileall backend`; expect success.
- [ ] Run `npm ci` and `npm run build` in `frontend`; expect Next.js build success.
- [ ] Start local backend and frontend, create a deterministic/offline Run, and verify status, events, trace, result, cancel/retry, and waiting-input flows.
- [ ] Inspect `git diff --check`, `git status -sb`, and the complete branch diff; stage only Harness-related files.
- [ ] Push `agent/papertrace-harness` to `origin` and open a draft PR against `main` with design, implementation, migration, and validation notes.
