"""Дополнение промпта агента: учёт агента в статистике RAGRAF.

Зачем. Промпт `C:\\Users\\<пользователь>\\.pi\\agent\\AGENTS.md` описывает
работу с RAGRAF. Владелец ввёл новую возможность — статистику по агентам,
и агент должен об этом знать: чтобы передавать своё имя и уметь читать
разбивку.

Что добавляется:
  1. разделение «канал» и «агент»;
  2. как агент сообщает своё имя;
  3. как читать статистику по себе и по всем агентам AutoClaw;
  4. особенность фильтра `unknown`.

Файл правится скриптом: он вне рабочего каталога RAGRAF, и файловые
инструменты к нему не применяются.
"""

from __future__ import annotations

import pathlib
import shutil

TARGET = pathlib.Path(
    str(pathlib.Path.home() / ".pi/agent/AGENTS.md")
)
print(f"  файл: {TARGET}")
print(f"  существует: {TARGET.exists()}")
if not TARGET.exists():
    raise SystemExit("файл промпта не найден")

text = TARGET.read_text(encoding="utf-8")
before_lines = len(text.splitlines())
print(f"  строк до правки: {before_lines}")

# Резервная копия.
backup = TARGET.with_suffix(".md.bak-agentstats")
shutil.copy2(TARGET, backup)
print(f"  копия: {backup.name}")

MARKER = "### 8.18 Учёт агента в статистике"
if MARKER in text:
    print("  раздел уже есть — правка не требуется")
    raise SystemExit(0)

SECTION = """
### 8.18 Учёт агента в статистике

Статистика использования RAGRAF разделена на две независимые оси. Раньше
они были смешаны в одном поле, и отфильтровать данные по агенту было нельзя.

| Ось | Поле в журнале | Примеры значений | Отвечает на вопрос |
|---|---|---|---|
| Канал | `tool` | mcp, cli, admin, dev, selftest | откуда пришёл вызов |
| **Агент** | `agent` | AutoClaw, SagaAI, MD.Viewer, RAGRAF-Admin | **кто именно вызвал** |
| **Семейство** | `agent_kind` | autoclaw, external, internal | свои или сторонние |

**Замер, из-за которого это сделано.** В корпусе z.batteryservice.ru поле
`tool` принимало значения admin/cli/dev/selftest/unknown, и 113 вызовов
из 138 числились как `unknown`. Причина: MCP-сервер создавал фасад, не
сообщая о себе, а поле `clientInfo` запроса `initialize` не читалось.

#### Как агент сообщает своё имя

Три пути, в порядке приоритета:

1. **Параметр запуска** MCP-сервера: `--agent AutoClaw --agent-kind autoclaw`.
   Приоритетный: задаётся владельцем явно и не зависит от клиента.
2. **Поле `clientInfo.name`** запроса `initialize`. По протоколу MCP клиент
   обязан себя назвать; сервер читает это поле.
3. **Переменные окружения** для консольного вызова: `RAGRAF_AGENT` и
   `RAGRAF_AGENT_KIND`. Нужны, когда RAGRAF запускается из скрипта или
   кастомной функции — у команды нет параметра «кто меня вызвал».

Если имя не задано ни одним способом, вызов учитывается как `unknown`.
Это не ошибка, но владелец в отчёте увидит «неизвестный агент» — поэтому
своё имя лучше передавать.

Семейство (`agent_kind`) выводится из имени, если не задано явно:
имя, содержащее `autoclaw`, считается семейством `autoclaw`, всё прочее —
`external`. Имя `RAGRAF-Admin` используется самим RAGRAF для собственной
панели и имеет семейство `internal`.

#### Как читать статистику

```bash
# общая по всем агентам
python tools/ragraf.py -p <конфиг> usage

# только конкретный агент
python tools/ragraf.py -p <конфиг> usage --agent AutoClaw

# все агенты AutoClaw вместе (свои), без сторонних
python tools/ragraf.py -p <конфиг> usage --agent-kind autoclaw

# только сторонние агенты
python tools/ragraf.py -p <конфиг> usage --agent-kind external
```

В ответе две разбивки:

* `by_agent` — по каждому агенту: сессии, вызовы, факты, объём, ошибки;
* `by_agent_kind` — по семействам: сколько агентов, сессий, вызовов.

Сессии и вызовы считаются **раздельно**: один процесс MCP может обслужить
несколько задач, поэтому числа могут расходиться. Сессия — это запуск
процесса, вызов — обращение к операции.

Через MCP: инструмент `ragraf_usage` принимает те же фильтры
(`agent`, `agent_kind`).

Через админ-панель: разбивка по агентам показана на вкладке статистики
таблицей, включая столбец «строк не читалось» — оценку экономии контекста
по каждому агенту.

#### Особенность фильтра `unknown`

Записи, сделанные до появления поля, хранят не строку `unknown`, а пустое
значение (NULL). Фильтр `--agent unknown` учитывает это и находит их, но
если писать запрос к базе напрямую, нужно проверять
`agent IS NULL OR agent = ''`.

Это не теоретическая деталь: без такой обработки фильтр `unknown` возвращал
0 вызовов, хотя в разбивке их было 138 — владелец видел строку, выбирал её
и получал пусто.

#### Старые записи не теряются

Поля добавляются догоняющей миграцией: прежние журналы не требуют удаления.
Проверено на живом журнале корпуса z: 190 сессий и 138 вызовов сохранены
полностью, новые записи видны рядом со старыми.

"""

# Раздел вставляется после 8.17 (предыдущий по нумерации).
anchor_candidates = ["\n## 9.", "\n## 10.", "\n## 11.", "\n## Раздел 9"]
inserted = False
for anchor in anchor_candidates:
    if anchor in text:
        text = text.replace(anchor, SECTION + anchor, 1)
        print(f"  вставлено перед {anchor.strip()}")
        inserted = True
        break

if not inserted:
    # Ищется последняя подсекция 8.x.
    import re

    matches = list(re.finditer(r"\n### 8\.(\d+)[^\n]*\n", text))
    if matches:
        last = matches[-1]
        # Вставляется после конца последней подсекции 8.x — перед
        # следующим заголовком уровня ##.
        tail = text[last.end():]
        next_h2 = tail.find("\n## ")
        if next_h2 >= 0:
            pos = last.end() + next_h2
            text = text[:pos] + SECTION + text[pos:]
            print(f"  вставлено после подсекции 8.{matches[-1].group(1)}")
            inserted = True
        else:
            text = text.rstrip() + "\n" + SECTION
            print("  вставлено в конец (следующего раздела нет)")
            inserted = True

if not inserted:
    text = text.rstrip() + "\n" + SECTION
    print("  вставлено в конец")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print(f"  строк после правки: {len(after.splitlines())}")

print()
print("  проверки:")
checks = [
    (MARKER in after, "раздел добавлен"),
    (after.count(MARKER) == 1, "раздел ровно один"),
    ("by_agent" in after, "описана разбивка по агентам"),
    ("--agent-kind autoclaw" in after, "приведён фильтр по семейству"),
    ("RAGRAF_AGENT" in after, "описан путь через окружение"),
    ("clientInfo" in after, "описан путь через протокол MCP"),
    ("agent IS NULL" in after, "описана особенность значений NULL"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")
