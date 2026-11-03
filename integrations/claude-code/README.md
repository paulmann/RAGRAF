# Claude Code: подключение RAGRAF

Клиент: **Claude Code 2.1.316** (`claude --version` на этой машине, 02.11.2026).
Транспорт: **MCP stdio**. Настройки клиента: `~/.claude/settings.json`,
состояние пользователя с MCP-серверами — `~/.claude.json`.

## Подключение

**Плагин (предпочтительно).** В репозитории лежит маркетплейс и сам плагин:

```text
/plugin marketplace add paulmann/RAGRAF
/plugin install ragraf@ragraf
```

Одна команда ставит MCP-сервер, четыре хука, навык и две команды; состав и цена
в контексте видны в `claude plugin details ragraf`. Подробности —
`docs/03_implementation/CLAUDE_PLUGIN.md`. Ниже — ручной способ без плагина.

**Ручная установка.**


Одна команда ставит все четыре части и проверяет их:

```powershell
python tools/install_claude_code.py            # установка + проверка
python tools/install_claude_code.py --check    # только состояние
python tools/install_claude_code.py --remove   # откат (с резервными копиями)
```

Что появляется:

| Часть | Где | Чем полезна |
|---|---|---|
| MCP-сервер `ragraf` | `~/.claude.json` → `mcpServers` (область `user`) | инструменты `mcp__ragraf__*` во всех проектах |
| Хуки | `~/.claude/settings.json` → `hooks` | факты корпуса в начале сеанса, метка задачи, гейт перед файловым поиском, отчёт в конце хода |
| Правила | `~/.claude/CLAUDE.md`, блок между маркерами `== RAGRAF` | агент знает порядок «сначала индекс, потом файлы» |
| Навык | `~/.claude/skills/ragraf-core`, `ragraf-secrets` | подробный справочник операций и работы с секретами |

Руками, без установщика, сервер добавляется так (значения — из
`tools/install_claude_code.py`, функция `mcp_entry`):

```powershell
claude mcp add-json --scope user ragraf '{"type":"stdio","command":"<RAGRAF>\.venv\Scripts\python.exe","args":["<RAGRAF>\tools\ragraf-mcp.py","--agent","claude-code","--agent-kind","external"],"env":{"PYTHONIOENCODING":"utf-8","PYTHONUTF8":"1"}}'
```

Шаблоны для ручной правки: `claude.template.json` (MCP, область `project` —
файл `.mcp.json` в корне проекта) и `settings.template.json` (хуки и права).

## Проверка

```powershell
python tools/install_claude_code.py --check          # 6 проверок, код 0/1
claude mcp get ragraf                                # ожидается Status: √ Connected
claude mcp list                                      # сервер в списке
pwsh -NoProfile -File integrations/claude-code/smoke.ps1   # смоук модели (нужен вход)
```

`--check` проверяет то, что не зависит от модели: `settings.json` разбирается,
все четыре события хуков на месте, право `mcp__ragraf__*` выдано, раздел правил
в `CLAUDE.md` есть, навыки развёрнуты, сервер отвечает на рукопожатие MCP.
Замер 02.11.2026: `claude mcp get ragraf` → `Status: √ Connected` — сервер
поднимается интерпретатором из `.venv` и проходит `initialize` без входа в
учётную запись.

Хуки проверяются прогоном на пробном payload — той же командой, которую
запускает клиент: `SessionStart` обязан вернуть `hookEventName=SessionStart` и
непустой `additionalContext`, `PreToolUse` — решение по протоколу.

## Уровни

| Уровень | Состояние |
|---|---|
| L1 — шаблон конфигурации | `claude.template.json`, `settings.template.json`; разбираются тем же разборщиком, что продукт |
| L2 — смоук | два уровня: `claude mcp get` (без модели) и `smoke.ps1` (с моделью, нужен вход) |
| L3 — проверка в чекере | `python tools/check_integrations.py`, `python tools/install_claude_code.py --check` |
| L4 — границы названы | раздел «Границы» ниже |
| L0 — установка на машине | Claude Code 2.1.316; учётная запись не авторизована — это названо, а не замаскировано |

## Границы

* **Смоук модели требует входа.** Без `/login` клиент отвечает `Not logged in`,
  и `smoke.ps1` возвращает код 3 — замерено. Проверки без модели (MCP-рукопожатие,
  хуки на payload) от входа не зависят и выполняются всегда.
* **Гейт не переписывает вызов.** Протокол разбирает `updatedInput`, но не
  применяет его: подсказка «сначала `search`» только предлагается текстом, а
  отказ блокирует конкретный вызов. Заставить агента переделать шаг нельзя.
* **`Stop` не показывает отчёт модели.** `hookSpecificOutput.additionalContext`
  для этого события протоколом не предусмотрен: строка отчёта видна в режиме
  транскрипта (`Ctrl-R`). Это ограничение клиента, а не RAGRAF.
* **Права выдаёт владелец.** Установщик добавляет только `mcp__ragraf__*`;
  права на правку файлов и запуск команд он не трогает.
* **Секретов в шаблонах нет.** Ключи живут в хранилище RAGRAF; проверка
  `tools/check_integrations.py` ищет значения вида «ключ: значение» и падает
  при находке.
* **Версия клиента проверяется замером.** Поведение хуков и полей настроек
  может отличаться между версиями; установщик печатает версию, а не
  предполагает её.

Стенд для чистых установок — 192.168.31.103 (решение владельца): на рабочей
машине живёт индекс, и ставить туда клиентов «для проверки» нельзя.
