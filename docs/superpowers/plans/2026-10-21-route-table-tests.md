# Route table tests (Phase A) Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Pure `resolve_text_route` + table tests + wire `card_mode` in handlers.

**Architecture:** New `bot/route.py`; handlers call it before chitchat when `card_mode`; tests drive the table without Telegram/LLM.

**Tech Stack:** Python, unittest, existing `llm.client` heuristics.

## Global Constraints

- Phase A only — no LLM golden tests
- No Telegram mocks required
- Prefer minimal handlers change

---

### Task 1: Failing table tests

**Files:**
- Create: `tests/test_route.py`

- [ ] Write `TestResolveTextRoute` with table cases (Стол→card, etc.)
- [ ] Run `python -m unittest tests.test_route -v` — expect ImportError / fail
- [ ] Commit only if user asked

### Task 2: Implement `bot/route.py`

**Files:**
- Create: `bot/route.py`

- [ ] `TextRouteContext`, `TextRoute`, `resolve_text_route`
- [ ] Reuse `classify_document_intent`, `looks_like_*`, `detect_transform`
- [ ] Run tests until green

### Task 3: Wire handlers

**Files:**
- Modify: `bot/handlers.py`

- [ ] Before chitchat, if `card_mode`: resolve and handle card/cancel/exit
- [ ] Remove duplicate card_mode block later in `on_text`
- [ ] Run `tests.test_route` + `tests.test_docs.TestIntentRouter`
