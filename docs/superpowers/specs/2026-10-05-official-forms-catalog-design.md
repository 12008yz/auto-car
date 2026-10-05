# Design: Official forms catalog for IP (pyBot)

**Date:** 2026-10-05  
**Scope:** Curated catalog of official Russian government forms useful for ИП; bot returns page link + file when a direct download exists.  
**Entry UX:** «Пример бланка» explains how to ask; natural-language requests match the catalog.

## Goals

1. User asks for an official blank (ФНС and similar IP-relevant) → gets verified official page link.
2. If the catalog entry has a direct file URL on an allowlisted host → bot downloads and sends the file.
3. Clear disclaimer: check currency on the site; not legal advice; not a filled filing.

## Non-goals (v1)

- Live web search / scraping arbitrary results.
- Courts, ГИБДД, Росреестр (deferred).
- Filling official FNS XML / submitting to agencies.
- Replacing business draft templates (договор/акт/счёт) or academic `write_form` title pages.

## Locked decisions

| Decision | Choice |
|----------|--------|
| Discovery | Curated catalog only |
| Delivery | Link always; file when `file_url` present and downloadable |
| Domains | Allowlist: `nalog.gov.ru`, `www.nalog.gov.ru`, and other explicit entries |
| Audience | ИП / small business tax & registration forms |
| UX entry | Update `docs_form_example_text` with how-to + example phrases |

## Catalog entry shape

```text
id, title, aliases[], page_url, file_url|null, source_name, notes
```

Match: normalized user text against `title` + `aliases` (substring / keyword score).  
If several hits → show top 3 as buttons or short list.  
If none → say so and offer business draft / tip to name the form (e.g. «Р21001», «УСН»).

## Flow

1. Intent: phrases like «официальный бланк», «форма ФНС», «скачай с nalog», known form codes → `official_form` path (does not create fake FNS layout via `write_form`).
2. Lookup catalog → reply with title, page link, optional file, disclaimer.
3. Billing: reuse `write` daily/credits **or** free helper with no charge in v1 — prefer **no charge** for link-only; charge `write` only if we generate something. **v1: no consume for catalog lookup** (just forwarding public docs).

## Disclaimer (always)

«Ссылка с официального сайта. Перед подачей проверьте актуальность формы на странице ведомства. Это не юридическая консультация.»
