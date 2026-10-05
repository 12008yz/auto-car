# Human-like understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the docs bot stop sticky-mode misfires, classify common Russian phrases correctly, confirm understanding on costly actions, and keep answers/edits aligned with user intent.

**Architecture:** Central `clear_dialog_modes` on navigation/upload; escape hatches in `awaiting_write` / pending-confirm; heuristic/guard phrase fixes; optional “Понял так” gate; LLM classify only on low confidence; billing/edit polish.

**Tech Stack:** Python, aiogram handlers, existing `llm.client` heuristics, unittest.

**Spec:** `docs/superpowers/specs/2026-10-05-human-understanding-design.md`

## Global Constraints

- Do not replace the whole router with LLM-only.
- Keep Apply/Cancel for LLM rewrite/fill/tone plans.
- Callbacks that bill must pass `user=query.from_user`.
- Prefer small focused changes; extend existing tests in `tests/test_docs.py` / `tests/test_clear_billing.py`.

---

### Task 1: Session dialog hygiene helper

**Files:**
- Modify: `bot/session.py`
- Test: `tests/test_docs.py`

**Interfaces:**
- Produces: `UserSession.clear_dialog_modes(*, keep_pending: bool = False, keep_card_mode: bool = False) -> None`

- [ ] **Step 1: Failing test** — after setting awaiting_write/clarifying/gap_fill/pending_clarify/card_mode, `clear_dialog_modes()` resets them; `keep_pending=True` preserves pending.
- [ ] **Step 2: Implement** helper on `UserSession`.
- [ ] **Step 3: Wire** menu home/docs/card, document ingest, cancel paths in `bot/handlers.py`.

### Task 2: Escape awaiting_write and pending-confirm

**Files:**
- Modify: `bot/handlers.py` (`on_text`)
- Test: `tests/test_docs.py` (session-level / classify + flow helpers)

- [ ] **Step 1: Tests** — cancel/chitchat exit awaiting_write; ask-like intent while pending does not force edit-only (document expected behavior via helper if pure unit).
- [ ] **Step 2: Implement** awaiting_write escapes; pending branch routes ask/risks/extract/compare/check via classify before edit fallback.

### Task 3: Phrase / guard fixes

**Files:**
- Modify: `llm/client.py`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Failing tests** for справка, Верно ли ФИО?, …написал реферат (files), Составь договор, Можно сделать короче?
- [ ] **Step 2: Fix** edit substring, FIO fill, strong create past tense, договор keys, tone synonyms.

### Task 4: “Понял так” gate for write

**Files:**
- Modify: `bot/handlers.py`, `bot/ui.py`, `bot/session.py` (pending action store)
- Test: wiring tests for keyboard callbacks

- [ ] Soft confirm before write when entered via soft landing / low-confidence path; skip for high-confidence direct write_text with strong create.

### Task 5: LLM classify on low confidence

**Files:**
- Modify: `llm/client.py`, `bot/handlers.py`
- Test: mockable unit if extractable; otherwise guarded integration skip

- [ ] When heuristic intent `none` or conf &lt; threshold, call LLM classify returning same intent schema; fall back to soft landing.

### Task 6: Edit / billing polish

**Files:**
- Modify: `bot/handlers.py` (`_materialize_pending`, `_handle_question`, soft landing copy, compare status)
- Test: refund helper / materialize restore if unit-testable

- [ ] Restore pending on materialize failure; refund ask on not-found answers; show which two files compare; soft landing note when non-docx.

---

## Execution note

Implement Tasks 1→3 first (highest user pain). Tasks 4–6 follow in the same effort if time allows.
