"""Настоящая причина падения теста usability_fixes.

МОЯ ДИАГНОСТИКА БЫЛА НЕВЕРНОЙ. Я решил, что в файле два дубля блока
отправки, но строка 1379 — это ШТАТНЫЙ код обработки отложенной задачи:

    _pending_task = _pop_ss(slug, "pending_task")
    if _pending_task and _ss(slug, "loop_state") is None:
        _set_ss(slug, "user_message", _pending_task)     # строка 1379
        _do_step(slug=slug, lang=lang)

Это не дубль: другой контекст, другое условие.

ЗНАЧИТ причину падения надо искать заново. Падение воспроизводится так:
    * тест без моей правки: PASSED
    * тест с моей правкой:  FAILED (KeyError: 'orch_o1_user_message')

Проверяется пошагово: что делает тест и где поток прерывается.
Тест подменяет `chat_input` и ждёт, что `_render_chat_tab` запишет
`user_message` в состояние сессии. Значит при моей правке блок отправки
НЕ исполняется. Надо найти, почему.

Проверяется:
  1. какой именно код тест исполняет (мокается ли st);
  2. доходит ли управление до блока `elif`;
  3. не подменяет ли тест что-то из того, что я использую.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TEST = SAGA / "tests" / "test_usability_fixes.py"
ORCH = SAGA / "ui" / "pages" / "orchestrator.py"

print("=" * 78)
print("1. КАК ТЕСТ ГОТОВИТ ОКРУЖЕНИЕ")
print("=" * 78)
test_text = TEST.read_text(encoding="utf-8", errors="replace")
lines = test_text.splitlines()

# Ищется фикстура ui_env и помощник _orch_chat_env.
for name in ("def ui_env", "def _orch_chat_env", "def _rerender"):
    for i, line in enumerate(lines, 1):
        if line.startswith(name):
            print(f"\n  === {name} (строка {i}) ===")
            end = next((j for j in range(i, len(lines))
                        if lines[j].startswith("def ")), len(lines))
            for j in range(i - 1, min(end, i + 40)):
                print(f"    {j + 1:>5}: {lines[j][:94]}")
            break

print()
print("=" * 78)
print("2. ЧТО ТЕСТ ПОДМЕНЯЕТ В ST")
print("=" * 78)
# Ищутся monkeypatch.setattr для streamlit.
for i, line in enumerate(lines, 1):
    if "monkeypatch" in line or "setattr" in line:
        print(f"    {i:>5}: {line.strip()[:92]}")

print()
print("=" * 78)
print("3. ЧТО МОЙ КОД ТРЕБУЕТ ОТ ОКРУЖЕНИЯ")
print("=" * 78)
# Мой перехват использует _parse_slash_command — проверяется, что он
# не зависит от того, чего нет в моке.
orch_text = ORCH.read_text(encoding="utf-8", errors="replace")
fn_start = orch_text.find("def _parse_slash_command")
fn_end = orch_text.find("\ndef ", fn_start + 10)
parse_src = orch_text[fn_start:fn_end]
print("  _parse_slash_command использует:")
for i, line in enumerate(parse_src.splitlines(), 1):
    stripped = line.strip()
    if "st." in stripped or "st(" in stripped:
        print(f"    {i:>3}: {stripped[:88]}")
print("  (если строк нет — функция не зависит от streamlit)")

print()
print("=" * 78)
print("4. ГДЕ СТОИТ МОЙ КОД ОТНОСИТЕЛЬНО ПРОВЕРОК ТЕСТА")
print("=" * 78)
# Проверяется: доходит ли поток до моего перехвата.
# Тест мокает st.chat_input так, что он возвращает "go" ВСЕГДА.
print("  Тест задаёт: ui_env.chat_input = lambda *a, **k: 'go'")
print("  Значит user_input = 'go' на каждой отрисовке.")
print()
# Ключевая проверка: не считает ли _parse_slash_command("go") командой.
probe = SAGA / "_probe_parse.py"
probe.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "import streamlit as st\n"
    "from ui.pages import orchestrator as orch\n"
    "for value in ['go', '/ragraf', '/RAGRAF', '/help', 'обычный текст']:\n"
    "    result = orch._parse_slash_command(value)\n"
    "    print(f'{value!r}|{result}')\n",
    encoding="utf-8", newline="\n",
)
import subprocess  # noqa: E402

proc = subprocess.run([str(SAGA / ".venv" / "Scripts" / "python.exe"),
                       str(probe)],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", cwd=str(SAGA), timeout=300)
print("  разбор ввода 'go':")
for line in (proc.stdout or "").splitlines():
    print(f"    {line}")
if proc.stderr.strip():
    print(f"  stderr: {proc.stderr.strip()[:300]}")
probe.unlink(missing_ok=True)
