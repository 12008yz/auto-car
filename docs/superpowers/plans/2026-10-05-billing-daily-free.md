# Billing daily Free + credits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Daily-free-first billing with transparent Free caps and credit costs; honest `/plans` + `/balance`.

**Architecture:** Update `FREE_DAILY` → rewrite `consume` (daily then credits; ask+summary share `daily_ask`) → rewrite pay copy → tests.

**Tech Stack:** Python, SQLite wallets, unittest.

**Spec:** `docs/superpowers/specs/2026-10-05-billing-daily-free-design.md`

## Global Constraints

- Free/day: card 1, ask+summary 5 shared, write 1, edit 0.
- Costs: ask 1, summary 2, card 2, write 3, edit 3.
- Daily first, then credits. UTC day OK.
- No auto-commit unless user asks.
- Keep `BILLING_OPEN_ACCESS` behavior (no block).

---

### Task 1: FREE_DAILY + consume + refund shared bucket

**Files:** `billing/skus.py`, `billing/service.py`, `tests/test_billing_daily.py` (new)

- [ ] Caps + daily-first consume + summary bumps/refunds `daily_ask`
- [ ] Tests: free card×2, ask+summary shared 5, edit needs credits, daily then credits

### Task 2: plans_text + balance_text

**Files:** `billing/pay.py`, tests for copy snippets

- [ ] Honest RU/EN copy; balance shows card/questions/write; Pro same daily Free

### Task 3: Fix existing billing tests for daily-first

**Files:** `tests/test_clear_billing.py`

- [ ] User with credits: ask uses daily first (credits unchanged) unless daily exhausted
