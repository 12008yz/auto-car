# Docs Understanding Phase 1 Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Make document chat professionally finish with files and keep multi-turn pending context (A+B).

**Architecture:** Session flow state + transform catalog + delivery policy (auto-file for transforms, confirm for LLM rewrite/fill/tone). Cards untouched.

**Tech Stack:** Python, aiogram, python-docx, existing llm/client heuristics

## Global Constraints

- Documents only; do not change card flows
- Prefer deterministic transforms over LLM for mechanical ops
- No git commit unless user asks
- Keep existing unittest style in `tests/test_docs.py`

## File map

- `bot/session.py` — `flow`, `last_op` helpers
- `edit/transforms.py` — NEW catalog + detectors
- `edit/docx_patch.py` — keep reverse_words primitives; catalog wraps them
- `bot/handlers.py` — pipeline order, delivery policy, pending rules
- `llm/client.py` — ensure reverse/transform not fill; apply-pending phrases
- `tests/test_docs.py` — dialog + transform regressions

---

### Task 1: Session flow fields

- [ ] Add `flow: str = "idle"` and `last_op: str = ""` on `UserSession` (or `DocTask`)
- [ ] Helpers: `set_awaiting_confirm(op)`, `clear_pending_flow()`, `remember_op(op)`
- [ ] Reset on `reset_memory`
- [ ] Test reset clears flow/last_op

### Task 2: Transform catalog

- [ ] Create `edit/transforms.py` with `detect_transform(text) -> str | None` and `apply_transform(op, src, dest)`
- [ ] Register `reverse_words` (and optionally case)
- [ ] Tests for detect + apply

### Task 3: Handler pipeline

- [ ] awaiting_confirm branch uses apply/cancel/transform/new-edit rules
- [ ] On transform: auto send file, clear pending, set last_op
- [ ] On LLM fill/tone/rewrite plan: awaiting_confirm + keyboard
- [ ] On explicit patch-only plans that are tiny/safe: optional auto-file (or keep confirm for fill — follow design: fill/tone/rewrite confirm)
- [ ] `_handle_question` never traps transforms

### Task 4: Regression tests + bot restart

- [ ] Dialog phrase tests
- [ ] Run `unittest discover -s tests -v`
- [ ] Restart single bot poller
