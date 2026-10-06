# IP on NPD guide (Alfa → Tochka) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clear step-by-step Telegram wizard that guides users to register IP on NPD via Alfa, then open Tochka account/acquiring, with honest tax copy.

**Architecture:** Pure copy + callback wizard (`ipnpd:*`). Step texts and URLs live in `docs/ip_npd_guide.py` + `config.py`; `bot/ui.py` keyboards; `bot/handlers.py` routes. No billing charge. No document filing by the bot.

**Tech Stack:** Python, aiogram callbacks, existing `bot/ui.py` / `bot/handlers.py` patterns.

## Global Constraints

- Alfa = registration IP+NPD; Tochka = settlement account / acquiring.
- Never claim «вообще бесплатно» or «банк примет всех».
- Say: online registration without state fee; tax 4%/6% on income.
- Warn Alfa may offer its own account; Tochka is the recommended next step.
- Partner URLs from `config` / `.env`, not hardcoded mega-UTM in UI strings.
- Disclaimer: helper, not legal advice.

---

### Task 1: Config + guide module + tests

**Files:**
- Create: `docs/ip_npd_guide.py`
- Modify: `config.py`
- Create: `tests/test_ip_npd_guide.py`

- [ ] Step 1: Write failing tests for step texts (Alfa, Tochka, 4%/6%, disclaimer) and URL helpers
- [ ] Step 2: Add `ALFA_IP_NPD_URL`, `TOCHKA_RKO_URL` to `config.py` with safe defaults
- [ ] Step 3: Implement `docs/ip_npd_guide.py` (steps 0–3, DIY blurb, keyboards data)
- [ ] Step 4: Run tests until green

### Task 2: UI + handlers wiring

**Files:**
- Modify: `bot/ui.py`
- Modify: `bot/handlers.py`
- Modify: `bot/ui.py` help / home entry
- Modify: `tests/test_ip_npd_guide.py` (UI entry asserts)

- [ ] Step 1: Add home/docs button «Открыть ИП на НПД» + wizard keyboards
- [ ] Step 2: Handle `menu:ip_npd` and `ipnpd:*` callbacks
- [ ] Step 3: Mention in `/help`
- [ ] Step 4: Run related tests; smoke classify not required

### Task 3: Spec status + bot restart

- [ ] Mark design spec status approved/implemented
- [ ] Restart bot process
