# Business document templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users create filled business `.docx` drafts (договор услуг, акт, счёт, претензия, письмо) via chat: extract fields, ask only for gaps, render typed templates.

**Architecture:** Heuristic kind detection → LLM/heuristic field extract into `BusinessDraft` → clarify loop (no billing) → `consume(write)` → `edit/business_docx.py` render → send file with draft caption. Business path wins over academic `write_form`/`generate_document` for these five kinds.

**Tech Stack:** Python 3, python-docx, existing aiogram handlers, `llm.client` chat JSON helpers, unittest.

**Spec:** `docs/superpowers/specs/2026-10-05-business-docs-templates-design.md`

## Global Constraints

- Output: filled `.docx` draft only; caption always includes «Черновик — проверьте реквизиты и суммы перед отправкой.»
- Never invent INN, BIK, bank account, passport data, or third-party FIO.
- `consume(write)` only at render start; clarify turns are free; refund on render failure.
- Do not use `awaiting_gap_fill` for business field questions (that mode is for uploaded blank fill).
- Do not replace essay `write_text` or empty-blank `write_form` flows.
- No OCR, Excel, tax declarations, or multi-doc packs in this plan.
- Prefer small modules; keep `handlers.py` as thin orchestration.
- Commits: only when the user explicitly asks (do not auto-commit during execution).

## File map

| File | Responsibility |
|------|----------------|
| `docs/business_types.py` | Kinds, required/soft fields, kind detect, missing-field calc, defaults |
| `llm/business_extract.py` | Extract fields JSON for a kind from user text |
| `edit/business_docx.py` | Render each kind to `.docx` |
| `bot/session.py` | `BusinessDraft` + clear on `clear_dialog_modes` |
| `bot/session_store.py` | Persist/restore `business_draft` |
| `bot/handlers.py` | Route, clarify, charge, render, send |
| `bot/ui.py` | Kind buttons + short help lines |
| `llm/client.py` | Stop routing «составь договор/счёт/…» into academic form-only when business kind matches |
| `tests/test_business_docs.py` | New unit/dialog tests |
| `tests/test_docs.py` | Regression: реферат still write_text; contract create still creatable |

---

### Task 1: Kind schemas + detection + missing fields

**Files:**
- Create: `docs/business_types.py`
- Modify: `docs/__init__.py` (export public helpers if useful)
- Test: `tests/test_business_docs.py`

**Interfaces:**
- Produces:
  - `BUSINESS_KINDS = ("contract", "act", "invoice", "claim", "letter")`
  - `detect_business_kind(text: str) -> str | None`  # None if ambiguous/absent
  - `required_fields(kind: str) -> list[str]`
  - `soft_required_fields(kind: str) -> list[str]`
  - `missing_fields(kind: str, fields: dict) -> list[str]`  # required first, then soft not waived
  - `apply_defaults(kind: str, fields: dict) -> dict`  # e.g. claim reply_deadline
  - `KIND_LABELS_RU: dict[str, str]`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_business_docs.py
import unittest
from docs.business_types import (
    detect_business_kind,
    missing_fields,
    apply_defaults,
)

class TestBusinessTypes(unittest.TestCase):
    def test_detect_kinds(self):
        self.assertEqual(detect_business_kind("Составь договор оказания услуг с ООО Ромашка"), "contract")
        self.assertEqual(detect_business_kind("Нужен акт выполненных работ за март"), "act")
        self.assertEqual(detect_business_kind("Выстави счёт на 50000 за разработку"), "invoice")
        self.assertEqual(detect_business_kind("Напиши претензию поставщику о просрочке"), "claim")
        self.assertEqual(detect_business_kind("Напиши деловое письмо клиенту с просьбой уточнить срок"), "letter")

    def test_referat_not_business(self):
        self.assertIsNone(detect_business_kind("Напиши реферат на тему ИИ в медицине"))

    def test_missing_contract_fields(self):
        miss = missing_fields("contract", {"party_a": "ИП Иванов", "subject": "сайт"})
        self.assertIn("party_b", miss)
        self.assertIn("price", miss)
        self.assertIn("term", miss)

    def test_claim_default_deadline(self):
        out = apply_defaults("claim", {"addressee": "ООО X", "violation_summary": "не оплатил", "demand": "оплатить"})
        self.assertIn("10 календарных", out.get("reply_deadline", ""))
```

- [ ] **Step 2: Run tests — expect FAIL** (module missing)

```bash
python -m unittest tests.test_business_docs.TestBusinessTypes -v
```

- [ ] **Step 3: Implement `docs/business_types.py`**

Rules for `detect_business_kind`:
- Prefer explicit nouns with create/need verbs (сделай/составь/нужен/выстави/напиши/оформи…).
- If several kinds appear, pick the **first mentioned** kind keyword in the user string (not a fixed priority list). Helper: `count_business_kinds(text) -> list[str]` in mention order for Task 7 caption.
- «реферат|эссе|доклад|сочинен» → `None` even if «письмо» appears in academic sense only when those words dominate; if «реферат» present → `None`.
- Questions about an existing file («что в договоре», «какие риски») without create/need verb → `None`.
- Soft need without a kind noun («нужен документ для клиента») → `None` (handler shows kind buttons — Task 6).

`missing_fields`:
- Empty/whitespace values count as missing.
- Invoice: if `line_items` or (`service_name` + `amount`) present, do not require `total`.
- Soft fields included in missing unless `fields.get("_waive_<name")` is true (e.g. `_waive_bank_details`).

- [ ] **Step 4: Re-run tests — expect PASS**

- [ ] **Step 5: Export** from `docs/__init__.py` only if other packages need it; otherwise keep imports direct.

---

### Task 2: DOCX renderers (all five kinds)

**Files:**
- Create: `edit/business_docx.py`
- Test: `tests/test_business_docs.py` (new class)

**Interfaces:**
- Consumes: kind + fields dict from Task 1
- Produces: `render_business_docx(kind: str, fields: dict, dest: Path) -> Path`
- Produces: `suggest_business_filename(kind: str, fields: dict) -> str`  # `*.docx`

- [ ] **Step 1: Failing tests**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from docx import Document
from edit.business_docx import render_business_docx

class TestBusinessRender(unittest.TestCase):
    def test_invoice_has_table_and_no_invented_inn(self):
        fields = {
            "seller": "ИП Иванов",
            "buyer": "ООО Ромашка",
            "line_items": [{"name": "Разработка сайта", "qty": 1, "price": 50000}],
            "bank_details": {"bank": "Т-Банк", "bik": "044525974", "rs": "40802810900000000000"},
        }
        with TemporaryDirectory() as td:
            dest = Path(td) / "invoice.docx"
            render_business_docx("invoice", fields, dest)
            doc = Document(str(dest))
            text = "\n".join(p.text for p in doc.paragraphs)
            self.assertIn("Счёт", text)
            self.assertTrue(doc.tables)
            self.assertNotIn("выдуман", text.lower())

    def test_contract_contains_parties_and_price(self):
        fields = {
            "party_a": "ИП Иванов",
            "party_b": "ООО Ромашка",
            "subject": "разработка сайта",
            "price": "80000 руб.",
            "term": "30 календарных дней",
        }
        with TemporaryDirectory() as td:
            dest = Path(td) / "contract.docx"
            render_business_docx("contract", fields, dest)
            doc = Document(str(dest))
            text = "\n".join(p.text for p in doc.paragraphs)
            self.assertIn("ИП Иванов", text)
            self.assertIn("ООО Ромашка", text)
            self.assertIn("80000", text)
```

Add similar smoke tests for `act`, `claim`, `letter` (title/heading present + key field string present).

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement renderers**

Layout rules:
- Font: Times New Roman (match project), but **no** academic first-line indent on invoice/act.
- Invoice/act: table of positions; sum row; requisites block; if bank/INN missing use `[укажите …]` only there.
- Contract: numbered sections (предмет, срок, цена, порядок сдачи, реквизиты) with conservative short clauses; no fake law citations beyond «ГК РФ» generic if needed — prefer no invented article numbers.
- Claim/letter: addressee block, body, demand/closing, signature line.
- Always ensure `dest.suffix == .docx`; create parents.

Compute invoice `total` if absent: `sum(qty * price)`.

- [ ] **Step 4: Run — expect PASS**

---

### Task 3: Field extraction

**Files:**
- Create: `llm/business_extract.py`
- Test: `tests/test_business_docs.py`

**Interfaces:**
- Consumes: `docs.business_types` schemas; reuse `llm.client._chat` + `_extract_json` (there is **no** `_chat_json` helper — same pattern as `generate_document`).
- Produces: `extract_business_fields(prompt: str, kind: str, *, llm_call=None) -> dict` with keys:
  - `fields: dict`
  - `missing: list[str]`
  - `confidence: float`
  - `notes: str` (optional)

- [ ] **Step 1: Failing test for pure merge/heuristic path** (no network)

```python
from llm.business_extract import merge_extracted_fields, sanitize_business_fields

class TestBusinessExtract(unittest.TestCase):
    def test_sanitize_drops_invented_inn_pattern_when_flagged(self):
        # If model returns inn that was not in prompt, strip it
        prompt = "Счёт от ИП Иванов для ООО Ромашка, 10_000 руб за консультацию"
        raw = {"seller": "ИП Иванов", "buyer": "ООО Ромашка", "inn_seller": "7707083893",
               "line_items": [{"name": "консультация", "qty": 1, "price": 10000}]}
        clean = sanitize_business_fields(prompt, "invoice", raw)
        self.assertFalse(clean.get("inn_seller") or clean.get("inn"))

    def test_merge_answers_into_fields(self):
        base = {"seller": "ИП Иванов"}
        merged = merge_extracted_fields(base, {"buyer": "ООО Ромашка", "amount": "50000"})
        self.assertEqual(merged["buyer"], "ООО Ромашка")
```

- [ ] **Step 2: Implement `sanitize_business_fields`**
  - For sensitive keys (`inn*`, `bik`, `rs`, `kpp`, bank account numbers): keep only if the digit string appears in the user prompt (normalize spaces).
  - Map first-person («я», «мы») into seller/party_a only when a real name also appears; else leave empty so missing_fields asks.

- [ ] **Step 3: Implement `extract_business_fields`**
  - Call LLM with strict JSON schema per kind.
  - System prompt: fill only from user text; use null for unknown; never invent requisites.
  - Then `sanitize` → `apply_defaults` → `missing_fields`.
  - On LLM error: return empty fields + all required in `missing` + confidence 0.

- [ ] **Step 4: Optional offline heuristic** for high-signal amounts/parties via regex (qty/price) before LLM to reduce asks — keep simple; tests without network must not call API (guard with env or inject client).

Use dependency injection:

```python
def extract_business_fields(prompt: str, kind: str, *, llm_call=None) -> dict:
    ...
```

Default `llm_call` hits real API; tests pass a stub.

- [ ] **Step 5: Run unit tests — PASS**

---

### Task 4: `BusinessDraft` session + persistence

**Files:**
- Modify: `bot/session.py`
- Modify: `bot/session_store.py`
- Test: `tests/test_business_docs.py`

**Interfaces:**
- Produces:

```python
@dataclass
class BusinessDraft:
    kind: str = ""
    fields: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    base_prompt: str = ""
    bank_asked: bool = False
    questions_asked: int = 0
```

- `UserSession.business_draft: BusinessDraft | None = None`
- `clear_dialog_modes` also sets `business_draft = None`
- `session_to_payload` / `apply_payload` round-trip the draft

- [ ] **Step 1: Failing tests** for clear + payload round-trip

```python
from bot.session import UserSession, BusinessDraft
from bot.session_store import session_to_payload, apply_payload

class TestBusinessDraftSession(unittest.TestCase):
    def test_clear_dialog_modes_clears_draft(self):
        s = UserSession(user_id=1)
        s.business_draft = BusinessDraft(kind="invoice", base_prompt="счёт")
        s.clear_dialog_modes()
        self.assertIsNone(s.business_draft)

    def test_payload_roundtrip(self):
        s = UserSession(user_id=1)
        s.business_draft = BusinessDraft(kind="act", fields={"amount": "1"}, missing=["period"], base_prompt="x")
        payload = session_to_payload(s)
        s2 = UserSession(user_id=1)
        apply_payload(s2, payload)
        self.assertEqual(s2.business_draft.kind, "act")
        self.assertEqual(s2.business_draft.missing, ["period"])
```

- [ ] **Step 2: Implement dataclass + clear + store**

- [ ] **Step 3: Run — PASS**

---

### Task 5: Intent routing — business wins over form

**Files:**
- Modify: `llm/client.py` (`classify_document_intent` / write branch ~1900)
- Test: `tests/test_docs.py` + `tests/test_business_docs.py`

**Interfaces:**
- Produces: create-verb + business kind → intent `business_write` with `doc_kind` / `mode` set to that kind:

```python
{
  "intent": "business_write",
  "confidence": 0.9,
  "mode": kind,  # contract|act|invoice|claim|letter
  "family": "write",
  "doc_kind": kind,
}
```

**Must also update** (or business path never sticks):
- `refine_document_intent_llm` `allowed` set + prompt enum string
- `looks_like_write()` → include `business_write`
- `apply_document_routing_guards` branches that special-case only `write_form`/`write_text` (do not downgrade `business_write` when strong create)
- `bot/handlers.py` intent switches that today only handle `write_form`/`write_text` (~1308, confirm callbacks ~1477)
- Docstring of `classify_document_intent`
- Existing test `test_compose_contract_is_write_or_clarify` → expect `business_write`

- [ ] **Step 1: Failing tests**

```python
# in test_docs or test_business_docs
def test_compose_contract_is_business_write(self):
    d = classify_document_intent("Составь договор оказания услуг с ООО Ромашка на 80 тысяч, срок месяц", False)
    self.assertEqual(d["intent"], "business_write")
    self.assertEqual(d["doc_kind"], "contract")

def test_referat_still_write_text(self):
    d = classify_document_intent("Напиши реферат на тему ИИ", False)
    self.assertEqual(d["intent"], "write_text")
```

Update existing `test_compose_contract_is_write_or_clarify` if it asserted `write_form`.

- [ ] **Step 2: Wire detect_business_kind into classify before write_form special-case for договор/акт**

- [ ] **Step 3: Run docs + business tests — PASS**

---

### Task 6: Handler orchestration (clarify + render + billing)

**Files:**
- Modify: `bot/handlers.py`
- Modify: `bot/ui.py` (kind keyboard + help blurbs)
- Test: `tests/test_business_docs.py` (helpers; mock extract/render where needed)

**Interfaces:**
- Produces:
  - `_handle_business_write(message, session, text, *, kind: str | None, user=None)`
  - `_continue_business_draft(message, session, text)`
  - UI: `business_kind_keyboard()` → five buttons `biz:kind:contract` etc.
  - Callback handler for `biz:kind:*`

**Flow to implement:**

1. On `intent == business_write`: call `_handle_business_write`.
2. `extract_business_fields` (to_thread) → fill `BusinessDraft`.
3. If `missing`:
   - Ask one combined short question for up to 2–3 fields (Russian labels).
   - `session.flow = clarifying`; `questions_asked += 1`.
   - **No** `consume` yet.
4. On next text while `business_draft` and clarifying:
   - If cancel/chitchat → clear draft, idle.
   - If other strong intent (ask/edit/card) → clear draft, re-classify path.
   - Else merge answer into fields (LLM extract on `base_prompt + "\nУточнение: " + text` or `merge_extracted_fields`).
   - If still missing and `questions_asked < 3` → ask again.
   - Else proceed to render: remaining **optional/soft** → omit or `[укажите …]`; remaining **required** after 3 asks → `[укажите …]` placeholders (never invent INN/BIK/rs digits).
5. Render path:
   - `charge = await _require_quota(..., "write")`  # middleware already persists session via `_PersistSessionMiddleware`
   - `render_business_docx` in thread
   - ingest via existing `_ingest_path`
   - `answer_document` with caption `f"{title}\nЧерновик — проверьте реквизиты и суммы перед отправкой."`
   - On error: `_refund(..., "write")`
   - Clear `business_draft`; `remember_op("business_write")`
6. Skip “Понял так?” when `doc_kind` present and confidence ≥ 0.8.
7. Ambiguous kind: create/need phrasing but `detect_business_kind` is `None` (e.g. «нужен документ для клиента») → do **not** call academic `write_form`; send kind buttons (`business_kind_keyboard` / `biz:kind:*`). Callback sets kind and runs extract on stored prompt.

Bank soft-required:
- If invoice missing bank and not `bank_asked`: ask once, set `bank_asked=True`.
- If user says нет/пропусти/без реквизитов: set `_waive_bank_details` and continue.

Helpers live in `docs/business_types.py` (not handlers): `wants_waive_bank(text)`, `field_labels_ru()`, `missing_question(missing: list[str]) -> str`.

- [ ] **Step 1: Unit-test pure helpers** without Telegram

```python
from docs.business_types import wants_waive_bank, missing_question

def test_waive_bank_phrases(self):
    self.assertTrue(wants_waive_bank("реквизитов нет, сделай без них"))
```

- [ ] **Step 2: Implement handler path + UI buttons**

- [ ] **Step 3: Update `docs_prompt_text` / `help_text` in `bot/ui.py`**
  - Add line under Создать: договор · акт · счёт · претензия · письмо (черновик Word).
  - Do **not** advertise FNS declarations as official.

- [ ] **Step 4: Run full unittest**

```bash
python -m unittest tests.test_business_docs tests.test_docs -v
```

Expected: PASS (or only pre-existing failures unrelated — fix regressions you introduce).

---

### Task 7: Multi-doc request + regression polish

**Files:**
- Modify: `docs/business_types.py` (`detect_business_kind` first-mentioned rule — already in Task 1; verify)
- Modify: `bot/handlers.py` (short note when multiple kinds in one prompt)
- Test: `tests/test_business_docs.py`

- [ ] **Step 1: Test**

```python
def test_multi_kind_picks_first(self):
    kind = detect_business_kind("Сделай договор и потом счёт и акт с ООО Ромашка")
    self.assertEqual(kind, "contract")
```

- [ ] **Step 2: After render of multi-mention prompt**, append to caption: «Другие документы из запроса сделайте отдельным сообщением.» only if ≥2 kind keywords detected.

- [ ] **Step 3: Run tests — PASS**

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| Five kinds + fields | 1, 2 |
| No invent INN/bank | 3 sanitize |
| Clarify 1–3 asks, free | 6 |
| Charge only at render | 6 |
| BusinessDraft persist | 4 |
| Wins over form mode | 5 |
| DOCX + draft caption | 2, 6 |
| Help text | 6 |
| One doc / multi request | 7 |
| Refund on failure | 6 |
| Skip Понял так when clear | 6 |
| Essay regression | 5 |

## Placeholder / consistency self-review

- Locked intent name: `business_write` + `doc_kind` (not `write_text`/`mode=business`).
- Locked draft type: `BusinessDraft` on `UserSession` (+ `questions_asked`); cleared by `clear_dialog_modes`; persisted by existing middleware after store fields added.
- Kind conflict rule: **first mentioned** in text (aligned with Task 7 test).
- LLM helper: `_chat` + `_extract_json` only.
- Guards/`looks_like_write`/handler switches must learn `business_write` or routing regresses to empty form blanks.
- Spec rollout order (invoice/act first) is risk guidance; this plan ships all five renderers in Task 2 for one testable slice — acceptable.

---

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-10-05-business-docs-templates.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — same session, executing-plans with checkpoints  

Which approach?
