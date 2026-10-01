# Design: Professional document understanding (pyBot)

**Date:** 2026-10-01  
**Scope:** Documents only (product cards unchanged)  
**Strategy:** Phase 1 = dialog + operations (A+B); Phase 2 = smarter intent routing (C)

## Goals

A professional, friendly docs bot that:

1. Almost always finishes with a **ready `.docx` file** or a clear next step (buttons).
2. Keeps **multi-turn context**: draft → “дай файл” → “поправь год” → file again.
3. Later expands coverage of free-form phrasing without breaking (1)–(2).

Success metric for Phase 1: the reverse-words chat transcript class of failures does not recur.

## Non-goals (Phase 1)

- Changing card / Wildberries–Ozon flows
- Full LLM-only router replacing heuristics
- Editing non-`.docx` with round-trip save

## Delivery policy

| Operation class | Behavior |
|-----------------|----------|
| Deterministic transform (reverse words, case, …) | Apply immediately, send `.docx` |
| Safe explicit patch (named find→replace) | Apply immediately, send `.docx` |
| Fill / tone / full rewrite / ambiguous LLM plan | Show draft + **Применить / Отмена**; “дай файл / давай / примени” materializes file |

## Architecture

### Session dialog state

Extend `UserSession` / `DocTask`:

- `flow`: `idle` | `awaiting_confirm` | `clarifying`
- `last_op`: short op id (`fill`, `patch`, `tone`, `rewrite`, `reverse_words`, `ask`, `write`, …)
- Keep `pending: PendingEdit | None` for confirmable plans
- Keep `pending_clarify` for create/ask clarifications

**Rules while `flow == awaiting_confirm` and `pending` set:**

1. Cancel phrases → clear pending, `flow=idle`
2. Apply / give-file phrases → materialize + send file, `flow=idle`, remember `last_op`
3. Transform request → run transform on active file (ignore stale pending or clear it), send file
4. Other edit/fill text → new plan (replace pending)
5. Never route to `ask_document` “blank fill” tips

### Message pipeline (docs)

1. If clarifying pending → handle clarify choice / cancel  
2. If awaiting_confirm + pending → rules above  
3. Classify intent (existing heuristics + guards)  
4. If transform detector matches → run catalog op, send file  
5. Else edit / fill / tone / ask / write / risks / … as today  
6. On confirmable plan created → `flow=awaiting_confirm`, set `last_op`

### Transform catalog

New module `edit/transforms.py` (or extend `edit/docx_patch.py`):

- Registry: `op_id → {detect(text), apply(src,dest) | needs_llm}`
- Phase 1 ops:
  - `reverse_words` (exists)
  - `upper_case` / `lower_case` (optional if cheap)
- Detector used before fill/ask so “переверни…” never becomes fill

LLM `plan_edits` remains for semantic edits; transforms bypass LLM.

### Intent / ask guardrails

- `_handle_question` must not answer with “напишите добавь данные” for edit/transform signals (already partially fixed; keep enforced)
- Soft “нужно” + files still defaults to work-on-file, not create clarify (existing guards)
- Phase 2 (later): low-confidence → LLM classify OR clarify buttons; never silent wrong ask

### Testing

- Unit: detectors, apply-pending phrases, reverse_words docx
- Dialog regressions (session-level, no Telegram):  
  1) caption reverse → file path logic  
  2) pending + “дай файл” → materialize  
  3) pending + “год должен быть …” → new edit, not ask  
  4) “нужно перевернуть… скинуть” → transform, not fill

## Phase 2 (C) — deferred

- Optional LLM intent classifier when heuristics return `none` / low confidence
- Broader op catalog (date format, dedupe spaces, anonymize)
- `last_op` + “ещё раз” / “то же на другом файле”

## Risks

- Over-eager apply-pending (“да”) on unrelated yes → mitigate with pending required
- Transform false positives → keep detectors phrase-specific
- Large rewrites still need confirm to avoid silent document destruction
