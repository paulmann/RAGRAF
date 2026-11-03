# Агентские интеграции: что считается поддержкой

Документ описывает часть A плана «Агентские интеграции» (этапы 6–7): одиннадцать
продуктов, уровни поддержки и то, что проверяется машиной, а что остаётся
человеку. Главное правило: **«упомянуто в README» поддержкой не считается**.

## 1. Пять уровней

| Уровень | Что сделано | Чем проверяется |
|---|---|---|
| L0 | продукт установлен на машине-стенде (192.168.31.103) | `--version` клиента; на рабочей машине не ставим |
| L1 | шаблон конфигурации MCP в `integrations/<slug>/` | `tools/check_integrations.py`: разбор шаблона |
| L2 | смоук: клиент вызывает `ragraf_ensure` и `ragraf_search`, в ответе есть `path:line` | `integrations/<slug>/smoke.ps1`, коды возврата 0/1/2/3 |
| L3 | автоматическая проверка в чекере | `tools/Invoke-ProjectDoctrine.ps1`, 46-я проверка |
| L4 | границы названы: что не работает и почему | раздел «Границы» в README интеграции |

L0 — **условие** проверки, а не поддержка: установленный клиент без конфигурации
и смоука ничего не даёт. Требование к сдаче интеграции — L1..L4.

## 2. Матрица продуктов

| Продукт | `slug` | Уровни | Транспорт | Конфигурация | L2: прогон на этой машине |
|---|---|---|---|---|---|
| OpenClaw | `openclaw` | L1, L2, L3, L4 | MCP stdio | `~/.openclaw/openclaw.json` | код 3: шлюзу нужны учётные данные |
| Hermes Agent (Nous) | `hermes` | L1, L2, L3, L4 | MCP stdio | `~/.config/hermes/config.yaml` | не запускался: ждёт интерактивного ввода |
| Claude Code | `claude-code` | L1, L2, L3, L4 | MCP stdio | `~/.claude.json` (область `user`), для проекта — `.mcp.json` | код 3: `Not logged in · Please run /login`; проверки без модели проходят |
| OpenAI Codex CLI | `codex-cli` | L1, L2, L3, L4 | MCP stdio | `~/.codex/config.toml` | код 3: каталог не доверенный, ключа нет |
| OpenCode | `opencode` | L1, L2, L3, L4 | MCP stdio | `opencode.json` | не запускался: ждёт интерактивного ввода |
| Cline (VS Code) | `cline` | L1, L3, L4 | MCP stdio | `cline_mcp_settings.json` | смоука нет: GUI, вместо него чек-лист |
| Pi | `pi` | L1, L2, L3, L4 | MCP stdio | `~/.pi/config.json` | не запускался: ждёт интерактивного ввода |
| DeepSeek Harness | `deepseek-harness` | L1, L3, L4 | MCP stdio | `cordis.patch.yml` | смоука нет: GUI, чек-лист |
| AutoClaw | `autoclaw` | L1, L3, L4 | MCP stdio + HTTP | Settings → Tools & connections | смоука нет: GUI, чек-лист |
| Cursor | `cursor` | L1, L3, L4 | MCP stdio | `.cursor/mcp.json` | смоука нет: GUI, чек-лист |
| Manus | `manus` | L1, L3, L4 | MCP по HTTP | нет локальной конфигурации | смоука нет: облачный клиент, локальной установки нет |

Таблица — **источник истины о уровнях**: `tools/check_integrations.py` сверяет её
с `integrations/index.json` и падает, если набор продуктов или уровней разошёлся.

## 3. Что измерено, а что нет

**Измерено на этой машине (02.11.2026):** установлены `openclaw` 2026.10.5,
`claude` 2.1.316, `codex-cli` 0.166.0, `opencode` 1.19.5, `pi` 1.4.0; Cline
4.1.26 в VS Code; AutoClaw 1.20.0. Это L0 только для перечисленных.

**L2 не измерен ни у одного продукта, и это названо, а не замаскировано:**

| Продукт | Что ответил клиент | Код смоука |
|---|---|---|
| Claude Code | `Not logged in · Please run /login` | 3 |
| OpenClaw | `gateway agent requires credentials before opening a websocket` | 3 |
| OpenAI Codex CLI | `Not inside a trusted directory and --skip-git-repo-check was not specified` | 3 |
| Hermes, OpenCode, Pi | клиент ждёт интерактивного ввода: headless-вызов в этой среде не завершается | не запускался |

Код 3 означает «клиент не готов» (нет входа, ключа или доверия к каталогу), а не
«интеграция сломана». Полный L2 требует авторизованного клиента — это работа на
стенде 192.168.31.103 по решению владельца, а не на машине с живым индексом.

## 4. Как проверить самому

```powershell
python tools/check_integrations.py            # все проверки, код 1 при находках
python tools/check_integrations.py --templates # только шаблоны и README
pwsh -NoProfile -File integrations/openclaw/smoke.ps1   # L2 одного продукта
```

Коды смоука: 0 — ссылка `path:line` получена; 1 — клиент ответил, но адреса нет;
2 — клиент не найден; 3 — клиент не готов (нет входа, ключа или доверия).

## 5. Границы части A

* **GUI не автоматизируется.** Cline, DeepSeek Harness, AutoClaw, Cursor получают
  чек-лист из пяти шагов; каждый шаг наблюдаем человеком. Это L4, а не L2.
* **Секретов в шаблонах нет.** Ключи провайдеров и токены доступа живут в
  окружении или хранилище RAGRAF; проверка ищет значения вида «ключ: значение» и
  падает при находке.
* **Manus — облачный клиент.** Локального конфигурационного файла нет; шаблон
  описывает HTTP-транспорт, а граница названа прямо: облачный клиент не получает
  локальный корпус без решения владельца.
* **Стенд отдельный.** Чистые установки — только на 192.168.31.103: на рабочей
  машине живёт индекс, и установка клиентов «для проверки» ломает замеры.
* **Смоук — не тест модели.** Он проверяет, что клиент вызвал инструмент и привёл
  адрес; качество ответа модели — вне области RAGRAF.

## 6. Где что лежит

| Что | Где |
|---|---|
| объявление продуктов и уровней | `integrations/index.json` |
| каталоги продуктов | `integrations/<slug>/` (шаблон, README, смоук либо чек-лист) |
| правила объявления (дубли слагов, уровни, связка «L2 → смоук») | `tools/check_integrations.py:112` (`check_index`) |
| разбор шаблона своим разборщиком | `tools/check_integrations.py:64` (`parse_template`) |
| поиск секретов в шаблоне | `tools/check_integrations.py:97` (`scan_secrets`) |
| сверка таблицы уровней с документом | `tools/check_integrations.py:246` (`check_doc`) |
| прогон проверок | `tools/check_integrations.py:262` (`run`) |
| проверка в чекере, функция `Test-AgentIntegrations` | `tools/Invoke-ProjectDoctrine.ps1:4513` |
| свойство | `tests/test_agent_integrations.py:70` (`main`) |
| подсадки | `stage-docgraph/mutations6.py:37` (`MUTATIONS`) |
