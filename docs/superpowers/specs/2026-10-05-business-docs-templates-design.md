# Design: Business document templates for IP / SMB (pyBot)

**Date:** 2026-10-05  
**Scope:** Create filled business `.docx` drafts (not tax filing, not OCR, not cards).  
**Strategy:** Typed templates + field extraction + short clarify → render `.docx`.

## Goals

1. User describes a document in natural Russian → gets a usable filled `.docx` draft.
2. Support five types: service contract, acceptance act, invoice (счёт), claim (претензия), business letter.
3. If required fields are missing → ask 1–3 short questions; if enough → file immediately.
4. Do not invent INN / bank details / other people’s names.
5. Honest product stance: draft for human review, not legal advice / official FNS form.

## Non-goals

- Tax declarations, FNS XML, official government form fidelity.
- OCR / photo-of-notes → document.
- Excel / multi-file “deal pack” generation in one shot.
- Long “how to submit” guides (v1 caption is one short check line only).
- Replacing existing academic essay / blank “form” flows entirely (they stay for write_text / write_form).

## Product decisions (locked)

| Decision | Choice |
|----------|--------|
| Audience focus | IP / small business routine paperwork |
| Document set v1 | Договор услуг, акт, счёт, претензия, письмо |
| Interaction | Free text in; clarify only gaps; one document per run |
| Output | `.docx` only + caption: «Черновик — проверьте реквизиты и суммы перед отправкой.» |
| Architecture | Smart templates (code skeleton) + LLM fills fields |
| Legal stance | Draft / helper, not lawyer or tax agent |

## User flow

1. After normal intent classify: if text matches a business kind (or user picked a kind button), enter **business_write** path. This path **wins** over academic `write_form` / `write_text` / `generate_document` form-mode for the five kinds.
2. Detect `doc_kind` ∈ `{contract, act, invoice, claim, letter}` (heuristics first; LLM only if ambiguous).
3. Extract fields from the **current chat text** only (v1: no photo OCR, no “use uploaded file as source” unless we explicitly add it later).
4. If required / soft-required fields missing → stay in dialog, ask only those fields (see Clarify state below).
5. `consume(write)` **only when starting render** (not before clarify). On render failure → refund.
6. Render typed `.docx`, ingest into session, send file + short caption.
7. Follow-up “поправь сумму…” uses existing edit path on the new `.docx`.

## Field model

### contract (договор оказания услуг)

- **Required:** party_a, party_b, subject, price, term  
- **Optional:** inn_a, inn_b, address_a, address_b, payment_order, acceptance_order

### act (акт)

- **Required:** party_a, party_b, services (list), amount, period  
- **Optional:** contract_ref (number/date or “без договора”), inn_a, inn_b, no_claims_phrase

### invoice (счёт)

- **Required:** seller, buyer, line_items[{name, qty, price}] (or a single service description + amount)  
- **Derived:** total — compute from line items when possible  
- **Soft-required:** bank_details (rs/bik/bank) — ask once; if user declines → `[укажите …]` only in bank block  
- **Optional:** inn/kpp, payment_due, vat_mode (`with_vat` \| `no_vat` \| `unknown`)

### claim (претензия)

- **Required:** addressee, violation_summary, demand  
- **Soft-required:** reply_deadline — if missing, default «в течение 10 календарных дней с даты получения»  
- **Optional:** contract_ref, debt_amount, attachments_note

### letter (деловое письмо)

- **Required:** addressee, body_purpose  
- **Soft-required:** sender_sign — if missing, ask once or use name from prompt if clearly stated; else `[ФИО / должность]`  
- **Optional:** subject_line, reply_deadline, sender_requisites

**Extraction rules**

- Never invent bank accounts, INN, BIKs, passport data, or third-party FIOs.
- First-person prompts («я ИП…», «мой клиент…») map to party_a / seller / sender without inventing a full legal name if not given — ask if the visible party name is empty.
- Missing required → ask; soft-required → ask once or apply documented default; optional → omit or `[укажите …]` only in that slot.
- Invoice: if `line_items` present and `total` absent → compute total; do not ask for total separately.
- Ambiguous doc kind → one clarify with buttons (Договор / Акт / Счёт / Претензия / Письмо).

## Architecture

### New units

1. `docs/business_types.py` — enums, required/optional field schemas, detect hints.
2. `llm/business_extract.py` — `extract_business_fields(prompt, kind) -> {fields, missing, confidence}`.
3. `edit/business_docx.py` — `render_business_docx(kind, fields, dest) -> Path` with per-kind layouts (tables for invoice/act; clauses for contract; blocks for claim/letter).
4. Handler path in `bot/handlers.py` — business kinds win over academic `generate_document` / `write_form` when detected.

### Clarify state (must not overload unrelated modes)

`PendingClarify` today only stores `prompt` / `options` / `question` — not partial fields. For business v1:

- Add `BusinessDraft` on `UserSession`: `kind`, `fields: dict`, `missing: list[str]`, `base_prompt: str`, `bank_asked: bool` (persist via `session_store` like other pending state).
- Kind picker (buttons) may use `pending_clarify.options` as today.
- Field follow-up uses `BusinessDraft` + `flow=clarifying` — **not** `awaiting_gap_fill` (that remains for “что не заполнено” on uploaded blanks).
- Max ~3 field questions; after that render with placeholders for anything still empty (except refuse to invent bank/INN).

### Integration with existing dialog

- Reuse dialog hygiene from human-understanding (`clear_dialog_modes`, cancel phrases).
- Billing action: `write`. Charge only at render start; refund on failure; do not charge for clarify-only turns.
- Do **not** force “Понял так?” when kind is clear and required fields are complete.
- Keep “Понял так?” only for low-confidence kind detection.
- Existing `write_text` / `write_form` remain for essays and empty blanks; business kinds bypass academic `write_structured_docx` layout.

### Data flow

```
user text
  → classify intent
  → business kind detected? → business_write path (wins over form/text)
  → detect kind (or buttons)
  → extract fields into BusinessDraft
  → missing required/soft? → ask (no billing yet)
  → consume(write) → render_business_docx
  → ingest + answer_document + draft caption
```

## Error handling

| Case | Behavior |
|------|----------|
| Unknown kind | Buttons for 5 types (no charge) |
| Missing bank details on invoice | Ask once; if user says “нет реквизитов” → placeholders only in bank block |
| LLM extract failure | Soft message + offer retry / choose kind (no charge) |
| Render failure | Refund `write` charge; no partial silent file |
| User cancels clarify | Clear draft + flow; idle (no charge) |
| User switches to ask/edit mid-clarify | Clear business draft; handle new intent (same sticky hygiene as human-understanding design) |
| “Сделай договор+счёт+акт” | Make **one** primary kind (prefer first named / contract); say that other docs are separate requests |

## Testing

- Unit: kind detection phrases; required-field missing sets; no invented INN when absent.
- Unit: each renderer produces `.docx` opening with expected headings/table presence.
- Dialog: full prompt → file; partial prompt → one question → file; cancel clarify.
- Regression: “напиши реферат…” still `write_text`; tax-blank requests stay out of business pack (or soft-redirect: not supported as official form).

## Rollout order

1. Schemas + extract + render for **invoice** and **act** (highest structure risk).
2. **contract**, then **claim** / **letter**.
3. Handler routing + clarify loop + caption.
4. UI help text lines for business docs (short).
5. Tests + remove/avoid routing these kinds into academic form mode.

## Risks

- Users expect a full “deal pack” (contract+invoice+act together) — v1 is one file; say so in help if asked.
- Contract legal quality varies — mitigate with conservative clauses + draft caption.
- Old `generate_document` form-mode treating «договор» as blank — business router must win for these five kinds.
- Billing: same `write` action; failures must refund.

## Success metric

In manual checks: for a typical IP prompt with parties + sum + term, bot returns a filled `.docx` of the right kind in ≤1 clarify turn, without inventing bank/INN data.
