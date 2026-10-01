# UX checklist: ready for any user message

Based on conversation-design practice (Google confirmations, soft landings, context memory).

## Always

1. **Interpret** — say how the bot understood the request (`Понял так: …`) before acting.
2. **Never dead-end** — if unsure or empty search, show soft-landing buttons (ask / fill / edit / send file / menu).
3. **Keep short-term memory** — pending edit, gap fields, facts, last_op.
4. **Confirm costly actions** — fill / rewrite / tone → draft + Apply; transforms → file immediately.
5. **Greetings** — friendly + menu, never “send a file first”.

## Soft landing (implemented)

- No files → buttons: send file / create doc / docs / card
- Has files → buttons: answer / fill / edit / gaps / docs menu
- Empty RAG hit or “not found in fragments” → soft landing with fill/edit options

## Not “any question” literally

The bot cannot answer arbitrary world knowledge without files; it must route to:

- document Q&A / edit / create, or
- product card, or
- soft landing with clear next steps

That is the professional behavior for this product.
