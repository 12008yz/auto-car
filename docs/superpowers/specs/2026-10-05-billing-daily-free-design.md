# Design: Transparent daily Free + credits billing (pyBot)

**Date:** 2026-10-05  
**Scope:** Free daily attempts, credit top-ups, Pro pack, user-facing `/plans` + `/balance` clarity.  
**Strategy:** Daily free quota first → then credits → clear refusal + pay CTA. Align copy with real `consume` rules.

## Goals

1. Free users get a clear daily taste of the product without burning API budget.
2. Extra usage and Word edits are paid with credits (transparent per-action cost).
3. `/plans` and `/balance` match real billing logic (no “Free без правок” when code allows edits, etc.).
4. Refusal messages state what ran out (daily attempt vs credits) and what to do next.

## Non-goals

- Changing UnitPay / Stars payment rails.
- Turning off `BILLING_OPEN_ACCESS` in this change set (document only: must be off before public sell).
- Redesigning SKU prices in RUB/Stars (keep current SKUs unless noted).
- Multi-currency accounting beyond existing rails.

## Locked product decisions

### Free daily caps (reset calendar day via existing `daily_date` / `_today()`)

| Action | Free / day |
|--------|------------|
| `card` | **1** |
| `ask` + `summary` (shared) | **5** total |
| `write` | **1** |
| `edit` | **0** |

**Shared bucket (locked):** both `ask` and `summary` increment **`daily_ask` only**.  
`FREE_DAILY["ask"] = 5`. `FREE_DAILY["summary"]` is not a separate free cap (keep key only if needed for dict completeness = 0 or unused).  
`refund_consume` / open-access bump for `summary` must also adjust **`daily_ask`**, not `daily_summary`.  
`/balance` shows one line «вопросы used/5» = `daily_ask` (ignore `daily_summary` for display, or stop writing it).

### Credit costs (unchanged)

| Action | Credits |
|--------|---------|
| `ask` | 1 |
| `summary` | 2 |
| `card` | 2 |
| `write` | 3 |
| `edit` | 3 |

### Consume order (must replace current “credits first” behavior)

1. If free daily remaining for this action → charge **daily** (0 credits).  
   - `edit`: always skip (limit 0).  
   - `ask`/`summary`: remaining = `5 - daily_ask`.  
2. Else if credits ≥ cost → charge **credits**.  
3. Else → deny + `/pay`.

**Pro:** purchase grants +200 credits / 30 days. **Same daily Free caps as everyone** — Pro is not unlimited daily. Benefit = credit pool for edits and extra usage. Copy must say this so Pro buyers are not surprised.

### Credit packs / SKUs (keep)

- `pro_month` — Pro 30 days + 200 credits — 299 ₽ / 250 ⭐  
- `credits_20` — 20 credits — 99 ₽ / 80 ⭐  
- `credits_50` — 50 credits — 199 ₽ / 150 ⭐  

## Shared “вопросы” bucket

- Display: «Вопросы сегодня: used/5» (вопросы + содержание).  
- Free slot: **1** from the shared 5 whether `ask` or `summary`.  
- Wallet: `summary` still costs **2** credits when not free.  
- Plans line: «5 вопросов по файлу (включая содержание)».

## User-facing copy

### `/plans` (RU essence)

- **Бесплатно каждый день:** 1 карточка / 5 вопросов по файлу (включая краткое содержание) / 1 создание документа. Правки Word — за кредиты.  
- **Сколько стоят действия:** вопрос 1 · содержание 2 · карточка 2 · создать 3 · правка Word 3.  
- **Pro:** +200 кредитов на 30 дней (дневные бесплатные попытки те же; правки и сверхлимит — с кредитов).  
- **Пакеты / покупки:** список «Купить» — Pro + 20 / 50 кредитов (не путать Pro с пакетом кредитов).  
- Remove false lines: “Free без правок Word”, “правки только на Pro”, “без правок на Free” contradictions.

### `/balance`

```
Тариф: бесплатный|Pro
Кредиты: N
Pro действует до: … (if Pro)

Бесплатно сегодня (использовано / лимит):
карточка used/1 · вопросы used/5 · создание used/1
Правки Word — всегда за кредиты (3 за раз). Сверх дневного лимита — тоже кредиты.
```

If `BILLING_OPEN_ACCESS`: test banner that limits do not block.

### Denial messages

- Daily gone, not enough credits: «На сегодня бесплатная попытка уже использована. Нужно ещё X кредита, сейчас Y. Продолжить: /pay»  
- Daily gone, 0 credits: «На сегодня бесплатная попытка уже использована. Можно продолжить с кредитами или Pro — /pay»  
- Edit, 0 credits: «Чтобы править Word, нужно 3 кредита. Сейчас у вас Y. Пополнить: /pay»

## Code touchpoints

| File | Change |
|------|--------|
| `billing/skus.py` | `FREE_DAILY`: ask=5, card=1, write=1, edit=0; summary not a separate free cap |
| `billing/service.py` | daily-first then credits; ask+summary → `daily_ask`; refund/bump for summary → `daily_ask`; `get_balance` expose shared used/limit |
| `billing/pay.py` | rewrite RU+EN `plans_text` / `balance_text` |
| Tests | new order, shared bucket, edit=0 free, Pro with 0 credits still gets daily free |

## Implementation note

Previously `consume` charged **credits first**, then Free daily; Pro with empty wallet was blocked even with Free daily left. **Fixed:** daily-first, then credits (see `billing/service.py`).

## Migration / compatibility

- Existing wallets keep `credits_balance`.  
- Tightening Free caps does not require DB migration if reusing columns; `daily_edit` stays but free limit 0.  
- Users who relied on old huge Free caps will hit paywalls sooner — intentional.

## Testing

- Free, 0 credits: 1st card OK (daily); 2nd card → need 2 credits / deny.  
- Free: 5 ask/summary combined then 6th needs credits.  
- Free: 1 write free; 2nd write → 3 credits.  
- Free: edit always credits path.  
- With credits: after daily used, card deducts 2.  
- Open access: still never blocks; counters may still bump.  
- Copy tests / snapshot strings for plans/balance contain cost table and new Free lines.

## Success metric

A new user reading `/plans` can answer in one glance: what is free today, what costs credits, how much a Word edit costs — and `/balance` matches what `consume` will do next.

## Risks

- Shared ask/summary: refund/bump must hit `daily_ask` or counters drift.  
- Consume order change vs old credits-first — call out for testers.  
- EN copy must be updated with RU.  
- Daily reset is **UTC** (`_today()`), not Moscow 00:00 — say «сутки» in UI, or later switch to Europe/Moscow if product wants MSK.  
- Pro buyers may expect unlimited daily — mitigate with explicit Pro copy (+кредиты, не безлимит дня).
