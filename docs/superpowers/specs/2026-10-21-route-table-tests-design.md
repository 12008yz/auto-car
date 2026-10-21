# Phase A: session-aware text routing + table tests

**Date:** 2026-10-21  
**Scope:** Phase A only (no LLM quality / golden answers).

## Problem

Session modes (`card_mode`, `awaiting_gap_fill`, files in index) change how the same text must be handled. Example: after «Карточка», «Стол» must make a product card, not «посмотрю в файле…». Logic lived only in `handlers.py`, hard to regress-test.

## Goal

1. Pure function `resolve_text_route(text, ctx) → TextRoute` (no Telegram, no LLM refine).
2. Table-driven tests: many `{ctx, text} → action/intent` cases.
3. `on_text` uses the router for `card_mode` (and dispatch intent), so behaviour and tests stay aligned.

## Non-goals

- Mocking Telegram / end-to-end bot runs
- LLM refine / quality golden sets (Phase B)
- Full rewrite of `on_text`

## API

```python
@dataclass(frozen=True)
class TextRouteContext:
    card_mode: bool = False
    has_files: bool = False
    has_facts: bool = False
    awaiting_gap_fill: bool = False

@dataclass(frozen=True)
class TextRoute:
    action: str  # card | cancel_card | exit_card_menu | dispatch
    intent: str = ""
    mode: str = ""
    confidence: float = 0.0
    clear_card_mode: bool = False
    decision: dict | None = None
```

### Priority when `card_mode`

1. cancel words → `cancel_card`
2. chitchat (and not card-like) → `exit_card_menu`
3. explicit file edit/fill/transform/reverse → clear card, `dispatch` via `classify_document_intent`
4. else → `card` (including short names like «Стол»)

### Without early card exit

`action=dispatch`, fields from `classify_document_intent` (existing heuristics).

## Handler wiring

- Call `resolve_text_route` **before** the global chitchat branch when `session.card_mode` (so product names are not stolen).
- Handle `card` / `cancel_card` / `exit_card_menu` there; on `dispatch` after card override, clear `card_mode` and continue with existing classify/LLM refine path **or** reuse `route.decision` for the first heuristic pass.

## Tests

`tests/test_route.py` — table (list or YAML) of cases; at least:

| ctx | text | expect |
|-----|------|--------|
| card_mode + files | Стол | action=card |
| card_mode | отмена | cancel_card |
| card_mode | привет | exit_card_menu |
| card_mode + files | замени ФИО… | dispatch, intent=edit |
| files | Что в договоре про срок? | ask |
| awaiting_gap_fill + files | Богородицк | gap_fill |
| card_mode | керамическая кружка 300 мл | card |

Rule: every production routing bug → new table row.

## Success

`python -m unittest tests.test_route -v` green; «Стол» after card cannot regress without a failing test.
