from llm.client import classify_document_intent as c, looks_like_fill_data, looks_like_card
from bot.session import UserSession, DocTask
from bot import ui
import ast
from pathlib import Path

for f in ["bot/handlers.py", "bot/session.py", "bot/ui.py", "llm/client.py"]:
    ast.parse(Path(f).read_text(encoding="utf-8"))
    print("syntax", f)

assert hasattr(ui, "docs_fill_example_text")
assert "Что не заполнено" in str(ui.docs_inline().inline_keyboard)

cases = [
    ("В файле нужно добавить данные про Чикасова Дениса Владимировича 27.11.1999 Богородицк", True, "edit"),
    ("Нужно добавить эти данные", True, "edit"),
    ("Сделай фио Чикасов Денис Владимирович", True, "edit"),
    ("Что не заполнено в бланке?", True, "check"),
    ("Напиши реферат на тему ИИ", False, "write_text"),
    ("Напиши декларацию для ИП", False, "clarify"),
    ("Сделай титульный лист декларации ИП", False, "write_form"),
    ("Замени Иванова на Петрова", True, "edit"),
    ("Что в договоре про срок?", True, "ask"),
    ("Нужно продать кружку 300 мл", False, "card"),
]
for t, hf, e in cases:
    d = c(t, has_files=hf)
    print(d["intent"], "exp", e, "|", d.get("mode", ""))
    assert d["intent"] == e, (d, e, t)

s = UserSession(user_id=1)
s.remember_doc_task("fill", "Чикасов Денис 27.11.1999 Богородицк", accumulate_facts=True)
merged = s.fill_instruction("Нужно добавить эти данные")
assert "Чикасов" in merged
assert looks_like_fill_data(cases[0][0])
assert not looks_like_card(cases[0][0])
print("ALL OK")
