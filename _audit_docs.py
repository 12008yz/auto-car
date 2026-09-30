from llm.client import classify_document_intent as c, looks_like_tone
from bot import ui
import ast
from pathlib import Path

for f in ["bot/handlers.py", "bot/ui.py", "llm/client.py", "bot/session.py"]:
    ast.parse(Path(f).read_text(encoding="utf-8"))
    print("syntax", f)

assert hasattr(ui, "docs_more_examples_text")
assert any("menu:risks" in str(row) for row in ui.docs_inline().inline_keyboard)

cases = [
    ("Проверь договор на риски", True, "risks"),
    ("Вытащи стороны, даты и суммы", True, "extract"),
    ("Сравни эти два файла", True, "compare"),
    ("Оформи по образцу", True, "format"),
    ("Сделай текст официальнее", True, "edit"),
    ("Что не заполнено?", True, "check"),
    ("Напиши претензию поставщику о просрочке", False, "write_text"),
    ("Напиши коммерческое предложение на сайт", False, "write_text"),
    ("Сделай титульный лист декларации ИП", False, "write_form"),
    ("Нужно продать кружку 300 мл", False, "card"),
]
for t, hf, e in cases:
    d = c(t, has_files=hf)
    print(d["intent"], e, d.get("mode"))
    assert d["intent"] == e, (d, e, t)

assert looks_like_tone("Сделай текст официальнее")
assert c("Сделай текст официальнее", has_files=True).get("mode") == "tone"
print("ALL OK")
