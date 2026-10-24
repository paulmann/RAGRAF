---
name: ragraf-secrets
description: "Хранилище секретов RAGRAF: пароли, токены, ключи API, закрытые ключи, сертификаты, строки подключения, SSH-ключи, вебхуки. Загружать, когда в задаче встречается пароль или ключ, когда нужен доступ к сохранённому секрету, когда надо выдать или ограничить доступ конкретному агенту, когда владелец спрашивает «где лежит пароль», просит сохранить/сменить/удалить секрет, посмотреть список сохранённого с датами и авторами, или когда хранилище отказывает (no_key, disabled, not_found при известном имени)."
---

# RAGRAF: хранилище секретов

Значения лежат **только** в `C:\AutoClaw\RAGRAF\catalog\` (вне корпуса),
**только** шифротекстом и выдаются **только** по праву с записью в аудит.
Правило «не индексировать» не менялось: значение не попадает ни в индекс, ни в
поиск, ни в карты.

## Когда загружать

- В запросе или файле встретился секрет (пароль, токен, ключ, сертификат, DSN).
- Нужно **получить** значение для работы: деплой, подключение к базе, проверка API.
- Нужно **сохранить** новое значение или **сменить** старое.
- Нужно **выдать/забрать** доступ: «пусть этот пароль видит только агент X».
- Владелец спрашивает «что сохранено», «кто добавил», «когда», «кому доступно».
- Хранилище отвечает отказом: `no_key`, `disabled`, `not_found`, `reason_required`.

## Главное правило поведения

> Секрет, встреченный в запросе или файле, **не печатать** в ответ и в чат.
> Подтверждать именем, типом и маской.
>
> **Сохранять** встреченное — только если включена настройка
> `secrets_capture_enabled`. Она **выключена по умолчанию**: сохранение чужого
> пароля без спроса — действие, а не наблюдение. При выключенной настройке
> спросить владельца, а не сохранять «на всякий случай».

Проверить действующие правила одной командой:

```powershell
python tools/ragraf.py secrets policy
```

Ответ содержит `enabled`, `capture`, `default_visibility`,
`reveal_requires_reason`, список типов и поле `instruction` — готовая
формулировка для агента.

## Операции (CLI)

```powershell
cd C:\AutoClaw\RAGRAF
python tools/ragraf.py secrets init                         # создать мастер-ключ (один раз)
python tools/ragraf.py secrets list                         # что сохранено: имя, тип, маска, режим, автор, время
python tools/ragraf.py secrets list --query postgres --kind password
python tools/ragraf.py secrets show  pg/prod/app             # маска + метаданные, без значения
python tools/ragraf.py secrets reveal pg/prod/app --reason deploy
python tools/ragraf.py secrets set   pg/prod/app --kind password --service postgres `
        --tags "prod,db" --visibility listed --agents HARNESS   # значение из stdin
python tools/ragraf.py secrets grant  pg/prod/app --agents "HARNESS,AutoClaw"
python tools/ragraf.py secrets revoke pg/prod/app --agents HARNESS
python tools/ragraf.py secrets rm    pg/prod/app
python tools/ragraf.py secrets check                        # целостность, отпечаток ключа, сроки, дубли
python tools/ragraf.py secrets rotate                       # перешифровать новым ключом
python tools/ragraf.py secrets export --out snap.json --passphrase-env RAGRAF_PASS
python tools/ragraf.py secrets import --out snap.json --passphrase-env RAGRAF_PASS
python tools/ragraf.py secrets audit --limit 50             # журнал: кто, когда, что, результат
```

Значение **никогда** не передаётся аргументом командной строки (история
оболочки, список процессов). Источники: stdin, `--from-env VAR`,
`--from-file PATH`.

## Инструменты MCP

`ragraf_secret_list`, `ragraf_secret_show`, `ragraf_secret_reveal`,
`ragraf_secret_set`, `ragraf_secret_grant`, `ragraf_secret_revoke`,
`ragraf_secret_delete`, `ragraf_secret_policy`. В HARNESS те же инструменты
называются `mcp__ragraf__ragraf_secret_*`.

`reveal` требует `reason` (если включена настройка) — причина попадает в аудит;
без причины журнал отвечает «кто взял», но не «зачем».

## Права: как устроено

| Режим записи | Кто читает |
|---|---|
| `all` (**по умолчанию**, если права не выставлены) | любой агент с доступом к хранилищу |
| `listed` | только перечисленные имена (`--agents`), поддерживается `*` |
| `owner_only` | только тот, кто сохранил |

Плюс общий список `secrets_allowed_agents`: непустой список **сильнее** режима
записи и отсекает агента целиком.

- Права меняет **автор** записи или локальный администратор (человек за панелью,
  команда CLI на этой машине).
- Имя агента — то, что он о себе сообщает (`RAGRAF_AGENT`, `clientInfo.name`,
  параметр запуска MCP). Если клиент каждый раз зовётся по-новому, ограничение
  «только этому агенту» превращается в «никому» — проверять по `secrets audit`.
- Отказ **не подтверждает** существование записи: агент получает тот же ответ,
  что на несуществующее имя, причина остаётся в аудите.

## Что видно в списке

Имя, тип, сервис, метка, маска, длина значения, режим доступа и список агентов,
`created_at` (UTC, ISO-8601) и `updated_at`, `expires_at` и `days_left`,
`created_by` (кто добавил), `created_by_kind` (тип агента), `created_by_via`
(канал: mcp/cli/admin), `created_by_params` (метаданные агента: метка, тип,
канал, клиент, задача, признак администратора), `reveal_count` и кто выдавал
последним.

## Настройки (панель → раздел секретов, файл `catalog/settings.json`)

`secrets_enabled`, `secrets_capture_enabled`, `secrets_default_visibility`,
`secrets_reveal_requires_reason`, `secrets_max_value_bytes`,
`secrets_allowed_agents`, `secrets_expiry_warn_days`. Каждая меняет поведение;
переключателей «на будущее» в этом блоке нет.

## Что ломается чаще всего

| Симптом | Причина и что делать |
|---|---|
| `no_key` | мастер-ключ не создан: `secrets init`. Ключ НЕ создаётся сам при чтении — иначе опечатка в пути молча завела бы пустое хранилище |
| `disabled` | хранилище выключено настройкой `secrets_enabled`; включить, а не искать обход |
| `not_found` при известном имени | либо записи нет, либо нет права. Различить можно только в аудите: `secrets audit --limit 50` (причина `no_access` против `no_such_name`) |
| `reason_required` | включено требование причины: добавить `--reason` |
| `ciphertext_invalid` | другой мастер-ключ или повреждённая запись: `secrets check` назовёт запись |
| `capture_disabled` | попытка сохранить «встреченное» при выключенной настройке |
| `key_from_env` при ротации | ключ задан `RAGRAF_SECRETS_KEY`; менять надо переменную, а не файл |

Отдельно: **не путать** «нет права» с «нет записи» в отчёте агенту — для
внешнего наблюдателя это один и тот же ответ, и так задумано.

## Что ещё не сделано (не обещать владельцу)

Страница секретов в админ-панели, scopes `secrets:read`/`secrets:write` для MCP
по HTTP, автоматическое сохранение встреченного с детекторами, миграция
plaintext из `catalog/db_access.json`, разбор срока сертификата из тела. Полный
список — `C:\AutoClaw\AMD\08-ragraf\ПРОМТ-Хранилище-секретов.md` §5–§7.

## Проверки

```powershell
cd C:\AutoClaw\RAGRAF
python tests\test_vault.py                 # 60 свойств: крипто, права, аудит, ротация, снимок
python tools\ragraf.py secrets check       # ok: true + отпечаток ключа
```

Устройство и модель угроз — `docs/03_implementation/SECRETS-VAULT.md`; правило
проекта — `AGENTS.md` §6.1.
