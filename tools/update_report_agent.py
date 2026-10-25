"""Обновление отчёта RAGRAF: номера ревизий и новый корпус sagaai.

Что делается:
  1. в таблице корпусов номер z.batteryservice.ru приводится к фактическому
     (rev-0150 → rev-0155): номер вырос из-за переиндексации сторожем,
     числа не изменились;
  2. добавляется строка корпуса sagaai;
  3. добавляется раздел о том, как учитывается агент в статистике.

Почему это нужно. Тест `test_report_facts.py` сверяет отчёт с индексом
и верно поймал расхождение: отчёт описывал прежнее состояние. Отчёт должен
соответствовать фактам, иначе он вводит в заблуждение.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "docs" / "RAGRAF-REPORT.md"
text = TARGET.read_text(encoding="utf-8")
print(f"  отчёт до правки: {len(text.splitlines())} строк")

changes: list[str] = []

# --- 1. Номер ревизии z: только номер, числа те же ---
if "| z.batteryservice.ru | rev-0150 |" in text:
    text = text.replace(
        "| z.batteryservice.ru | rev-0150 |",
        "| z.batteryservice.ru | rev-0155 |",
    )
    changes.append("z: rev-0150 → rev-0155")
else:
    # Номер мог отличаться: ищется любая строка корпуса z.
    import re
    match = re.search(r"\| z\.batteryservice\.ru \| (rev-\d+) \|", text)
    if match:
        text = text.replace(
            f"| z.batteryservice.ru | {match.group(1)} |",
            "| z.batteryservice.ru | rev-0155 |",
        )
        changes.append(f"z: {match.group(1)} → rev-0155")

# --- 2. Строка корпуса sagaai после строки RdpMON или в конце таблицы ---
if "| sagaai |" not in text:
    # Ищется конец таблицы корпусов: строка с Clean_BOM_Senior или RdpMON.
    anchors = [
        "| Clean_BOM_Senior | rev-0001 |",
        "| RdpMON | rev-0006 |",
    ]
    inserted = False
    for anchor in anchors:
        if anchor in text:
            # Находится конец строки с якорем.
            idx = text.find(anchor)
            end = text.find("\n", idx)
            row = ("\n| sagaai | rev-0008 | 378 | 5751 | 34 779 | "
                   "python 271, markdown 74, json 29, ini 1, text 1, "
                   "xml 1 | Python/Streamlit |")
            text = text[:end] + row + text[end:]
            changes.append("добавлена строка корпуса sagaai")
            inserted = True
            break
    if not inserted:
        changes.append("СТРОКА sagaai НЕ ДОБАВЛЕНА: якорь не найден")

# --- 3. Раздел об учёте агента ---
MARKER = "## Учёт агента в статистике"
if MARKER not in text:
    section = """
## Учёт агента в статистике

Статистика использования разделена на две независимые оси. Раньше они были
смешаны в одном поле, и отфильтровать данные по агенту было невозможно.

| Ось | Поле | Значения | Отвечает на вопрос |
|---|---|---|---|
| Канал | `tool` | mcp, cli, admin, dev, selftest | откуда пришёл вызов |
| Агент | `agent` | AutoClaw, SagaAI, MD.Viewer, RAGRAF-Admin | кто именно вызвал |
| Семейство | `agent_kind` | autoclaw, external, internal | свои или сторонние |

### Почему это понадобилось

Замер до правки: в корпусе z.batteryservice.ru поле `tool` принимало
значения admin/cli/dev/selftest/unknown, и **113 вызовов из 138** числились
как `unknown`. Причина: MCP-сервер создавал фасад, не сообщая о себе,
а клиент `clientInfo` в запросе `initialize` не читался.

После правки имя агента берётся из двух источников:
  1. параметр запуска `--agent` (приоритетный: задаёт владелец);
  2. поле `clientInfo.name` запроса `initialize` (по протоколу MCP
     клиент обязан себя назвать).

Для консольного вызова имя передаётся переменными окружения
`RAGRAF_AGENT` и `RAGRAF_AGENT_KIND` — это единственный способ узнать
агента, не меняя синтаксис командной строки.

### Три режима статистики

```
ragraf -p <конфиг> usage                   # общая по всем агентам
ragraf -p <конфиг> usage --agent AutoClaw  # только этот агент
ragraf -p <конфиг> usage --agent-kind autoclaw  # все агенты AutoClaw
```

В ответе есть разбивка `by_agent` (по каждому агенту) и `by_agent_kind`
(по семействам). Сессии и вызовы считаются раздельно: один процесс MCP
может обслужить несколько задач, поэтому числа могут расходиться.

### Старые записи не потеряны

Добавление полей идёт догоняющей миграцией: прежние журналы не требуют
удаления, а записи без агента видны как `unknown`. Проверено на живом
журнале: 190 сессий и 138 вызовов сохранены полностью.

Особенность: фильтр `--agent unknown` ищет не строку `unknown`, а записи
с пустым полем (NULL). Без этого фильтр возвращал 0 вызовов, хотя в разбивке
их было 138 — владелец видел строку и, выбрав её, получал пусто.

### Кто представлен в панели администратора

Панель обращается к RAGRAF ради чтения состояния и статистики. Её
собственные обращения помечены агентом `RAGRAF-Admin` с семейством
`internal`, поэтому они не смешиваются с работой агентов.

"""
    # Раздел вставляется перед разделом 9 (по прежней нумерации отчёта).
    anchors = ["\n## 9.", "\n## 10.", "\n## 11."]
    inserted = False
    for anchor in anchors:
        if anchor in text:
            text = text.replace(anchor, section + anchor, 1)
            changes.append(f"добавлен раздел об учёте агента (перед {anchor.strip()})")
            inserted = True
            break
    if not inserted:
        # Если нумерованных разделов нет — раздел идёт в конец.
        text = text.rstrip() + "\n" + section
        changes.append("добавлен раздел об учёте агента (в конец)")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print(f"  отчёт после правки: {len(after.splitlines())} строк")
print()
print("  изменения:")
for c in changes:
    print(f"    - {c}")

print()
print("  проверки:")
checks = [
    ("rev-0155" in after, "номер ревизии z обновлён"),
    ("rev-0150" not in after, "старого номера не осталось"),
    ("| sagaai |" in after, "корпус sagaai в таблице"),
    (MARKER in after, "раздел об учёте агента добавлен"),
    (after.count(MARKER) == 1, "раздел ровно один"),
    ("by_agent" in after, "описана разбивка по агентам"),
    ("RAGRAF-Admin" in after, "описана панель администратора"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")
