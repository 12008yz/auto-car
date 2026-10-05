# Design: Human-like understanding (pyBot plan B)

**Date:** 2026-10-05  
**Scope:** Documents (+ sticky card_mode hygiene). Cards product pipeline unchanged.  
**Strategy:** Dialog hygiene → phrase fixes → “Понял так” → LLM classify on doubt → edit polish.

## Goals

1. User can switch tasks without the bot staying in the previous mode.
2. Ambiguous / soft Russian phrasing routes correctly (ask vs edit vs write vs card).
3. Costly actions show a short confirmation of understanding when confidence is low.
4. Answers and edits match the user’s intent; usage counters stay honest.

## Non-goals

- Full LLM-only agent replacing all heuristics.
- OCR / scan PDF fill.
- Turning off confirm for large rewrites.

## Delivery policy

| Situation | Behavior |
|-----------|----------|
| Menu / docs / card / file upload / explicit cancel | Clear sticky dialog modes (`awaiting_write`, `clarifying`, `awaiting_gap_fill`; clear `pending_clarify`; optionally clear `card_mode` on docs/home/upload) |
| `awaiting_write` + cancel / chitchat / non-write intent | Exit write wait; handle new intent |
| `awaiting_confirm` + ask / risks / extract / compare / check | Answer; keep or clear pending per UX (prefer: answer without consuming pending unless apply/cancel) |
| High-confidence simple ask / explicit replace / transform | Act immediately (short status OK) |
| Write / large edit / low-confidence classify | “Понял так: …” + Да / Уточню when needed |
| Heuristic `none` / low confidence | LLM intent classify → same intent set; still unsure → soft landing |

## Architecture

### `UserSession.clear_dialog_modes(...)`

Central helper:

- Always: `flow=idle` (unless keeping confirm), clear `pending_clarify`, `awaiting_gap_fill=False`
- Flags: `keep_pending`, `keep_card_mode`, `keep_awaiting_write`
- Called from: `menu:home`, docs entry, card entry (set card), document ingest, cancel paths

### Intent pipeline order (`on_text`)

1. Reply buttons (existing)
2. If `awaiting_write`: cancel/chitchat → idle; else if strong non-write intent → clear write wait and classify; else write topic
3. If pending confirm: cancel / apply / transform / **doc Q&A intents** / else new edit plan
4. Classify (heuristics + guards)
5. If low confidence / none → optional LLM classify
6. Soft landing / act / “Понял так” gate for costly ops

### Phrase / guard fixes

- `правк` must not match inside `справка`
- FIO fill requires name/date signals, not any two words + `фио`
- Strong create includes past forms `написал` / `создал` (stem or list)
- `договор` / `акт` as create targets with strong verb
- Tone synonyms: `сделать короче`, `упрости язык`
- Soft create with files: prefer clarify create-vs-ask when strong create present (already); soft “нужна справка” → write_form or clarify, not edit

### Confirm understanding

Callback `confirm:yes` / `confirm:no` with stored `PendingClarify`-like payload (`pending_action`: action + prompt + mode).

### Billing honesty

- Refund `ask` when model answer matches “not found” patterns (same as empty RAG soft landing path).
- Do not clear `pending` before successful materialize; restore on failure.

## Testing

- Session: clear_dialog_modes; menu-equivalent resets
- Handlers/dialog: awaiting_write + «отмена»; pending + ask intent
- Intent: справка, верно ли ФИО, написал реферат, составь договор, сделать короче
- Balance: ask refund on not-found text (unit on handler helper if extractable)

## Rollout order

1. Sticky hygiene + awaiting_write / confirm escapes  
2. Phrase/guard fixes  
3. “Понял так” gate for write (+ optional edit)  
4. LLM classify on low confidence  
5. Edit polish (materialize restore, compare labels, soft-landing docx-only tips, ask refund)
