"""MCP-сервер RAGRAF: операции фасада как инструменты агента (ФТ-22).

Протокол: JSON-RPC 2.0 поверх stdio (MCP, версия 2024-11-05).
Реализованы методы: initialize, notifications/initialized, tools/list, tools/call, ping.

Принципиальные ограничения (архитектура §7, ФТ-15):
  * наружу выставляются ТОЛЬКО фиксированные операции фасада;
  * отсутствует инструмент «выполнить запрос к графу» — языков запросов нет;
  * каждый ответ сохраняет конверт с провенансом и пометками устаревших фактов.

Запуск (пример конфигурации MCP-клиента):
    python tools/ragraf-mcp.py --project config/revoap.yaml
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from typing import Any, Callable

from . import __version__
from . import access as access_mod
from . import contact as contact_mod
from .config import ProjectConfig, load as load_config
from .config import ConfigError
from pathlib import Path

from .facade import Facade

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ragraf"

# Инструкции сервера: клиент обязан показать их агенту (поле `instructions`
# ответа `initialize`). Это ответ на B1: корпус определяется по рабочему
# каталогу ПРОЦЕССА сервера, а конфигурация клиента задаёт этот каталог
# (в HARNESS профиле — `cwd: RAGRAF`), поэтому сессия с другим
# рабочим каталогом получала бы факты о чужом проекте молча. Инструкция
# говорит, чем это проверить, и не заменяет серверную гарантию: сторож
# свежести работает на каждом вызове независимо от того, прочитал ли клиент
# это поле.
INSTRUCTIONS = (
    "RAGRAF — графово-векторный индекс кода проекта. "
    "1. Перед первой операцией вызовите ragraf_ensure: он сверяет контрольную "
    "сумму корпуса с проиндексированной и сам пересобирает отставший индекс. "
    "2. Если ответ ссылается на пути, которых нет в текущем проекте, вызовите "
    "ragraf_context: корпус мог быть определён по рабочему каталогу процесса "
    "сервера, а не по проекту задачи, — тогда выберите корпус через "
    "ragraf_use_project (список даёт ragraf_catalog). "
    "3. Факт без ссылки файл:строка — догадка; пустой результат — это "
    "found=false, а не ошибка. "
    "4. Перед записью в базу вызовите ragraf_write_check, перед правкой прав — "
    "ragraf_policy_trace. "
    "5. Поле stale=true означает, что код ушёл вперёд индекса: сначала "
    "ragraf_ensure, потом выводы."
)

# Ресурсы (MCP resources): состояние и реестр читаются клиентом по требованию,
# а не занимают место в списке инструментов и не стоят вызова.
STATUS_URI = "ragraf://status"
CATALOG_URI = "ragraf://catalog"
CORPUS_MAP_URI = "ragraf://corpus/{name}/map"
CORPUS_MAP_URI_PREFIX = "ragraf://corpus/"


def force_utf8_stdio() -> None:
    """Сделать потоки UTF-8, не полагаясь на локаль процесса и окружение.

    Зачем. MCP-клиент запускает сервер как дочерний процесс и передаёт ему
    УРЕЗАННОЕ окружение: официальный SDK наследует только `APPDATA`,
    `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `PATH`, `PROCESSOR_ARCHITECTURE`,
    `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `USERNAME`, `USERPROFILE`,
    `PROGRAMFILES` — ни `PYTHONUTF8`, ни `PYTHONIOENCODING` туда не попадают
    (замерено по `@modelcontextprotocol/client/dist/stdio.mjs`). Оболочка
    AutoClaw ставит обе переменные, поэтому дефект был невидим; DeepSeek
    Harness добавляет к этому чистку `PYTHON*` — и сервер падал на ПЕРВОМ же
    ответе с русским текстом: `UnicodeEncodeError: 'charmap' codec can't
    encode characters` в `send()` на кодировке cp1252, то есть клиент видел
    не инструменты, а мёртвый процесс.

    Русский текст есть и в описаниях инструментов (`tools/list`), и в ответах
    операций, поэтому полагаться на окружение нельзя: потоки перенастраиваются
    явно. stdin тоже: аргументы запроса приходят в UTF-8, а декодированные
    в cp1252 превратились бы в мусор, и поиск вернул бы не то — без единой
    ошибки.

    Ошибка замены не глушится: если потока нет или он без `reconfigure`
    (вывод перенаправлен в объект без него), это нормальный случай.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# Описание инструментов: имя = имя операции фасада.
# Имя поля протокола MCP для уведомлений о ходе. Вынесено в константу,
# потому что литерал встречается в двух местах (чтение запроса и ответ), и
# дублировать его — источник расхождения. Значение — имя поля протокола,
# а не учётные данные.
PROGRESS_FIELD = "progressToken"

TOOLS: list[dict[str, Any]] = [
    # --- документный слой (этап «документы», фаза A) ---
    {
        "name": "ragraf_doc_search",
        "description": (
            "Поиск по документам с адресом: возвращает локатор (path:12, "
            "path#anchor, path:sheet=…), структуру и провенанс разбора. "
            "Отличается от ragraf_search тем, что адрес документа не строка файла."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос по содержанию документа"},
                "limit": {"type": "integer", "default": 20},
                "kind": {"type": "string",
                         "description": "вид узла: section, paragraph, table, record, slide, sheet"},
                "doc": {"type": "string", "description": "ограничить документом (путь или doc_id)"},
                "with_text": {"type": "boolean", "default": True},
                "text_width": {"type": "integer", "default": 240},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_asof",
        "description": (
            "Поиск по документам, действительным на дату: в каждом факте — "
            "интервал действия (valid_from/valid_to), редакция (version_id) и "
            "признак замены (superseded_by). Отличается от ragraf_doc_search "
            "тем, что отвечает по редакции на дату, а не по последней загруженной."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос по содержанию документа"},
                "date": {"type": "string",
                         "description": "дата действия: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"},
                "limit": {"type": "integer", "default": 20},
                "kind": {"type": "string",
                         "description": "вид узла: section, paragraph, table, record, slide, sheet"},
                "with_text": {"type": "boolean", "default": True},
                "text_width": {"type": "integer", "default": 240},
            },
            "required": ["query", "date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_dependents",
        "description": (
            "Что опиралось на факт: прямые и транзитивные зависимости по рёбрам "
            "деривации (до depth шагов). У каждой строки — адрес, провенанс "
            "(stated/derived), расстояние и действие: «пересчитать» или «решение человека»."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "идентификатор факта"},
                "locator": {"type": "string", "description": "адрес факта: path:строки"},
                "depth": {"type": "integer", "default": 3},
                "include_stated": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_stale_cascade",
        "description": (
            "Что пересчитать после изменения входов: обход рёбер деривации "
            "от изменённых фактов до depth шагов, с адресами и расстоянием. "
            "По умолчанию только отчёт; apply ставит пометки, clear их "
            "закрывает. За пределом глубины факты считаются в outside_cap."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "changed": {"type": "string",
                            "description": "факты или адреса через запятую"},
                "depth": {"type": "integer", "default": 3},
                "apply": {"type": "boolean", "default": False},
                "clear": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_entity_neighbors",
        "description": (
            "Соседи сущности по документному графу: кто связан и через какую "
            "связь. У каждого соседа — адрес цитаты (path:start_line[-end_line]), "
            "поэтому утверждение можно открыть. Вид «theme» отвечает темами, "
            "когда слой тем собран (этап 5)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string",
                           "description": "имя сущности или её entity_id"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1,
                          "description": "шагов обхода: 1..3; вне диапазона — отказ"},
                "kind": {"type": "string", "enum": ["entity", "theme"], "default": "entity"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["entity"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_relations",
        "description": (
            "Связи документов через общие сущности: пары «документ ↔ документ» с "
            "адресами упоминания связующей сущности с ОБЕИХ сторон. Нужен ровно "
            "один вход: doc (путь) либо entity (имя или id)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc": {"type": "string", "description": "путь документа"},
                "entity": {"type": "string", "description": "имя сущности или entity_id"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_themes",
        "description": (
            "Темы документного графа: группы сущностей, которые встречаются "
            "вместе чаще случайного. Кластеры детерминированы (тот же слой — те "
            "же theme_id), у каждого участника адрес цитаты, у темы — сводка по "
            "цитатам, если модель была доступна (иначе summary=null и причина). "
            "Издание pro (возможность doc-graph)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string",
                          "description": "ограничить область: подкаталог корпуса"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_global_overview",
        "description": (
            "Карта документного графа: документы, сущности, связи, темы, сводки "
            "и топ-темы с адресами цитат. Отвечает на «что вообще есть», а не "
            "«что найдено по запросу». Издание pro (возможность doc-graph)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_search_levels",
        "description": (
            "Dual-level поиск одним ответом: локальные факты (чанк с адресом "
            "path:line) и глобальный уровень — сущности и связи документного "
            "графа, в которые эти чанки входят. Связь уровней названа явно: у "
            "хита via_entities, у глобального факта via_chunk. Локальный "
            "уровень работает под любым изданием; глобальный требует "
            "возможности doc-graph (pro) и без неё называется неполнота."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                          "default": 20},
                "mode": {"type": "string", "enum": ["local", "global", "both"],
                         "default": "both",
                         "description": "local — только чанки, global — только граф, both — оба уровня"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_get",
        "description": (
            "Текст по адресу документа и провенанс. Сначала ищет в индексе, "
            "затем разрешает по диску тем же читателем и называет источник."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "locator": {"type": "string",
                            "description": "адрес: path:12, path:page=3, path#anchor, path:sheet=Лист!A1:B2"},
                "with_provenance": {"type": "boolean", "default": True},
            },
            "required": ["locator"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_status",
        "description": (
            "Состояние документного слоя: сколько документов разобрано, "
            "сколько failed и not_indexed_by_edition с причинами, бюджеты чанков."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc": {"type": "string", "description": "документ (путь или doc_id)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_ocr",
        "description": (
            "Состояние распознавания текста (OCR) и решение по файлу: включена ли "
            "возможность, есть ли локальный движок, почему нет. Изображение без OCR "
            "не превращается в пустой текст молча."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "файл изображения, если нужно решение по нему"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_doc_validate",
        "description": (
            "Проверка целостности чанкинга документов: адрес у каждого чанка, "
            "heading_chain или причина, table_id у таблиц, неподавленные ошибки."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc": {"type": "string", "description": "документ (путь или doc_id)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_index_status",
        "description": (
            "Состояние индекса кода: ревизия, полнота разбора, доступные возможности, "
            "размер журнала расхождений. ВЫЗЫВАТЬ ПЕРВЫМ перед любой работой с кодом."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ragraf_compact",
        "description": (
            "Политика хранения .ragraf: сколько занимают граф, лексика, хранилище "
            "управления и каталог cpg, что удаляется вместе с ревизией и где нужен "
            "VACUUM. По умолчанию только ПЛАН (ничего не меняется). apply=true "
            "удаляет хвост ротации, артефакты CPG вне хранения и временные файлы, "
            "затем VACUUM там, где это оправдано. Меняющая диск операция: "
            "вызывать после плана и бэкапа."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "apply": {
                    "type": "boolean",
                    "default": False,
                    "description": "выполнить очистку (по умолчанию только план)",
                },
                "vacuum": {
                    "type": "boolean",
                    "description": "VACUUM: true — выполнить, false — не выполнять, "
                                   "не задано — по порогу свободных страниц",
                },
                "retain": {
                    "type": "integer",
                    "description": "сколько последних ревизий хранить (по умолчанию retain_revs)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_context",
        "description": (
            "КАКОЙ КОРПУС ВЫБРАН: имя разработки, корень, ревизия, свежесть, режим "
            "и на каком основании корпус определён (аргумент запуска, рабочий каталог "
            "процесса, реестр или use_project). Вызывать, когда ответ ссылается на "
            "пути, которых нет в текущем проекте: молчаливая подмена корпуса — "
            "источник ложных фактов, а не мелкое неудобство."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "probe": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "сверить контрольную сумму корпуса с кодом (может занять "
                        "время); false — только состояние индекса без сверки"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_drift_report",
        "description": (
            "Журнал расхождений индекса и кода. Если непуст — факты по перечисленным "
            "путям могли устареть: сначала вызовите live_read по этим файлам, "
            "и только потом делайте выводы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"since": {"type": "integer", "description": "начиная с id записи"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_sso_login",
        "description": (
            "Единый вход (OIDC, authorization code + PKCE): обменять код "
            "авторизации и проверить id_token — подпись по JWKS, iss, aud, exp, "
            "nonce. Роли берутся только из групп отображения sso_group_roles; "
            "роль, присланная клиентом, не читается. Возможность издания "
            "enterprise: без действующей лицензии возвращается EditionRefusal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "код авторизации от провайдера"},
                "state": {"type": "string", "description": "state начатой попытки входа"},
                "provider": {"type": "string", "description": "вид провайдера (по умолчанию из настроек)"},
                "redirect_uri": {"type": "string", "description": "адрес возврата, если он не из настроек"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_sso_map_roles",
        "description": (
            "Отобразить группы внешнего каталога на роли панели (read/write/admin) "
            "и показать права, которые они дадут. Без сети: отвечает ДО включения "
            "входа. Пустое отображение означает «прав не получит никто»."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "имена групп из каталога",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_siem_export",
        "description": (
            "Выгрузить журналы (доступ к панели и аудит хранилища секретов) во "
            "внешнюю SIEM после курсора: только метаданные, значения секретов не "
            "выгружаются никогда. Курсор сдвигается лишь после успешной доставки. "
            "Возможность издания enterprise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "access | secrets | all"},
                "limit": {"type": "integer", "description": "сколько записей уходит в наборе"},
                "dry_run": {"type": "boolean", "description": "собрать набор, но не отправлять"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_siem_test",
        "description": (
            "Пробная доставка в приёмник SIEM: дойдёт ли набор до приёмника "
            "сейчас. Курсор не трогает и журналы не читает — поэтому пробу можно "
            "делать до включения выгрузки."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ragraf_license_offline",
        "description": (
            "Офлайн-лицензия закрытого контура: заявка от установки, подпись "
            "лицензии, приёмка с привязкой к установке и заявка на перенос. Сети "
            "не требует; без ключа подписи выпуск отказывает с причиной. "
            "Возможность издания enterprise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["request", "issue", "accept", "transfer"],
                    "description": "вид работы: заявка, выпуск, приёмка, перенос",
                },
                "request": {"type": "string", "description": "файл заявки"},
                "license": {"type": "string", "description": "файл лицензии"},
                "out": {"type": "string", "description": "куда записать результат"},
                "edition_name": {"type": "string", "description": "запрашиваемое издание"},
                "licensee": {"type": "string", "description": "лицензиат"},
                "days": {"type": "integer", "description": "срок лицензии в днях"},
                "note": {"type": "string", "description": "пометка в заявке"},
                "public_key": {"type": "string", "description": "доверенный открытый ключ"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_attest_bundle",
        "description": (
            "Аттестация поставки: собрать подписанный набор доказательств "
            "(версия, ревизия корпуса и её сумма, хэши поставки, SBOM, отчёт "
            "проверяльщика, карта изданий, порчи гейта) либо проверить готовый "
            "набор пересчётом хэшей из файлов. Набор без подписи помечается "
            "signed=false. Возможность издания enterprise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "verify": {"type": "boolean", "description": "проверить готовый набор"},
                "out": {"type": "string", "description": "каталог набора (сборка)"},
                "bundle": {"type": "string", "description": "каталог набора (проверка)"},
                "tree": {"type": "string", "description": "дерево поставки для хэшей"},
                "with_sbom": {"type": "boolean", "description": "включить SBOM"},
                "with_editions": {"type": "boolean", "description": "включить карту изданий"},
                "gate_report": {"type": "string", "description": "отчёт гейта порч"},
                "sign": {"type": "boolean", "description": "подписать манифест"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_broker_issue",
        "description": (
            "Выдача секрета по запросу: аренда со сроком, распорядителем и "
            "причиной. Значение отдаётся ОДИН раз: повтор получает lease_used, "
            "смена значения — lease_stale, истечение срока — lease_expired. "
            "Свою запись агент получает по правам хранилища, чужую — только от "
            "её автора. Возможность уровня pro (secret-broker); в сборке без "
            "модуля отвечает причиной, а не пустым ответом."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "secret": {"type": "string", "description": "имя секрета в хранилище"},
                "agent": {
                    "type": "string",
                    "description": "кому выдаётся аренда; пусто — вызывающему",
                },
                "ttl_minutes": {
                    "type": "integer", "default": 0,
                    "description": "срок аренды в минутах; 0 — как настроено, не «бессрочно»",
                },
                "reason": {"type": "string", "description": "причина выдачи (в журнал доступа)"},
                "reveal": {
                    "type": "boolean", "default": True,
                    "description": "погасить аренду сразу и вернуть значение",
                },
            },
            "required": ["secret"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_broker_rotate",
        "description": (
            "Плановая смена секрета: status — что запланировано и что просрочено, "
            "plan — задать срок смены, run — сменить значение сейчас (версия "
            "поднимается, все аренды записи отзываются). Значение в ответ не "
            "возвращается. Возможность уровня pro (secret-broker)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "secret": {"type": "string", "description": "имя секрета (нужно для plan и run)"},
                "mode": {
                    "type": "string", "enum": ["status", "plan", "run"],
                    "default": "status", "description": "вид работы",
                },
                "every_days": {
                    "type": "integer", "default": 0,
                    "description": "срок смены в сутках; 0 — как настроено",
                },
                "value": {
                    "type": "string",
                    "description": "новое значение; пусто — сгенерировать",
                },
                "reason": {"type": "string", "description": "причина смены (в журнал доступа)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_contract_pin_status",
        "description": (
            "ПИННИНГ ПОДПИСИ ОПЕРАЦИЙ: что подтверждено человеком, что разошлось "
            "с текущим описанием. Хеш считается по каноническому описанию "
            "операции (имя, параметры с умолчаниями, ключи ответа) через "
            "surface — второго описания поверхности нет. В ответе: пины с "
            "возрастом, расхождения (оба хеша и возраст пина), неподтверждённые "
            "операции, устаревшие пины и события журнала (pin_mismatch — "
            "высокий уровень, pin_break — вызов, которого уже нет в описании). "
            "Ничего не меняет. Возможность издания enterprise (contract-pin)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50,
                          "description": "сколько последних событий журнала показать"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_contract_pin_approve",
        "description": (
            "ПОДТВЕРДИТЬ ТЕКУЩЕЕ ОПИСАНИЕ операции (или всех операций) — пин "
            "ставится на релиз, а не на каждый запуск. Автор обязателен: пин без "
            "автора неотличим от самоподписи. Автоматического подтверждения нет: "
            "при расхождении RAGRAF сообщает, а не «чинит» пин сам. Возможность "
            "издания enterprise (contract-pin)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "description": "какую операцию подтвердить"},
                "everything": {"type": "boolean", "default": False,
                               "description": "подтвердить описание всех операций"},
                "by": {"type": "string", "description": "кто подтверждает (обязательно)"},
                "note": {"type": "string", "description": "пометка к пину"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_preflight",
        "description": (
            "ЦЕНА И ГРАНИЦЫ ДО СТАРТА: осмотр каталога ДО создания индекса и "
            "ничего не создаёт. Отвечает: распределение по расширениям и число "
            "файлов, доля сгенерированного кода, найденные миграции, ожидаемое "
            "время разбора (замер на выборке тем же парсером с названной "
            "погрешностью), слепые зоны динамики по языкам с адресами "
            "файл:строка (call_user_func, $obj->$method, getattr, eval, "
            "reflection), готовность векторов и Joern, эффект исключений. "
            "Возможность издания pro (preflight)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string",
                         "description": "каталог для осмотра (по умолчанию корень корпуса)"},
                "sample": {"type": "integer", "default": 50,
                           "description": "сколько файлов разобрать для замера времени"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_quality_suite",
        "description": (
            "Набор проверок индекса: чанки сверх предела размера, обрезанные "
            "сигнатуры, доступность векторного поиска, честность режима "
            "capability и доля неполного разбора. Прогон записывается в журнал, "
            "ответ детерминирован: на неизменённом индексе два прогона дают "
            "одинаковый digest. Возможность издания pro (quality-loop)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "пометка прогона в журнале"},
                "record": {
                    "type": "boolean", "default": True,
                    "description": "записать прогон в журнал (false — только измерить)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_quality_diff",
        "description": (
            "Разбор расхождений между двумя прогонами качества: что появилось, "
            "что исчезло, что изменилось. При разных ревизиях вердикт не "
            "выносится — различие может быть следствием правки кода. "
            "Возможность издания pro (quality-loop)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_run": {"type": "integer", "description": "номер прогона «от»"},
                "to_run": {"type": "integer", "description": "номер прогона «до»"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_quality_pair",
        "description": (
            "ПАРНЫЙ ПРОГОН ПО ФИКСИРОВАННЫМ КЕЙСАМ: два прогона качества "
            "сравниваются по каждому кейсу (better/worse/not_sure). Кейс — "
            "ожидаемые пути, обязательные шаги и запрет обхода (файл "
            "quality-cases.yaml плюс задачи bench/tasks.yaml). Правило "
            "блокирующее: если хотя бы один ранее проходивший кейс упал, общий "
            "вердикт не может быть «не хуже» — находка regressions. Одинаковое "
            "состояние (та же ревизия и отпечаток) даёт все not_sure честно. "
            "От quality_diff отличается предметом: тот сравнивает показатели "
            "индекса и между ревизиями вердикт не выносит. Возможность издания "
            "pro (quality-loop)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_run": {"type": "integer", "description": "номер прогона «от»"},
                "to_run": {"type": "integer", "description": "номер прогона «до»"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_quality_trace",
        "description": (
            "ТРАССА ПОДТВЕРЖДЁННОГО СБОЯ: записать сбой кейса целиком "
            "(объявление, шаги сеанса, решения гейта, ревизия индекса), "
            "показать сохранённые и повторить трассу. Повтор называет "
            "вердикт: still_failing, fixed (только на той же ревизии), "
            "other_rev, not_reproducible. Пройденный кейс не пишется.\n\n"
            "Тип: действие с журналом. Меняет: только журнал сбоев "
            "(таблица quality_trace), индекс и документы не трогает.\n"
            "Не меняет: граф, чанки, базу.\n"
            "Возвращает: trace_id, digest, вердикт повтора и причину.\n"
            "Сначала: quality_suite --session, затем save с тем же session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "list", "replay"],
                           "default": "list"},
                "case_id": {"type": "string",
                            "description": "кейс, чей сбой записывается"},
                "trace_id": {"type": "integer", "default": 0},
                "session": {"type": "string",
                            "description": "сеанс, чьи шаги попадут в трассу"},
                "label": {"type": "string", "default": ""},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_quality_prune",
        "description": (
            "Прореживание журнала прогонов качества: удаляет старые записи, "
            "оставляя последние keep. По умолчанию сухой прогон — только "
            "перечень; удаление требует confirm и до него снимается копия "
            "журнала. Удаляются только строки журнала прогонов по явным "
            "номерам: история расхождений и ревизии не трогаются. "
            "Возможность издания pro (quality-loop)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keep": {
                    "type": "integer",
                    "description": "сколько последних прогонов оставить (не меньше 1); "
                                   "если не указан — из предела журнала",
                },
                "dry_run": {
                    "type": "boolean", "default": True,
                    "description": "только перечень того, что было бы удалено",
                },
                "confirm": {
                    "type": "boolean", "default": False,
                    "description": "подтверждение необратимого удаления",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_search",
        "description": (
            "Гибридный поиск по коду и документации (лексика + семантика). "
            "Возвращает файл, диапазон строк, причину совпадения и пометку устаревания."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос: имя, фраза, описание задачи"},
                "limit": {"type": "integer", "default": 20},
                "with_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "добавить к каждому хиту фрагмент текста из индекса — экономит второй вызов",
                },
                "text_width": {"type": "integer", "default": 160},
                "answer": {
                    "type": "boolean", "default": False,
                    "description": ("вернуть сформулированный ответ со ссылками вместо "
                                    "структурного результата (P2-2): ≤ 1–2 КБ"),
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "code", "docs", "themes"],
                    "default": "auto",
                    "description": ("режим поиска: auto — прежнее поведение; code — "
                                    "только код; docs — dual-level (уровень «тема» из "
                                    "документного графа, издание pro); themes — темы"),
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_search_multi",
        "description": (
            "Поиск одним ответом по НЕСКОЛЬКИМ запросам: литеральные вхождения "
            "(не токены — находится и `'MCH', 'MOL'`), кодировка каждого файла, "
            "группировка по семействам копий (Files.php…Files7.php) с указанием, "
            "какой файл активен и чем это доказано. Экономит 2–4 вызова вместо "
            "последовательного поиска грепом."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "запросы: строки или регулярные выражения",
                },
                "limit": {"type": "integer", "default": 20},
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "regex": {
                    "type": "boolean",
                    "default": False,
                    "description": "считать запросы регулярными выражениями",
                },
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_duplicates",
        "description": (
            "Группы копий файлов: байт-идентичные и «одно имя с разными номерами» "
            "(php/Wizard/Files.php…Files7.php, import1.pl/import2.pl). У каждой группы "
            "назван главный файл и доказательство его активности — ссылка path:line."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "limit": {"type": "integer", "default": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_entrypoints",
        "description": (
            "Точки входа: маршруты, HTTP-обработчики (чтение $_GET/$_POST/$_SERVER), "
            "CLI-скрипты, endpoint'ы из разметки и скриптов. С параметром path — "
            "достижим ли конкретный файл от точек входа и кто на него ссылается."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "разобрать один файл"},
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_dead_code",
        "description": (
            "Файлы и подпрограммы без входящих ссылок и вне достижимости от точек "
            "входа. Вердикт выносится только при совпадении двух проверок (граф и "
            "текст); кандидаты, оправданные найденной ссылкой, перечислены в refuted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_diff",
        "description": (
            "Что изменилось между ревизиями индекса (P2-1): файлы, символы, рёбра, "
            "а также РЕГРЕССИИ — символы, потерявшие всех вызывающих, и таблицы, "
            "переставшие заполняться. Сравниваются факты двух ревизий; режим "
            "каждой назван в `modes`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "string",
                          "description": "ревизия для сравнения (по умолчанию — предыдущая)"},
                "limit": {"type": "integer", "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_excludes",
        "description": (
            "Управление списком исключений (уровень pro): какие каталоги не "
            "считать в корпус (temp, tmp, cache, logs и подобные). Действие "
            "show/impact считает эффект тем же сканером, что и индексация; "
            "apply записывает настройку и ОТКАЗЫВАЕТ, если выпадают файлы кода."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["show", "impact", "apply", "disable"],
                           "default": "show"},
                "dirs": {"type": "string",
                         "description": "имена каталогов через запятую"},
                "force": {"type": "boolean", "default": False,
                          "description": "включить даже при потере файлов кода"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_note",
        "description": (
            "Записать короткую заметку о решении (P2-3): знание, которое должно "
            "пережить сессию («в этой базе часть колонок генерируемые — писать "
            "нельзя»). Заметка — знание агента, а не факт индекса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "текст заметки"},
                "task": {"type": "string", "description": "метка задачи (RAGRAF_TASK)"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "path": {"type": "string", "description": "файл, к которому относится"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_notes",
        "description": (
            "Прочитать заметки (P2-3): свежие первыми, с фильтром по задаче и "
            "подстроке текста. Каждая запись помечена `source: note`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "query": {"type": "string", "description": "подстрока текста заметки"},
                "limit": {"type": "integer", "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_task",
        "description": (
            "Назвать текущую задачу — так работает `RAGRAF_TASK`, когда клиент "
            "не может передать метку в долгоживущий MCP-процесс. Метка собирает "
            "все обращения к индексу в одну задачу (её читают и MCP-сервер, и "
            "CLI, и хук гейта) и видна в `ragraf_task_stats`. Пустое имя или "
            "`clear=true` снимает метку. Переменная окружения `RAGRAF_TASK`, "
            "если задана при старте клиента, имеет приоритет над названной."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "метка задачи (пусто — снять)"},
                "clear": {"type": "boolean", "default": False,
                          "description": "снять метку задачи"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_task_stats",
        "description": (
            "Готовый отчёт по задаче (§3.4): сколько было обращений к индексу, "
            "сколько фактов, сколько вызовов заменили чтение файлов, были ли "
            "ответы по устаревшей ревизии, что решил гейт использования индекса. "
            "Числа берутся из журнала обращений и состояния гейта — их видно "
            "командой `ragraf_usage`, а отчёт добавляет решения гейта и заметки. "
            "Пусто по задаче — это находка (`calls: 0`), а не отсутствие данных."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "метка задачи (по умолчанию текущая)"},
                "format": {"type": "string", "enum": ["markdown", "json"], "default": "markdown"},
                "limit": {"type": "integer", "default": 10},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_symbol",
        "description": "Найти символ (класс, метод, функцию) по имени или полному имени.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string", "description": "ограничить файлом"},
                "limit": {"type": "integer", "default": 20},
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_references",
        "description": "Все места использования символа с указанием файла и строки.",
        "inputSchema": {
            "type": "object",
            "properties": {"fqn": {"type": "string"}, "limit": {"type": "integer", "default": 100}},
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_callers",
        "description": (
            "Кто вызывает символ. hard_only=true — только доказанные связи. "
            "Поле `name_links` перечисляет вызовы, назвавшие это имя, но не "
            "привязанные к определению (межфайловое разрешение по имени не "
            "делается): пустой `count` не означает «вызовов нет»."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "hard_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
                "min_confidence": {
                    "type": "number",
                    "description": ("порог достоверности связей 0..1 на этот вызов "
                                    "(по умолчанию 0.5; доказанные связи не "
                                    "отсекаются ни при каком пороге). Ответ "
                                    "называет значение и сколько отсечено (этап 10)"),
                },
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_callees",
        "description": "Кого вызывает символ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "hard_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
                "min_confidence": {
                    "type": "number",
                    "description": ("порог достоверности связей 0..1 на этот вызов "
                                    "(по умолчанию 0.5; доказанные связи не "
                                    "отсекаются ни при каком пороге). Ответ "
                                    "называет значение и сколько отсечено (этап 10)"),
                },
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_edge_evidence",
        "description": (
            "Рёбра символа с доказательствами (этап 8): вид доказательства у "
            "каждого ребра (`cpg_call` — подтверждено CPG, `signature_match` — "
            "по имени и месту объявления, `name_inference` — выведено по "
            "имени), способ привязки цели, координата `path:line`, сверенная с "
            "файлом ревизии (`evidence_ok`). Отдаются оба направления: `out` — "
            "кого вызывает, `in` — кто вызывает. `evidence.derived` называет, "
            "сколько рёбер в ответе выведено, а не подтверждено."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "kind": {
                    "type": "string",
                    "description": ("вид ребра: calls, calls_heuristic, …, либо "
                                    "all — показать и структурные"),
                },
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_impact",
        "description": (
            "Что затронет изменение символа или таблицы. Доказанные связи — отдельно от "
            "эвристических. Используйте ПЕРЕД правкой, чтобы не сломать потребителей."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "символ, имя файла или table:имя"},
                "mode": {"type": "string", "enum": ["hard", "full"], "default": "full"},
                "depth": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 200},
                "min_confidence": {
                    "type": "number",
                    "description": ("порог достоверности связей 0..1 на этот вызов "
                                    "(по умолчанию 0.5; доказанные связи не отсекаются)"),
                },
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_slice",
        "description": (
            "Структурный срез вокруг символа: только релевантные узлы и связи со строками, "
            "БЕЗ тел функций. Доступно только при полной сборке (capability=full)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "direction": {"type": "string", "enum": ["back", "forward"], "default": "back"},
                "limit": {"type": "integer", "default": 200},
                "min_confidence": {
                    "type": "number",
                    "description": ("порог достоверности связей 0..1 на этот вызов "
                                    "(по умолчанию 0.5; доказанные связи не отсекаются)"),
                },
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_code",
        "description": (
            "Тела символов — только по явному запросу. По умолчанию текст берётся "
            "из индекса; вложенные спаны не дублируются."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "path": {"type": "string"},
                "entry_ids": {
                    "type": "string",
                    "description": "node_id и/или chunk_id из хитов search — адрес второго шага",
                },
                "start": {"type": "integer"},
                "end": {"type": "integer"},
                "style": {"type": "string", "enum": ["compact", "signatures"], "default": "compact"},
                "source": {"type": "string", "enum": ["auto", "index", "disk"], "default": "auto"},
                "include_containers": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 5},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_repo_map",
        "description": "Карта репозитория (модули → файлы → ключевые символы) в заданном бюджете токенов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "подкаталог"},
                "budget_tokens": {"type": "integer", "default": 2000},
                "detail": {"type": "string", "enum": ["normal", "deep"], "default": "normal"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_schema",
        "description": "Схема БД из индекса: таблицы, колонки, ключи, источник схемы.",
        "inputSchema": {
            "type": "object",
            "properties": {"table": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_table_usage",
        "description": (
            "Кто читает и кто пишет таблицу БД: символы и SQL-обращения со строками. "
            "В каждом факте есть write и access (select/insert/update/delete/ddl), "
            "поэтому параметром mode можно отобрать только читающие (read) или "
            "только изменяющие (write) обращения. Поле unresolved_tables перечисляет "
            "таблицы, которых нет в схеме из миграций: это признак опечатки в SQL "
            "либо обращения к системной таблице."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
                "mode": {"type": "string", "enum": ["all", "read", "write"],
                         "default": "all"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_policy_trace",
        "description": (
            "ТРАССИРОВКА РЕШЕНИЙ О ПРАВАХ: где функция прав возвращает разрешение "
            "и где отказ, какая ветвь case к какому действию относится, и что "
            "происходит, если действие не описано явно. Вызывать при проверке или "
            "правке матрицы прав, маршрутов согласования и условий доступа. "
            "Поле default_behavior = allow означает дефект: неизвестное действие "
            "получает право. Поле callers показывает точки применения решения."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "action": {"type": "string", "description": "действие для отбора ветвей"},
                "depth": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 300},
                "min_confidence": {
                    "type": "number",
                    "description": ("порог достоверности связей вызывающих 0..1 "
                                    "(по умолчанию 0.5; доказанные связи не отсекаются)"),
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_class_usage",
        "description": (
            "КЛАССЫ ОФОРМЛЕНИЯ: где класс определён в стилях, где использован "
            "в разметке и что расходится. Вызывать при правке интерфейса и шаблонов. "
            "Режим missing возвращает классы, которые используются в разметке, "
            "но НЕ описаны в стилях: страница при этом работает и выглядит "
            "«как-то» за счёт оформления браузера по умолчанию, поэтому ошибка "
            "не проявляется при запуске. Режим unused — описанные, но не "
            "используемые классы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "имя класса без точки"},
                "mode": {"type": "string", "enum": ["all", "missing", "unused"],
                         "default": "all"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_write_check",
        "description": (
            "СВЕРКА ПЛАНА ЗАПИСИ СО СХЕМОЙ до выполнения запроса. Вызывать перед INSERT/UPDATE:"
            " возвращает отсутствующие колонки с ближайшими существующими именами, "
            "обязательные колонки и первичный ключ. Предотвращает ошибки вроде "
            "«Unknown column» на первом сохранении."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "колонки, которые собираетесь записать",
                },
                "operation": {
                    "type": "string",
                    "enum": ["insert", "update", "delete"],
                    "default": "insert",
                },
                "values": {
                    "type": "integer",
                    "description": "сколько значений в запросе (ловит Column count doesn't match)",
                },
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_usage",
        "description": (
            "Статистика обращений к индексу: сколько вызовов, по каким операциям и "
            "объектам, сколько фактов получено и сколько объёма не пришлось читать "
            "файлами. Возвращает разбивку по агентам (кто именно использовал "
            "RAGRAF) и по семействам агентов. Фильтры agent и agent_kind "
            "позволяют смотреть отдельного агента или всех агентов AutoClaw. "
            "scope=session отвечает на вопрос «видит ли журнал МОИ вызовы»: "
            "в этом режиме заполнено calls_in_session и session_id, а поле "
            "write_failures показывает, сколько записей журнал не смог сохранить. "
            "Нужна для оценки эффекта от использования индекса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "session", "agent"],
                    "default": "all",
                    "description": (
                        "область сводки: all — весь журнал, session — только эта "
                        "сессия процесса (проверка «журнал видит мои вызовы»), "
                        "agent — только агент этого процесса"
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "конкретная сессия процесса; без него берётся текущая. "
                        "Идентификатор текущей сессии виден в ответе (поле session_id)"
                    ),
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "только этот агент, например AutoClaw, SagaAI "
                        "или MD.Viewer (без параметра — все агенты)"
                    ),
                },
                "agent_kind": {
                    "type": "string",
                    "enum": ["autoclaw", "external", "internal"],
                    "description": (
                        "только это семейство агентов: autoclaw — все агенты "
                        "AutoClaw вместе, external — сторонние, "
                        "internal — служебные проверки"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_freshness",
        "description": (
            "СВЕЖЕСТЬ ИНДЕКСА: совпадает ли контрольная сумма кода с проиндексированной. "
            "Если код менялся после индексации, операция сама выполняет реиндексацию. "
            "Вызывать после правок кода, чтобы убедиться, что факты актуальны."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False,
                          "description": "проверить минуя кэш"}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_use_project",
        "description": (
            "Выбрать корпус, с которым работает этот сервер: имя разработки из реестра "
            "или путь к каталогу проекта. Нужно, когда один сервер обслуживает несколько "
            "проектов: клиент запускает сервер один раз, а корпус задаётся по задаче. "
            "Ответ подтверждает ревизию и режим выбранного корпуса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "имя разработки в реестре (например revoap.com) или путь к каталогу проекта",
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_ensure",
        "description": (
            "ПРИВЕСТИ КОРПУС В АКТУАЛЬНОЕ СОСТОЯНИЕ — вызывать ПЕРВЫМ шагом работы. "
            "Сверяет контрольную сумму кода с проиндексированной и при расхождении "
            "пересобирает индекс сам. Без аргумента проверяет корпус, к которому "
            "подключён этот сервер; с аргументом `root` — корпус указанного каталога "
            "(если корпуса нет, он будет создан для каталога, похожего на проект). "
            "Ответ: status = actual | reindexed | created | not_a_project | error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "каталог проекта; по умолчанию корень своего корпуса",
                },
                "no_create": {
                    "type": "boolean",
                    "default": False,
                    "description": "не создавать корпус для незнакомого каталога",
                },
                "no_index": {
                    "type": "boolean",
                    "default": False,
                    "description": "только проверить состояние, не индексировать",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "проверить свежесть минуя кэш (сразу после правок)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_catalog",
        "description": (
            "РЕЕСТР РАЗРАБОТОК: какие проекты индексированы, когда, с какими "
            "контрольными суммами и актуален ли каждый индекс. Показывает, что "
            "разные разработки учитываются раздельно."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ragraf_route",
        "description": (
            "МАРШРУТ К ЛОКАЛЬНОМУ ИНДЕКСУ: где лежат материалы по запросу и какой "
            "корпус отвечает точнее. Сравнивает реестр разработок и карту "
            "метаданных, называет причину выбора и состояние локального индекса "
            "(ревизия, режим, свежесть). Содержимое файлов НЕ читает и НЕ цитирует: "
            "это ответ «куда идти», а не ответ «что написано». Вызывать перед "
            "search, когда вопрос про другой проект или когда неизвестно, есть ли "
            "для него корпус."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "что ищем: проект, документ, тема"},
                "limit": {"type": "integer", "default": 10},
                "verify": {
                    "type": "boolean",
                    "default": True,
                    "description": "сверить отпечаток рекомендованного корпуса (читает только метаданные)",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_neighbors",
        "description": (
            "СОСЕДНИЕ КОНТУРЫ: есть ли готовая реализация нужной функции в "
            "ДРУГИХ корпусах того же стека. Стек берётся по фактическому "
            "индексу (языки и число файлов), а не по списку в конфиге. "
            "Возвращает соседей с причиной выбора и свежестью, хиты с "
            "корпусом, путём, строкой и ревизией ТОГО корпуса, а также "
            "`skipped` — кого не спросили и почему. Чужие индексы только "
            "читаются и не переиндексируются. Вызывать ПЕРЕД тем, как писать "
            "функцию, у которой может быть готовый аналог в соседнем проекте."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "что делает функция: словами или именем"},
                "name": {"type": "string",
                         "description": "имя функции/класса: точное совпадение идёт первым"},
                "limit": {"type": "integer", "default": 20},
                "per_corpus": {"type": "integer", "default": 5},
                "stack": {"type": "string",
                          "description": "языки через запятую вместо измеренных (php,sql)"},
                "min_shared": {"type": "integer", "default": 1},
                "with_text": {"type": "boolean", "default": True},
                "kind": {"type": "string",
                         "description": "вид чанка (symbol, file, db-structure); по умолчанию — всё, кроме слоя баз"},
                "verify": {"type": "boolean", "default": True,
                           "description": "сверить отпечатки входа соседей (только метаданные)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_routes",
        "description": "HTTP-маршруты и точки входа с обработчиками.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_live_read",
        "description": (
            "Чтение АКТУАЛЬНОГО файла с указанием, отличается ли он от индекса. "
            "Обязательно, когда drift_report или stale_warnings затронули этот файл."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_drift_ack",
        "description": (
            "Отметить записи журнала расхождений как учтённые. Записи при этом НЕ удаляются: "
            "история сохраняется."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_ids": {"type": "array", "items": {"type": "integer"}},
                "note": {"type": "string"},
            },
            "required": ["entry_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_schema",
        "description": (
            "СТРУКТУРА ЖИВЫХ БАЗ ДАННЫХ: базы, таблицы, колонки с типами, "
            "индексы, комментарии. Источник — сама СУБД, а не файлы миграций; "
            "для миграций есть отдельный инструмент ragraf_schema. Расхождение "
            "между ними само по себе находка: оно показывает, что миграции и база "
            "разошлись. Колонки, помеченные как персональные данные, присутствуют "
            "в ответе, но их значения не собирались вовсе."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "только эта таблица"},
                "database": {"type": "string", "description": "только эта база"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_table",
        "description": (
            "ОДНА ТАБЛИЦА ПОДРОБНО: колонки с типами, индексы и уникальные "
            "ограничения, ключи, комментарии, связи и список колонок, значения "
            "которых скрыты как персональные данные (с причиной). Вызывать, "
            "когда нужно точно знать устройство таблицы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
                "database": {"type": "string", "description": "уточнение по базе"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_relations",
        "description": (
            "СВЯЗИ ТАБЛИЦЫ, РАЗДЕЛЁННЫЕ ПО ПРОИСХОЖДЕНИЮ. «stated» — объявлено в "
            "базе внешним ключом, это ФАКТ. «derived» — выведено по имени колонки "
            "(например `user_id` → `users`), это ВЫВОД с достоверностью не выше "
            "0,5, и ссылаться на него как на структуру нельзя. В живых базах "
            "внешние ключи часто не объявлены, поэтому вывод — единственный способ "
            "увидеть связи, и он всегда помечен."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_sample",
        "description": (
            "ПРИМЕР СТРОК ТАБЛИЦЫ: сборный пример, где по каждой колонке взято "
            "первое НЕПУСТОЕ значение из просмотренных строк, плюс домены значений "
            "(статусы, типы) с частотами. Значения колонок, признанных "
            "персональными данными, НЕ СОБИРАЮТСЯ: на их месте пометка «скрыто» — "
            "не считайте такое поле пустым. Пример — СНИМОК на момент индексации: "
            "строки меняются, структура нет."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
                "database": {"type": "string", "description": "уточнение по базе"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_drift",
        "description": (
            "ЧТО ИЗМЕНИЛОСЬ В СТРУКТУРЕ БАЗ между ревизиями: добавленные и "
            "удалённые таблицы, колонки, связи. Сравнивается СТРУКТУРА, а не "
            "данные: изменение строк здесь не показывается намеренно. Если "
            "предыдущая ревизия не найдена, ответ это называет — пустой список "
            "изменений тогда ничего не доказывает."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "previous_rev": {"type": "string",
                                 "description": "с чем сравнивать (по умолчанию — предыдущая ревизия)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_cross",
        "description": (
            "СВЕРКА «КОД ↔ БАЗА»: «used_missing» — имя таблицы встречается в коде, "
            "но таблицы НЕТ в базе (признак устаревшего кода или неверного "
            "окружения); «unused» — таблица есть в базе, но код её не касается "
            "(кандидат на удаление). Это статическая сверка по именам: она "
            "показывает, куда смотреть, а не что удалять."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_contract_drift",
        "description": (
            "СВЕРКА ЗАМЫСЛА И КОДА ПО FQN: внешний YAML-манифест проекта "
            "(модули, зависимости, контракты с bind по FQN) сверяется с "
            "вычисленным графом. Находки: module_without_code, "
            "code_without_module (только при strict_code_coverage), unbound_fqn "
            "(метод переименован или удалён), stale_crosslink, "
            "contract_violation (ожидаемая сигнатура или ожидаемые вызовы не "
            "совпали с фактом) и missing_verification. У каждого code-side "
            "расхождения адрес файл:строка, у расхождения на стороне манифеста — "
            "строка манифеста. Манифест пишет сам проект: операция его только "
            "читает и не выдумывает. Нет манифеста — found=false с причиной, это "
            "не ошибка. Сверка статическая: значения во время выполнения не "
            "покрыты, а при capability=degraded проверка вызовов помечена как "
            "неполная."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "manifest_path": {
                    "type": "string",
                    "description": "путь к YAML-манифесту (по умолчанию "
                                   "contracts.yaml в корне корпуса)",
                },
                "source": {
                    "type": "string",
                    "enum": ["manifest", "anchors", "both"],
                    "default": "manifest",
                    "description": "откуда берётся замысел: только манифест, "
                                   "только якоря в комментариях или оба (манифест главнее)",
                },
            },
            "additionalProperties": False,
        },
    },
    # --- хранилище секретов: значения под правом и с аудитом ---
    {
        "name": "ragraf_secret_list",
        "description": (
            "Список секретов (ПАРОЛИ, ТОКЕНЫ, КЛЮЧИ, СЕРТИФИКАТЫ, СТРОКИ "
            "ПОДКЛЮЧЕНИЯ): имя, тип, маска, кому доступен, кто и когда добавил. "
            "Значений НЕ возвращает. Показывает только те записи, которые "
            "разрешены этому агенту."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "поиск по имени и метаданным"},
                "kind": {"type": "string", "description": "фильтр по типу"},
                "tag": {"type": "string", "description": "фильтр по метке"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_show",
        "description": (
            "Метаданные одного секрета и МАСКА значения: тип, срок, кто добавил, "
            "сколько раз выдавался. Значения не раскрывает — для значения есть "
            "ragraf_secret_reveal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_reveal",
        "description": (
            "ВЫДАТЬ ЗНАЧЕНИЕ секрета. Право проверяется по записи (всем / только "
            "перечисленным агентам / только автору) и по общему списку "
            "разрешённых агентов. Каждая выдача и каждый отказ пишутся в аудит. "
            "Требуется причина (reason), если это включено настройкой. "
            "ВАЖНО: значение нельзя печатать в чат без необходимости."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "reason": {"type": "string",
                           "description": "зачем нужно значение (попадает в аудит)"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_set",
        "description": (
            "СОХРАНИТЬ секрет (пароль, токен, ключ, сертификат, строку "
            "подключения). Значение шифруется на диске; в ответе только маска. "
            "Права: visibility=all (по умолчанию, доступен всем агентам), "
            "listed с agents=[...] (только перечисленным), owner_only. Флаг "
            "captured=true помечает сохранение ВСТРЕЧЕННОГО секрета и требует "
            "включённой настройки secrets_capture_enabled."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "понятное имя: сервис/что"},
                "value": {"type": "string"},
                "kind": {"type": "string", "default": "password"},
                "service": {"type": "string"},
                "label": {"type": "string"},
                "owner": {"type": "string"},
                "note": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "project": {"type": "string"},
                "visibility": {"type": "string",
                               "enum": ["all", "listed", "owner_only"]},
                "agents": {"type": "array", "items": {"type": "string"}},
                "expires_at": {"type": "string"},
                "captured": {"type": "boolean"},
            },
            "required": ["name", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_grant",
        "description": (
            "ВЫДАТЬ ДОСТУП к секрету конкретным агентам (agents=[...]) или "
            "сменить режим видимости: all — всем, listed — только перечисленным, "
            "owner_only — только сохранившему. Менять права может автор записи "
            "или локальный администратор."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "agents": {"type": "array", "items": {"type": "string"}},
                "visibility": {"type": "string",
                               "enum": ["all", "listed", "owner_only"]},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_revoke",
        "description": (
            "ЗАБРАТЬ ДОСТУП у конкретных агентов. Если в списке не остаётся "
            "никого, запись переходит в режим owner_only: пустой список "
            "«только перечисленным» читался бы как «никому», и это было бы "
            "неотличимо от ошибки настройки."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "agents": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "agents"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_policy",
        "description": (
            "ПРАВИЛА работы с секретами для этого агента: включено ли "
            "хранилище, надо ли сохранять ВСТРЕЧЕННЫЕ секреты, какова "
            "видимость по умолчанию, какие типы поддерживаются, требуется ли "
            "причина выдачи. Читается перед задачей, где могут встретиться "
            "пароли и ключи."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_secret_delete",
        "description": (
            "УДАЛИТЬ секрет. Требует прав автора записи или локального "
            "администратора. Удаление фиксируется в аудите; значение при этом "
            "не восстанавливается, поэтому перед удалением делается снимок "
            "(secrets export)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------------------
# Классификация инструментов для клиента: `title` и `annotations`.
#
# Зачем. Клиент по протоколу MCP различает «прочитать» и «изменить» ТОЛЬКО по
# полям `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
# `openWorldHint`). Пока их не было, для клиента все инструменты выглядели
# одинаково, и он не мог построить политику подтверждений: `ragraf_ensure`
# пересобирает индекс (22 с работы и запись ревизии), но снаружи был неотличим
# от `ragraf_search`. Замерено в сессии HARNESS 13.10.2026.
#
# Таблица отдельная, а не поля в каждом описании, по двум причинам:
#   * одно место правки, и весь набор виден сразу — пропущенный инструмент
#     ловится проверкой `_apply_tool_meta`, а не глазами;
#   * описания инструментов менять нельзя (на имена и схемы завязаны правило
#     `AGENTS.md` §6, навык ragraf-core, AutoClaw и SagaAI), а `annotations` —
#     аддитивное поле.
#
# Что означает каждое поле применительно к RAGRAF:
#   * `readOnlyHint` — не меняет ни индекс, ни состояние сервера. Запись
#     телеметрии о самом вызове сюда не считается: это не данные владельца,
#     и её делают все операции одинаково.
#   * `destructiveHint` — ничего не удаляет и не перезаписывает безвозвратно.
#     Такого нет ни у одного инструмента: `ensure` пересобирает индекс из кода,
#     `drift_ack` только помечает записи (журнал append-only).
#   * `idempotentHint` — повтор с теми же аргументами даёт то же состояние.
#   * `openWorldHint` — выходит за пределы индекса: файловая система, сеть
#     (модель эмбеддингов), внешние СУБД.
# ---------------------------------------------------------------------------
_TOOL_META: dict[str, dict[str, Any]] = {
    "ragraf_doc_search": {
        "title": "Поиск по документам с адресом и провенансом",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_doc_asof": {
        "title": "Поиск по документам, действительным на дату, с интервалом и редакцией",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_dependents": {
        "title": "Зависимые факты: что опиралось на утверждение и что пересчитать",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_stale_cascade": {
        "title": "Что пересчитать после изменения входов: каскад по рёбрам "
                 "факт → факт",
        "readOnlyHint": False, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
    "ragraf_entity_neighbors": {
        "title": "Соседи сущности по документному графу, с адресами цитат",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_doc_relations": {
        "title": "Связи документов через общие сущности, с адресами с двух сторон",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_themes": {
        "title": "Темы документного графа: кластеры, участники с адресами, сводки",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_global_overview": {
        "title": "Карта документного графа: состав и главные темы с цитатами",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_doc_search_levels": {
        "title": "Dual-level поиск: чанки с адресами и сущности графа со связями",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False,
    },
    "ragraf_doc_get": {
        "title": "Текст документа по адресу (индекс или диск)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_doc_status": {
        "title": "Состояние документного слоя: статусы и причины",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_doc_ocr": {
        "title": "Состояние OCR: включено ли, есть ли движок",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_doc_validate": {
        "title": "Валидатор чанкинга документов",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_index_status": {
        "title": "Состояние индекса: ревизия, полнота, возможности",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_context": {
        "title": "Какой корпус выбран: имя, корень, ревизия, свежесть",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_drift_report": {
        "title": "Журнал расхождений индекса и кода",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_sso_login": {
        "title": "Единый вход: проверка id_token и роли из групп каталога",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
        "openWorldHint": True,
    },
    "ragraf_sso_map_roles": {
        "title": "Отображение групп каталога на роли панели",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_siem_export": {
        "title": "Выгрузка журналов во внешнюю SIEM (только метаданные)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
        "openWorldHint": True,
    },
    "ragraf_siem_test": {
        "title": "Пробная доставка в приёмник SIEM",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
        "openWorldHint": True,
    },
    "ragraf_license_offline": {
        "title": "Офлайн-лицензия: заявка, выпуск, приёмка, перенос",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
    },
    "ragraf_attest_bundle": {
        "title": "Аттестация поставки: подписанный набор доказательств и его проверка",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
    },
    "ragraf_broker_issue": {
        "title": "Брокер секретов: аренда со сроком и распорядителем",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
        "openWorldHint": False,
    },
    "ragraf_broker_rotate": {
        "title": "Брокер секретов: план и смена значения",
        "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
        "openWorldHint": False,
    },
    "ragraf_contract_pin_status": {
        "title": "Пиннинг подписи операций: что подтверждено и что разошлось",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_contract_pin_approve": {
        "title": "Подтвердить текущее описание операции (пин на релиз)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_preflight": {
        "title": "Цена и границы до старта: время, слепые зоны, готовность",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_quality_suite": {
        "title": "Набор проверок индекса и запись прогона",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_quality_diff": {
        "title": "Расхождения между двумя прогонами качества",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_quality_pair": {
        "title": "Парный прогон по фиксированным кейсам (регрессия видна сразу)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_quality_trace": {
        "title": "Трасса подтверждённого сбоя: запись, список и повтор",
        "readOnlyHint": False, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
    "ragraf_quality_prune": {
        "title": "Прореживание журнала прогонов качества (с копией и подтверждением)",
        "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
    },
    "ragraf_search": {
        "title": "Гибридный поиск по коду и документации",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_search_multi": {
        "title": "Поиск по нескольким запросам с группировкой копий",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_duplicates": {
        "title": "Группы копий файлов и доказательство активности",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_entrypoints": {
        "title": "Точки входа и достижимость файлов",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_dead_code": {
        "title": "Файлы и подпрограммы без ссылок",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_symbol": {
        "title": "Найти символ по имени или полному имени",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_references": {
        "title": "Все места использования символа",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_callers": {
        "title": "Кто вызывает символ",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_edge_evidence": {
        "title": "Рёбра символа и чем они подтверждены",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_diff": {
        "title": "Что изменилось между ревизиями индекса",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_note": {
        "title": "Записать заметку о решении (знание между сессиями)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
    },
    "ragraf_compact": {
        "title": "Политика хранения: план уборки и (по запросу) уборка",
        "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
    },
    "ragraf_excludes": {
        "title": "Профиль шумных каталогов: что исключается и чего это стоит",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_notes": {
        "title": "Прочитать заметки о решениях",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_task_stats": {
        "title": "Отчёт по задаче: обращения к индексу и решения гейта",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_task": {
        "title": "Назвать текущую задачу (RAGRAF_TASK)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
    },
    "ragraf_callees": {
        "title": "Кого вызывает символ",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_impact": {
        "title": "Что затронет правка символа или таблицы",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_slice": {
        "title": "Структурный срез вокруг символа (без тел функций)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_code": {
        "title": "Тела символов по явному запросу",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_repo_map": {
        "title": "Карта репозитория в заданном бюджете токенов",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_schema": {
        "title": "Схема БД из миграций",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_table_usage": {
        "title": "Кто читает и кто пишет таблицу БД",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_policy_trace": {
        "title": "Трассировка решений о правах",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_routes": {
        "title": "HTTP-маршруты и обработчики",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_class_usage": {
        "title": "Классы оформления: определение, применение, расхождения",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_db_schema": {
        "title": "Схема живой базы данных",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_table": {
        "title": "Описание живой таблицы: колонки, ключи, связи",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_relations": {
        "title": "Связи живой таблицы (stated и derived)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_sample": {
        "title": "Пример строк живой таблицы без персональных данных",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_drift": {
        "title": "Изменения структуры живых баз между ревизиями",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_cross": {
        "title": "Сверка «код ↔ база»: какие таблицы упоминает код",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_contract_drift": {
        "title": "Сверка замысла и кода по FQN: модули, контракты, дрейф",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_write_check": {
        "title": "Сверка плана записи со схемой (до SQL)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_usage": {
        "title": "Статистика обращений к индексу",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    # Свежесть может запустить переиндексацию — это не только чтение.
    "ragraf_freshness": {
        "title": "Свежесть индекса (может пересобрать его)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_route": {
        "title": "Маршрут к локальному индексу: где лежит и что отвечает",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_neighbors": {
        "title": "Соседние контуры: готовая реализация того же стека",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_catalog": {
        "title": "Реестр разработок: какие проекты проиндексированы",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    # Переключение корпуса меняет состояние СЕРВЕРА, а не диска.
    "ragraf_use_project": {
        "title": "Выбрать корпус для этого сервера",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_ensure": {
        "title": "Привести корпус в актуальное состояние (пересобрать индекс)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_live_read": {
        "title": "Прочитать актуальный файл и сравнить с индексом",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_drift_ack": {
        "title": "Отметить записи журнала расхождений учтёнными",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },    # Хранилище секретов. `secret_reveal` — чтение по смыслу, но именно оно
    # выдаёт значение, поэтому `idempotentHint` ложен: каждый вызов меняет
    # счётчик и журнал.
    "ragraf_secret_list": {
        "title": "Список секретов (значений не отдаёт)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_secret_show": {
        "title": "Метаданные секрета и маска значения",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_secret_reveal": {
        "title": "Выдать значение секрета (право + аудит)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False,
        "openWorldHint": False,
    },
    "ragraf_secret_set": {
        "title": "Сохранить секрет (шифрование на диске)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_secret_grant": {
        "title": "Выдать доступ к секрету агентам",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_secret_revoke": {
        "title": "Забрать доступ к секрету у агентов",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_secret_delete": {
        "title": "Удалить секрет",
        "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
    },
    "ragraf_secret_policy": {
        "title": "Правила работы с секретами для агента",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },

}


# Схема машинного ответа (`structuredContent`). Она общая для всех операций,
# потому что конверт ответа у них один — это контракт проекта, а не совпадение:
# `op`, `index_rev`, `capability`, `provenance`, `found`, `freshness`,
# `stale_warnings`, `drift`, `corpus`, `result`. Поле `result` намеренно без
# ограничений: у каждой операции своя форма, и описывать её в одном месте
# значило бы обещать больше, чем проверяется.
#
# Зачем это нужно. До сих пор ответ уходил ОДНИМ текстовым блоком (JSON плюс
# шапка), и клиент не мог ничего проверить машиной: ни наличие `stale`, ни
# `capability`. Теперь тот же объект приходит ещё и как `structuredContent`.
#
# Ограничение клиента HARNESS, измеренное по его коду: он принимает схему
# только после своей проверки (`assertSupportedJsonSchema`), иначе молча
# отбрасывает её; а модель всё равно видит ТОЛЬКО текстовые блоки
# (`extractText` склеивает блоки `text`). Поэтому схема намеренно простая —
# без объединений типов вида `["string","null"]`, — а факты по-прежнему
# обязаны лежать в `content[].text`.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"type": "string"},
        "index_rev": {},
        "capability": {"type": "string"},
        "provenance": {"type": "string"},
        "found": {"type": "boolean"},
        "result": {},
        "freshness": {},
        "drift": {},
        "stale_warnings": {"type": "array"},
        "corpus": {"type": "object"},
        "error": {},
        "brief": {"type": "boolean"},
    },
    "required": ["op"],
    "additionalProperties": True,
}

# Общий аргумент всех инструментов: убрать служебную часть ответа.
# `brief` НЕ трогает факты (ссылки `path:line`, найденные символы, строки схемы):
# он убирает книжку учёта — суммы, подробности журнала расхождений, длинные
# хвосты предупреждений. Размер ответа ограничивается не «на глаз», а тем, что
# именно выброшено: список выброшенного виден в поле `brief_omitted`.
_BRIEF_PROP: dict[str, Any] = {
    "type": "boolean",
    "default": False,
    "description": (
        "краткий ответ: убрать служебные поля (суммы контрольных сумм, "
        "подробности журнала расхождений, хвост предупреждений), не трогая "
        "факты. Что именно убрано — перечислено в поле brief_omitted"
    ),
}


def _apply_tool_meta() -> list[str]:
    """Проставить `title`, `annotations`, `outputSchema` и аргумент `brief`.

    Возвращает имена инструментов, оставшихся без классификации. Молча отдать
    инструмент без `annotations` нельзя: клиент тогда не отличит запись от
    чтения, а это ровно тот дефект, ради которого таблица заведена. Поэтому
    неполнота видна и в stderr, и в отпечатке сборки (проверка чекера).
    """
    missing: list[str] = []
    for tool in TOOLS:
        name = str(tool.get("name"))
        meta = _TOOL_META.get(name)
        if meta is None:
            missing.append(name)
            tool.setdefault("title", name)
            tool["annotations"] = {}
        else:
            tool["title"] = meta["title"]
            tool["annotations"] = {
                key: meta[key]
                for key in ("readOnlyHint", "destructiveHint", "idempotentHint",
                            "openWorldHint")
                if key in meta
            }
        # Схема вывода и режим `brief` — общие для всех инструментов, поэтому
        # проставляются здесь, а не в тридцати описаниях: расхождение между
        # описаниями и кодом ловится проверкой, а не глазами.
        tool["outputSchema"] = _OUTPUT_SCHEMA
        props = tool.get("inputSchema", {}).get("properties")
        if isinstance(props, dict):
            props.setdefault("brief", dict(_BRIEF_PROP))
    return missing


UNCLASSIFIED_TOOLS = _apply_tool_meta()


def build_fingerprint() -> str:
    """Короткий отпечаток СБОРКИ сервера: содержимое ВСЕГО пакета + версия.

    Зачем. Измеренное расхождение: в исходнике 30 инструментов, а клиент показал
    24 — живой процесс MCP был старше правки кода, и увидеть это было нечем:
    `serverInfo` нёс только `version` пакета, одинаковую у всех сборок.

    Замер 06.11.2026 показал, что одного файла мало: правки в `watch.py`,
    `catalog.py`, `ensure.py` живой сервер не замечал, потому что отпечаток
    считался по `mcp_server.py`. Теперь в него входят ВСЕ модули пакета, а
    `code_drift()` сравнивает отпечаток живого процесса с диском.

    Считается по СОДЕРЖИМОМУ файлов, а не по времени правки: время меняется при
    копировании и не меняется при откате, а содержимое — всегда.
    """
    digest = hashlib.sha256()
    digest.update(__version__.encode("utf-8"))
    package = Path(__file__).resolve().parent
    sources = sorted(package.rglob("*.py"))
    if not sources:
        # Файлы недоступны (запуск без исходников): отпечаток остаётся
        # отпечатком версии, а не выдумкой.
        digest.update(b"no-source")
        return digest.hexdigest()[:12]
    for path in sources:
        try:
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"unreadable")
    return digest.hexdigest()[:12]


def _note_contact(agent: str, method: str, tool: str = "",
                  client: str = "") -> None:
    """Записать след обращения клиента.

    `client` — имя из `clientInfo` запроса `initialize`: оно важнее
    параметра запуска, потому что HTTP-сервис поднимается с
    `--agent AutoClaw`.

    Панель показывает по этим следам состояние подключения: сервис поднят, но
    обращений нет (жёлтое), обращение было (зелёное). Сбой учёта не должен
    ломать сервер — поэтому исключение гасится здесь, а не в обработчике.
    """
    try:
        contact_mod.record(agent, method, tool, client_name=client)
    except Exception:
        pass


def _config_stamp(path: str | None) -> tuple[int, int] | None:
    """Отпечаток файла конфигурации: время правки и размер.

    Нужен долгоживущему процессу (MCP): по такому отпечатку видно, что файл
    правили после запуска. Не читаем файл целиком и не считаем хеш: цена
    такой проверки — один `stat`, а выигрыш — правка исключений и порогов
    начинает действовать без перезапуска сервера (замер 05.11.2026: без неё
    переиндексация от сервера дала 979 файлов вместо 927).
    """
    if not path:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (int(info.st_mtime_ns), int(info.st_size))


class McpServer:
    """Обработчик JSON-RPC: чтение строк из stdin, запись ответов в stdout.

    Учёт агента. Сервер должен отвечать на вопрос владельца «какой агент
    использовал RAGRAF». Имя берётся из двух источников, в порядке
    надёжности:

      1. параметр запуска `--agent` (и `--agent-kind`) — задаёт владелец
         в конфигурации MCP, и он же может дать понятное имя;
      2. поле `clientInfo.name` из запроса `initialize` — по протоколу MCP
         клиент обязан себя назвать.

    Раньше не использовалось НИ ОДНО из них: фасад создавался без указания
    себя, и все вызовы агентов попадали в журнал как `unknown` (замер:
    113 из 138 вызовов в корпусе z). Из-за этого фильтрация статистики
    по агенту была невозможна.
    """

    def __init__(self, config_path: str | None, agent: str = "unknown",
                 agent_kind: str = "unknown",
                 access_label: str = "",
                 corpus_source: str = "",
                 watch: bool = True,
                 access_scopes: list[str] | None = None) -> None:
        self.config_path = config_path
        self.cfg: ProjectConfig | None = None
        # Отпечаток файла конфигурации: по нему видно, что конфиг правили
        # после запуска, и кэш перечитывается (см. `config()`).
        self._cfg_stamp: tuple[int, int] | None = None
        self._facade: Facade | None = None
        # Имя из параметров запуска: считается приоритетным, потому что
        # задано владельцем явно и не зависит от клиента.
        self.agent_arg = (agent or "").strip() or "unknown"
        self.agent_kind_arg = (agent_kind or "").strip() or "unknown"
        # ACCESS-LABEL-PATCH: метка выданного доступа. Пусто — вызов без доступа
        # (локальный CLI, тесты). Значение доступа здесь НЕ хранится:
        # серверу нужна только метка для статистики.
        self.access_label = (access_label or "").strip()
        # Уровни выданного доступа (этап 14). `None` — уровень не объявлен:
        # так приходит локальный stdio-вызов, CLI и тесты, и для них проверка
        # прав не применяется (они и так работают от владельца). Пустой список
        # — объявлен и пуст: это отказ на любом уровне.
        self.access_scopes: list[str] | None = (
            None if access_scopes is None else [str(s) for s in access_scopes])
        # Метка задачи (P0-4): `RAGRAF_TASK` выставляет вызывающий агент на ход
        # работы. Без неё журнал обращений не отвечает на вопрос «на какую
        # задачу ушёл индекс» — все записи из HARNESS шли как «(без задачи)».
        # Читается при создании сервера: переменная окружения — свойство
        # ПРОЦЕССА (как и метка доступа), а не отдельного вызова.
        self.task = (os.environ.get("RAGRAF_TASK") or "").strip()
        # Имя, полученное от клиента в initialize.
        self.agent_client = "unknown"
        self.agent_kind_client = "unknown"
        # Откуда взялся корпус (B1): "arg" — задан владельцем в конфигурации
        # клиента, "auto" — найден по рабочему каталогу процесса, "none" — не
        # выбран вовсе. Это НЕ украшение ответа: конфигурация MCP-клиента задаёт
        # рабочий каталог дочернего процесса (`cwd`), поэтому при "auto" сервер
        # видит каталог СВОЕГО процесса, а не проект сессии. Замерено в HARNESS:
        # `cwd: RAGRAF`, а задача была о другом проекте — факты о
        # чужом корпусе приходили молча. Теперь источник корпуса виден в каждом
        # ответе, а неподтверждённый корпус называется явно.
        self.corpus_source = (
            (corpus_source or "").strip()
            or ("arg" if (config_path or "").strip() else "none")
        )
        # Имя разработки в реестре для выбранного корпуса: считается один раз
        # (это чтение реестра), а нужно в каждом ответе.
        self._category_cache: str | None = None
        # Сторож свежести в фасаде. True — рабочее поведение: перед работой
        # сверяются суммы и при расхождении индекс пересобирается. False нужен
        # проверкам, которые обязаны быть воспроизводимыми и не запускать
        # индексацию (она занимает десятки секунд и пишет ревизию).
        self.watch_enabled = bool(watch)
        # Корни (roots), если клиент их пришлёт. HARNESS объявляет
        # `capabilities: {}`, поэтому сейчас их не бывает; читать их всё равно
        # надо — клиент, который их отдаёт, решает B1 сам, без угадывания.
        self.roots: list[str] = []
        self.corpus_mismatch: list[str] = []
        # Отпечаток сборки: различить устаревший живой процесс и текущий файл.
        self.build = build_fingerprint()
        # Фоновая переиндексация: идентификатор запроса, отменившего её, и
        # признак того, что отмена запрошена. Нужны для `notifications/cancelled`.
        self._cancelled_reindex: str = ""

    # ---------------- состояние ----------------

    @property
    def agent(self) -> str:
        """Действующее имя агента: параметр запуска важнее clientInfo."""
        if self.agent_arg != "unknown":
            return self.agent_arg
        return self.agent_client

    @property
    def agent_kind(self) -> str:
        """Действующее семейство агента.

        Если владелец не задал явно, семейство ВЫВОДИТСЯ из имени:
        клиенты AutoClaw содержат в clientInfo имя платформы, а всё прочее
        считается внешним агентом. Это нужно, чтобы общая статистика
        «все агенты AutoClaw» работала без ручной настройки каждого клиента.
        """
        if self.agent_kind_arg != "unknown":
            return self.agent_kind_arg
        if self.agent_kind_client != "unknown":
            return self.agent_kind_client
        name = self.agent.lower()
        if "autoclaw" in name or "autoclaw" in name.replace("-", ""):
            return "autoclaw"
        if name == "unknown":
            return "unknown"
        return "external"

    def note_client(self, params: dict) -> None:
        """Запомнить, кто представился в initialize.

        Разбор терпимый: поля может не быть, оно может быть не строкой,
        а имя бывает вида `AutoClaw/2.0` или `autoclaw-desktop`.
        Приводится к короткому виду: отбрасывается всё после разделителя
        версии и лишние пробелы.
        """
        info = params.get("clientInfo")
        if not isinstance(info, dict):
            return
        raw = info.get("name")
        if not isinstance(raw, str) or not raw.strip():
            return
        name = raw.strip()
        # Отбрасывается только хвост ВЕРСИИ: `AutoClaw/1.2.3` → `AutoClaw`.
        #
        # По дефису и пробелу резать НЕЛЬЗЯ: первая версия делала это и
        # превращала `autoclaw-desktop` в `autoclaw`, а `Claude Desktop`
        # в `Claude` — то есть теряла часть имени клиента, по которой
        # владелец мог хотеть различать агентов.
        short = name.split("/", 1)[0].strip()
        self.agent_client = short or "unknown"

    def _reset_facade(self) -> None:
        """Забыть текущий фасад (закрыв его), чтобы следующий вызов создал новый.

        Нужно и при смене корпуса, и после реиндексации: фасад держит дескриптор
        файла графа и кэш ревизии, поэтому без пересоздания следующие операции
        отдавали бы факты прежней ревизии — тот самый дефект, ради которого
        заведён сторож.
        """
        try:
            self.close()
        except Exception:  # noqa: BLE001 — закрытие не должно мешать ответу
            pass
        # Имя разработки для поля `corpus` берётся из реестра и относится к
        # прежнему корпусу — после смены его надо посчитать заново.
        self._category_cache = None

    def set_project(self, config_path: str) -> dict:
        """Переключить сервер на другой корпус, возвратив состояние нового.

        Зачем. У клиента может быть ОДИН долгоживущий процесс сервера на все
        проекты (так делает DeepSeek Harness для профиля): тогда корпус
        выбирается по задаче, а не при запуске. Смена обязана быть видимой:
        в ответе идёт фактическая ревизия и режим нового корпуса — иначе
        агент работал бы по фактам прежнего и не знал об этом.
        """
        self._reset_facade()
        self.config_path = config_path
        self.cfg = None
        # Корпус выбран ЯВНО — это подтверждение, а не догадка по рабочему
        # каталогу процесса (B1). Именно после этого ответы перестают быть
        # «фактами о чужом проекте»: агент назвал проект сам.
        self.corpus_source = "use_project"
        self._category_cache = None
        self._note_corpus_mismatch()
        # Фасад создаётся ленивым свойством уже на новом конфиге; свежесть
        # считается принудительно, чтобы не отдать факты по старому кэшу.
        #
        # Переиндексация здесь НЕ запускается, и это решение, а не мелочь.
        # Замер 28.10.2026 (задание И42): после честного выбора активной
        # ревизии вызов `use_project revoap.com` увидел реальное отставание
        # корпуса на 1 ГБ и запустил его пересборку — четыре прерванных
        # ревизии и таймаут вместо ответа. Переключение корпуса — чтение;
        # пересборка ЧУЖОГО корпуса решение владельца, а не следствие
        # запроса. Отставание при этом названо в ответе, а не скрыто.
        env = self.facade().op_freshness(force=True, auto_reindex=False)
        status = self.facade().op_index_status()
        data = dict(status.get("result") or {})
        data["config"] = config_path
        freshness = dict(env.get("result") or {})
        if freshness.get("stale"):
            # Причина названа явно: иначе агент видел бы «отстал» и не знал,
            # что пересборка не идёт по решению, а не по забывчивости.
            freshness["reindex_skipped"] = (
                "переключение корпуса не запускает переиндексацию: "
                "пересборка этого корпуса — решение владельца"
            )
        data["freshness"] = freshness
        return data

    def config(self) -> ProjectConfig:
        """Конфигурация корпуса; перечитывается, если файл изменился.

        ЗАЧЕМ ПРОВЕРКА. Процесс MCP живёт часами, а `config/<корпус>.yaml`
        правит человек или агент — исключения, пороги, режим CPG. Кэш без
        проверки означал, что правка не действует до перезапуска сервера.
        Замер 05.11.2026: после исключения `**/config/*.yaml` из корпуса RAGRAF
        переиндексация, запущенная ЭТИМ сервером, построила ревизию с ПРЕЖНИМ
        списком — 979 файлов вместо 927, и активной стала ревизия,
        противоречащая файлу конфигурации. Цена проверки — один `stat`, как у
        `rev_info` (`facade.rev_info`), а не полная перечитка на каждый вызов.
        """
        if self.cfg is None:
            if not self.config_path:
                raise ConfigError(
                    "корпус не выбран. Вызовите ragraf_use_project с именем разработки "
                    "(список даёт ragraf_catalog) или с путём к каталогу проекта."
                )
            self.cfg = load_config(self.config_path)
            self._cfg_stamp = _config_stamp(self.config_path)
            return self.cfg
        if _config_stamp(self.config_path) != self._cfg_stamp:
            # Конфигурацию сменили на диске: фасад держит ПРЕЖНЮЮ, поэтому он
            # сбрасывается вместе с кэшем — иначе исключения и пороги были бы
            # старыми в одном месте и новыми в другом.
            self.log(f"конфигурация изменена на диске, перечитываю: {self.config_path}")
            self._reset_facade()
            self.cfg = load_config(self.config_path)
            self._cfg_stamp = _config_stamp(self.config_path)
        return self.cfg

    def facade(self) -> Facade:
        if self._facade is None:
            # Агент передаётся в фасад, иначе вызовы не будут учтены
            # по агентам: именно этого не хватало раньше.
            #
            # Метка задачи (`RAGRAF_TASK`) читается ЗДЕСЬ (P0-4). До этой правки
            # её читал только CLI, а MCP-сервер создавал фасад без `task=`,
            # поэтому весь журнал обращений из HARNESS шёл как «(без задачи)» —
            # по нему нельзя было понять, на какой вопрос ушёл индекс.
            self._facade = Facade(
                self.config(), tool="mcp",
                agent=self.agent, agent_kind=self.agent_kind,
                task=self.task,
                token_label=self.access_label,
                watch=self.watch_enabled,
            )
        return self._facade

    def close(self) -> None:
        if self._facade is not None:
            self._facade.close()
            self._facade = None

    # ---------------- транспорт ----------------

    def send(self, message: dict) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def log(self, text: str) -> None:
        """Диагностика идёт в stderr: stdout занят протоколом."""
        sys.stderr.write(f"[ragraf-mcp] {text}\n")
        sys.stderr.flush()

    def run(self) -> int:
        self.log(f"запуск, версия {__version__}, проект {self.config_path}")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                self.send(self._error(None, -32700, f"не удалось разобрать JSON: {exc}"))
                continue
            response = self.handle(request)
            if response is not None:
                self.send(response)
        self.close()
        return 0

    # ---------------- обработка ----------------

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}

        if method in ("notifications/initialized", "initialized"):
            return None
        # Уведомления отмены: клиент сообщает, что запрос больше не нужен.
        # Обрабатывается ровно то, что можно обработать честно: фоновая
        # переиндексация, запущенная этим запросом, не начинается. Уже идущую
        # пересборку рвать нельзя — ревизия пишется в несколько таблиц, и
        # прерывание посередине оставило бы битый индекс (см. `_on_cancelled`).
        if method == "notifications/cancelled":
            return self._on_cancelled(params)
        if method == "notifications/progress":
            # Уведомления о ходе — от клиента к серверу их не бывает; молча
            # принимаем, чтобы не отвечать ошибкой на корректный поток.
            return None
        if method == "initialize":
            # Клиент представляется здесь — имя запоминается для телеметрии.
            # Фасад на этот момент ещё не создан, поэтому агент из clientInfo
            # успевает попасть в журнал с первого же вызова.
            self.note_client(params)
            self.note_roots(params)
            # След подключения: именно это обращение показывает панели, что
            # клиент ДОШЁЛ до RAGRAF, а не только записан в своих настройках.
            _note_contact(self.agent, "initialize",
                          client=str(((params.get("clientInfo") or {})
                                      .get("name")) or ""))
            return self._ok(req_id, self._initialize_result())
        if method == "ping":
            return self._ok(req_id, {})
        if method == "tools/list":
            _note_contact(self.agent, "tools/list")
            return self._ok(req_id, self._tools_list())
        if method == "tools/call":
            _note_contact(self.agent, "tools/call", str(params.get("name") or ""))
            return self._call_tool(req_id, params)
        if method == "resources/list":
            return self._ok(req_id, self._resources_list())
        if method == "resources/templates/list":
            return self._ok(req_id, self._resource_templates_list())
        if method == "resources/read":
            return self._read_resource(req_id, params)
        if method == "prompts/list":
            return self._ok(req_id, self._prompts_list())
        if method == "prompts/get":
            return self._get_prompt(req_id, params)
        return self._error(req_id, -32601, f"метод не поддерживается: {method}")

    # ---------------- initialize, корни, поверхность инструментов ----------------

    def _initialize_result(self) -> dict:
        """Ответ на `initialize`: версия протокола, возможности, отпечаток сборки.

        Три вещи здесь несут смысл, а не заполняют форму:

        * `instructions` — то, что клиент обязан показать агенту. Раньше поля не
          было вовсе, и правило «первым шагом вызови ensure» жило только в
          навыке и в тексте описания инструмента.
        * `serverInfo.build` — отпечаток СОДЕРЖИМОГО файла сервера. Измерено:
          в исходнике 30 инструментов, а клиент показывал 24, потому что живой
          процесс был старше правки кода. Сравнив отпечаток живого процесса с
          `python -c "from ragraf.mcp_server import build_fingerprint; ..."`,
          расхождение видно сразу, а не выясняется догадкой.
        * `serverInfo.tools` — сколько инструментов сервер отдал ФАКТИЧЕСКИ
          (после сверки состава с обработчиками), а не сколько их в списке.
        """
        listed = self._tools_list()
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                # `listChanged: False` — честно: состав инструментов статичен,
                # уведомлений об изменении списка сервер не шлёт. Клиент узнаёт
                # о смене сборки по отпечатку, а не по обещанию, которого нет.
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": __version__,
                "build": self.build,
                "tools": len(listed["tools"]),
            },
            "instructions": INSTRUCTIONS,
        }

    def note_roots(self, params: dict) -> None:
        """Запомнить корни проекта, если клиент их прислал.

        По протоколу корни приходят либо в `initialize` (поле `roots`), либо
        ответом на запрос `roots/list`, который сервер вправе сделать, только
        если клиент объявил возможность `roots`. HARNESS объявляет
        `capabilities: {}`, поэтому там корней не будет — но клиент, который их
        отдаёт, решает B1 сам: корень и есть проект задачи, и его надо
        предпочесть рабочему каталогу процесса.
        """
        raw = params.get("roots")
        if raw is None:
            return
        items = raw.get("roots") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return
        roots: list[str] = []
        for item in items:
            if isinstance(item, dict):
                uri = item.get("uri") or item.get("path")
            else:
                uri = item
            if not isinstance(uri, str) or not uri.strip():
                continue
            path = uri.strip()
            if path.startswith("file://"):
                path = path[7:]
            # Windows-путь в URI приходит как /C:/dir — ведущий слэш отбрасывается,
            # иначе путь не существует ни для одного потребителя.
            if len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            roots.append(path)
        self.roots = roots
        self._note_corpus_mismatch()

    def _note_corpus_mismatch(self) -> None:
        """Сравнить выбранный корпус с корнями клиента и запомнить расхождение.

        Это и есть серверная часть B1: когда клиент называет проект задачи, а
        сервер работает с другим корпусом, подмена обязана быть видна в КАЖДОМ
        ответе. Молчаливая выдача фактов о чужом проекте — не неудобство:
        по таким фактам принимают решения о коде, которого перед агентом нет.
        """
        self.corpus_mismatch = []
        if not self.roots or not self.config_path:
            return
        current = ""
        try:
            current = str(self.config().root)
        except Exception:  # noqa: BLE001 — конфиг может не читаться; это не повод падать
            return
        if not current:
            return
        from pathlib import PurePath

        cur_norm = str(PurePath(current).as_posix()).rstrip("/").lower()
        for root in self.roots:
            try:
                root_norm = str(PurePath(root).as_posix()).rstrip("/").lower()
            except Exception:  # noqa: BLE001
                continue
            if not root_norm:
                continue
            if root_norm == cur_norm or root_norm.startswith(cur_norm + "/"):
                # Корень задачи внутри корпуса — это нормальный случай.
                continue
            self.corpus_mismatch.append(root)

    def _corpus_block(self) -> dict:
        """Компактное состояние корпуса для поля `corpus` каждого ответа."""
        data: dict[str, Any] = {
            "source": self.corpus_source,
            # Подтверждён — значит назван явно (`--project`) или выбран
            # операцией `use_project`; «auto» — угадан по рабочему каталогу
            # процесса и потому подлежит проверке агентом.
            "confirmed": self.corpus_source in ("arg", "use_project"),
        }
        if self.config_path:
            data["config"] = self.config_path
        try:
            from .watch import category_for

            cfg = self.config()
            data["root"] = str(cfg.root)
            # Имя разработки в реестре — не поле конфига: его знает реестр
            # (`watch.category_for`). Считается один раз на корпус: это чтение
            # реестра, а поле нужно в КАЖДОМ ответе.
            if self._category_cache is None:
                self._category_cache = category_for(cfg)
            data["category"] = self._category_cache
        except Exception as exc:  # noqa: BLE001 — корпус может быть не выбран
            data["error"] = f"{type(exc).__name__}"
        if self.roots:
            data["roots_from_client"] = list(self.roots)
        if self.corpus_mismatch:
            data["mismatch"] = list(self.corpus_mismatch)
        if not data["confirmed"]:
            data["how_to_change"] = (
                "вызовите ragraf_context (какой корпус выбран) и ragraf_use_project "
                "(имя разработки из ragraf_catalog или путь к каталогу проекта)"
            )
        return data

    def _tools_list(self) -> dict:
        """Список инструментов, сверенный с ФАКТИЧЕСКИМИ обработчиками.

        Зачем сверка. `TOOLS` — статический список описаний, `_handlers()` —
        словарь исполняемых обработчиков. Пока их состав совпадает, разницы не
        видно; при расхождении клиент получает инструмент, который отвечает
        «неизвестный инструмент», либо теряет работающий, не узнав об этом.
        Поэтому список строится по обработчикам: клиенту уходит только то, что
        действительно исполняется, а расхождение печатается в stderr.
        """
        handlers = set(self._handlers().keys())
        listed = [tool for tool in TOOLS if str(tool.get("name")) in handlers]
        orphan_handlers = sorted(handlers - {str(t.get("name")) for t in TOOLS})
        dropped = sorted({str(t.get("name")) for t in TOOLS} - handlers)
        if dropped or orphan_handlers or UNCLASSIFIED_TOOLS:
            self.log(
                "поверхность инструментов расходится с обработчиками: "
                f"без обработчика {dropped or '—'}, "
                f"без описания {orphan_handlers or '—'}, "
                f"без классификации {sorted(UNCLASSIFIED_TOOLS) or '—'}"
            )
        return {"tools": listed}

    # ---------------- ресурсы (MCP resources) ----------------

    def _resources_list(self) -> dict:
        """Ресурсы: состояние и реестр — без вызова инструмента и без его цены.

        Зачем. Операции `index_status` и `catalog` отвечают на вопрос «с чем я
        работаю», но стоят вызова инструмента: 3,4 КБ и 10,3 КБ контекста
        (замерено). Ресурс — это тот же ответ, который клиент читает ТОЛЬКО
        когда он ему нужен, и он не занимает место в списке инструментов.
        """
        return {
            "resources": [
                {
                    "uri": STATUS_URI,
                    # Имя ресурса без префикса `ragraf_`: имена ресурсов — не
                    # идентификаторы для клиента (адресует URI), а проверка
                    # чекера считает инструменты по образцу `"name": "ragraf_..."`.
                    # Одинаковый образец в двух разных местах делает счёт
                    # недостоверным — то есть правило перестало бы что-либо
                    # доказывать.
                    "name": "status",
                    "title": "Состояние индекса и корпуса",
                    "description": (
                        "Корпус, корень, ревизия, capability, свежесть. "
                        "Замена вызову ragraf_context без его стоимости."
                    ),
                    "mimeType": "application/json",
                },
                {
                    "uri": CATALOG_URI,
                    "name": "catalog",
                    "title": "Реестр разработок",
                    "description": (
                        "Какие проекты проиндексированы, их ревизии и совпадение "
                        "контрольных сумм."
                    ),
                    "mimeType": "application/json",
                },
                {
                    "uri": CORPUS_MAP_URI.format(name="current"),
                    "name": "corpus_map",
                    "title": "Карта текущего корпуса",
                    "description": (
                        "Модули и файлы корпуса с ключевыми символами в бюджете "
                        "токенов. Для другого корпуса — шаблон ragraf://corpus/{name}/map."
                    ),
                    "mimeType": "application/json",
                },
            ]
        }

    def _resource_templates_list(self) -> dict:
        """Шаблоны ресурсов: корпус задаётся именем разработки или путём."""
        return {
            "resourceTemplates": [
                {
                    "uriTemplate": CORPUS_MAP_URI,
                    "name": "corpus_map",
                    "title": "Карта корпуса по имени разработки",
                    "description": (
                        "Карта модулей корпуса. {name} — имя из ragraf_catalog "
                        "(например ragraf) или путь к каталогу проекта."
                    ),
                    "mimeType": "application/json",
                }
            ]
        }

    def _read_resource(self, req_id: Any, params: dict) -> dict:
        """Прочитать ресурс. Неизвестный URI — честная ошибка, а не пустой ответ."""
        uri = str(params.get("uri") or "").strip()
        if uri == STATUS_URI:
            payload = self._op_context({"probe": False})
            return self._ok(req_id, self._resource_contents(uri, payload))
        if uri == CATALOG_URI:
            payload = self.facade().op_catalog()
            return self._ok(req_id, self._resource_contents(uri, payload))
        prefix = CORPUS_MAP_URI_PREFIX
        if uri.startswith(prefix) and uri.endswith("/map"):
            name = uri[len(prefix):-len("/map")].strip("/")
            payload = self._op_corpus_map(name)
            if payload is None:
                return self._error(
                    req_id, -32602,
                    f"корпус не найден: «{name}». Список разработок даёт ресурс {CATALOG_URI}",
                )
            return self._ok(req_id, self._resource_contents(uri, payload))
        return self._error(
            req_id, -32602,
            f"ресурс не поддерживается: {uri}. Доступны {STATUS_URI}, {CATALOG_URI}, "
            f"{CORPUS_MAP_URI.format(name='имя')}",
        )

    def _resource_contents(self, uri: str, payload: dict) -> dict:
        """Ресурс отдаётся и текстом, и структурированно — как и ответ инструмента."""
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ]
        }

    def _op_corpus_map(self, name: str) -> dict | None:
        """Карта КОНКРЕТНОГО корпуса, не переключая сервер.

        Почему не переключением. `use_project` меняет состояние сервера: после
        него все последующие ответы были бы о другом проекте, а клиент читает
        ресурс ради справки. Поэтому для чужого корпуса создаётся ВРЕМЕННЫЙ
        фасад только для чтения (`watch=False`): он не переиндексирует чужой
        проект и не трогает текущий выбор.
        """
        from .config import load as load_config
        from .catalog import Catalog

        target = (name or "").strip()
        if not target or target == "current":
            return self.facade().op_repo_map(budget_tokens=2000)

        config_path: str | None = None
        try:
            catalog = Catalog()
        except Exception:  # noqa: BLE001 — реестр недоступен: остаётся путь
            catalog = None
        if catalog is not None:
            for category, entry in catalog.entries().items():
                if str(category).lower() == target.lower():
                    config_path = str(entry.get("config") or "")
                    break
            if config_path is None:
                try:
                    category = catalog.category_for_root(Path(target))
                except Exception:  # noqa: BLE001
                    category = None
                if category:
                    entry = catalog.get(category)
                    if entry is not None:
                        config_path = str(entry.config)
        if not config_path:
            candidate = Path(target) / ".ragraf" / "config.yaml"
            config_path = str(candidate) if candidate.is_file() else ""
        if not config_path or not Path(config_path).is_file():
            return None

        peer = Facade(load_config(config_path), tool="mcp", agent=self.agent,
                      agent_kind=self.agent_kind, token_label=self.access_label,
            task=os.environ.get("RAGRAF_TASK", "").strip(),
                      watch=False)
        try:
            data = peer.op_repo_map(budget_tokens=2000)
            data.setdefault("corpus", {})
            data["corpus"]["category"] = peer.cfg.category
            data["corpus"]["root"] = str(peer.cfg.root)
            data["corpus"]["source"] = "resource"
            return data
        finally:
            peer.close()

    # ---------------- подсказки (MCP prompts) ----------------

    def _prompts_list(self) -> dict:
        """Готовые запросы к агенту: проверка индекса и восстановление.

        Живут отдельно от правил проекта, потому что формулировка — часть
        контракта, а не пожелание: `/ragraf-check` требует начать с `ensure` и
        назвать ревизию, `/ragraf-repair` — не выдумывать факты, когда индекса нет.
        """
        return {
            "prompts": [
                {
                    "name": "ragraf-check",
                    "title": "Проверить индекс перед работой",
                    "description": (
                        "Сверить индекс с кодом, назвать корпус и ревизию, "
                        "честно сказать про stale."
                    ),
                    "arguments": [
                        {
                            "name": "project",
                            "description": "проект задачи, если он не совпадает с текущим корпусом",
                            "required": False,
                        }
                    ],
                },
                {
                    "name": "ragraf-repair",
                    "title": "Восстановить работу, когда фактов нет",
                    "description": (
                        "Что делать, если инструменты RAGRAF не отвечают или "
                        "корпус не выбран: порядок восстановления без выдумывания фактов."
                    ),
                    "arguments": [],
                },
            ]
        }

    def _get_prompt(self, req_id: Any, params: dict) -> dict:
        """Текст подсказки. Незнакомое имя — ошибка со списком доступных."""
        name = str(params.get("name") or "").strip()
        args = params.get("arguments") or {}
        if name == "ragraf-check":
            project = str(args.get("project") or "").strip()
            extra = ""
            if project:
                extra = (f"\nЕсли окажется, что выбран другой корпус, вызови "
                         f"ragraf_use_project с project={project!r}.")
            return self._ok(req_id, {
                "description": "Проверка индекса перед работой",
                "messages": [{
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Перед выводами о коде сделай ровно это:\n"
                            "1. Вызови ragraf_context — назови корпус, корень и ревизию, "
                            "и скажи, подтверждён ли корпус явно.\n"
                            "2. Вызови ragraf_ensure — приведи индекс в актуальное состояние.\n"
                            "3. Если в ответе stale=true, повтори ragraf_ensure и не делай "
                            "выводов по устаревшим фактам.\n"
                            "4. Дальше отвечай по фактам со ссылкой файл:строка; факт без "
                            "ссылки называй догадкой." + extra
                        ),
                    },
                }],
            })
        if name == "ragraf-repair":
            return self._ok(req_id, {
                "description": "Восстановление работы без выдумывания фактов",
                "messages": [{
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Если инструменты ragraf_* недоступны или отвечают отказом:\n"
                            "1. Вызови ragraf_catalog — какие корпуса вообще есть.\n"
                            "2. Если корпус не выбран, вызови ragraf_use_project с именем "
                            "разработки или путём к каталогу.\n"
                            "3. Если корпуса нет, вызови ragraf_ensure с root — он создаст "
                            "корпус, только если каталог похож на проект, и честно ответит "
                            "not_a_project с причиной.\n"
                            "4. Пока индекс не поднят, НЕ выдавай догадки за факты: скажи "
                            "прямо, что факты не подтверждены индексом."
                        ),
                    },
                }],
            })
        return self._error(
            req_id, -32602,
            f"подсказка не найдена: «{name}». Доступны ragraf-check, ragraf-repair",
        )

    def _on_cancelled(self, params: dict) -> None:
        """Обработать `notifications/cancelled`.

        Что можно отменить честно, а что нельзя. Отменяется ФОНОВАЯ
        переиндексация, ещё не начатая: она помечается и не запускается. Уже
        идущая пересборка НЕ прерывается: ревизия пишется в несколько таблиц
        (graph, coverage, rev_history, реестр), и обрыв посередине оставил бы
        индекс, которому нельзя верить, — то есть отмена навредила бы больше,
        чем помогла. Это ограничение названо прямо, а не спрятано.
        """
        request_id = params.get("requestId")
        self._cancelled_reindex = str(request_id) if request_id is not None else "*"
        self.log(f"запрошена отмена запроса {request_id}: фоновая переиндексация не начнётся")
        return None

    def _call_tool(self, req_id: Any, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}

        # ФТ-15: никаких языков запросов к графу, в том числе через свободный аргумент.
        joined = " ".join(str(v) for v in args.values()
                          if isinstance(v, (str, int, float))).lower()
        for marker in ("match (", "return ", "cpgql", "gremlin", "create (", "merge ("):
            if marker in joined:
                return self._error(
                    req_id, -32602,
                    "язык запросов к графу не поддерживается: используйте фиксированные операции",
                )

        # Права проверяются ДО обработчика и в одной точке: второй такой точки
        # быть не должно, иначе они разойдутся (замер 01.11.2026: уровень
        # доступа записывался в журнал, но нигде не проверялся, поэтому выданный
        # `read` мог вызвать `ragraf_secret_set`).
        allowed, need, why = access_mod.scope_allows(self.access_scopes, str(name or ""))
        if not allowed:
            self.log(f"отказ по правам: {name} (нужен {need}, доступ "
                     f"{self.access_label or 'без метки'})")
            refused = {
                "isError": True,
                "content": [{"type": "text",
                             "text": f"доступ не разрешает {name}: {why}",
                             "зона": "права"}],
                "structuredContent": {"tool": str(name or ""), "refused": True,
                                      "required_scope": need,
                                      "access_label": self.access_label},
            }
            return self._ok(req_id, refused)

        handler = self._handlers().get(name)
        if handler is None:
            return self._ok(
                req_id,
                {"isError": True, "content": [{"type": "text", "text": f"неизвестный инструмент: {name}"}]},
            )
        # Уведомления о ходе отправляются ТОЛЬКО по запросу клиента: у HARNESS
        # незапрошенное уведомление о ходе — ошибка («получено уведомление о
        # ходе для неизвестного токена»), а не подсказка. Замерено по коду
        # клиента 13.10.2026.
        progress_id = (params.get("_meta") or {}).get(PROGRESS_FIELD)
        if progress_id is not None:
            self._notify_progress(progress_id, 0.0, f"начато: {name}")
        try:
            result = handler(args)
        except ConfigError as exc:
            # Корпус ещё не выбран (клиент запустил сервер без --project и не
            # вызывал use_project) либо его конфиг не читается. Это штатная
            # ветвь, а не сбой сервера: агент получает причину и подсказку,
            # а не тип исключения и не мёртвый процесс.
            self.log(f"корпус недоступен для {name}: {exc}")
            return self._ok(
                req_id,
                {"isError": True, "content": [{"type": "text", "text": f"{exc}"}]},
            )
        except Exception as exc:  # ошибка операции не должна ронять сервер
            self.log(f"ошибка {name}: {type(exc).__name__}: {exc}")
            if "--debug" in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return self._ok(
                req_id,
                {
                    "isError": True,
                    # Текст причины идёт в ответ ВМЕСТЕ с именем класса. Раньше
                    # уходило только имя (`OperationalError`), и по ответу нельзя
                    # было ничего ни понять, ни исправить: агент видел
                    # «ошибка операции ensure: OperationalError» и начинал
                    # чинить несуществующую поломку (замер 04.11.2026: в корпусе
                    # не было `.ragraf`, а причина — «индекс не создан» — была
                    # выброшена на этом самом месте).
                    "content": [{"type": "text",
                                 "text": f"ошибка операции {name}: {type(exc).__name__}: {exc}"}],
                },
            )
        if progress_id is not None:
            self._notify_progress(progress_id, 1.0, f"готово: {name}")
        if isinstance(result, dict):
            # Корпус идёт в КАЖДЫЙ ответ: при источнике "auto" он не
            # подтверждён, и агент обязан это видеть, а не догадываться,
            # о каком проекте ему ответили (B1).
            result.setdefault("corpus", self._corpus_block())
            # Единая точка: если свежесть говорит «устарел», предупреждение
            # обязано попасть в `stale_warnings`, а оттуда — в ТЕКСТ ответа.
            # Иначе агент видит «found=True, provenance=index» и не знает, что
            # факты отстали (замер 06.11.2026: `freshness.warning` была, а
            # `stale_warnings` пуст).
            self._warn_if_stale(result)
            if args.get("brief"):
                result = self._brief_view(result)
        return self._ok(req_id, self._content(result))

    # ---------------- инструменты ----------------

    def _handlers(self) -> dict[str, Callable[[dict], dict]]:
        f = self.facade
        return {
            "ragraf_index_status": lambda a: f().op_index_status(),
            # Политика хранения: план по умолчанию, выполнение — только по явному
            # apply=true. Инструмент добавлен после замера 30.10.2026, когда
            # `.ragraf` занимал 26,8 ГБ при 8,8 ГБ данных индекса.
            "ragraf_compact": lambda a: f().op_compact(
                apply=bool(a.get("apply")),
                vacuum=(None if a.get("vacuum") is None else bool(a.get("vacuum"))),
                retain=(int(a["retain"]) if a.get("retain") not in (None, "") else None),
            ),
            # Контекст корпуса: единственная операция, которая обязана отвечать
            # даже когда корпус не выбран вовсе (B1).
            "ragraf_context": lambda a: self._op_context(a),
            "ragraf_drift_report": lambda a: f().op_drift_report(
                since=a.get("since"), limit=a.get("limit", 500)
            ),
            # Внешний доступ (ПРОМТ-И7). Операции издания enterprise: гейт
            # возвращает отказ с причиной раньше, чем начинается работа.
            "ragraf_sso_login": lambda a: f().op_sso_login(
                code=a.get("code", ""), state=a.get("state", ""),
                provider=a.get("provider", ""),
                redirect_uri=a.get("redirect_uri", ""),
            ),
            "ragraf_sso_map_roles": lambda a: f().op_sso_map_roles(
                groups=list(a.get("groups") or [])
            ),
            "ragraf_siem_export": lambda a: f().op_siem_export(
                source=a.get("source", "all"), limit=int(a.get("limit") or 0),
                dry_run=bool(a.get("dry_run")),
            ),
            "ragraf_siem_test": lambda a: f().op_siem_test(),
            "ragraf_doc_search": lambda a: f().op_doc_search(
                a.get("query", ""), limit=int(a.get("limit") or 20),
                kind=a.get("kind"), doc=a.get("doc"),
                with_text=bool(a.get("with_text", True)),
                text_width=int(a.get("text_width") or 240),
            ),
            "ragraf_doc_asof": lambda a: f().op_doc_asof(
                a.get("query", ""), a.get("date", ""),
                limit=int(a.get("limit") or 20), kind=a.get("kind"),
                with_text=bool(a.get("with_text", True)),
                text_width=int(a.get("text_width") or 240),
            ),
            "ragraf_dependents": lambda a: f().op_dependents(
                fact=a.get("fact", ""), locator=a.get("locator", ""),
                depth=int(a.get("depth") or 3),
                include_stated=bool(a.get("include_stated", True)),
            ),
            "ragraf_stale_cascade": lambda a: f().op_stale_cascade(
                changed=str(a.get("changed") or ""),
                depth=int(a.get("depth") or 3),
                apply=bool(a.get("apply", False)),
                clear=bool(a.get("clear", False)),
                limit=int(a.get("limit") or 50),
            ),
            "ragraf_entity_neighbors": lambda a: f().op_entity_neighbors(
                a.get("entity", ""), depth=int(a.get("depth") or 1),
                kind=str(a.get("kind") or "entity"),
                limit=int(a.get("limit") or 50),
            ),
            "ragraf_doc_relations": lambda a: f().op_doc_relations(
                doc=a.get("doc"), entity=a.get("entity"),
                limit=int(a.get("limit") or 50),
            ),
            "ragraf_themes": lambda a: f().op_themes(
                scope=a.get("scope"), limit=int(a.get("limit") or 20),
            ),
            "ragraf_global_overview": lambda a: f().op_global_overview(
                limit=int(a.get("limit") or 20),
            ),
            "ragraf_doc_search_levels": lambda a: f().op_doc_search_levels(
                a.get("query", ""), limit=int(a.get("limit") or 20),
                mode=str(a.get("mode") or "both"),
            ),
            "ragraf_doc_get": lambda a: f().op_doc_get(
                a.get("locator", ""), with_provenance=bool(a.get("with_provenance", True)),
            ),
            "ragraf_doc_status": lambda a: f().op_doc_status(a.get("doc")),
            "ragraf_doc_validate": lambda a: f().op_doc_validate(a.get("doc")),
            "ragraf_doc_ocr": lambda a: f().op_doc_ocr(a.get("path", "")),
            # Офлайн-лицензия и аттестация (ПРОМТ-И8): возможности издания
            # enterprise, поэтому на Community отвечает причиной издания.
            "ragraf_license_offline": lambda a: f().op_license_offline(
                mode=a.get("mode", "accept"), request=a.get("request", ""),
                license=a.get("license", ""), out=a.get("out", ""),
                edition_name=a.get("edition_name", ""), licensee=a.get("licensee", ""),
                days=int(a.get("days") or 365), note=a.get("note", ""),
                public_key=a.get("public_key", ""),
            ),
            "ragraf_attest_bundle": lambda a: f().op_attest_bundle(
                verify=bool(a.get("verify")), out=a.get("out", ""),
                bundle=a.get("bundle", ""), tree=a.get("tree", ""),
                with_sbom=bool(a.get("with_sbom")),
                with_editions=bool(a.get("with_editions", True)),
                gate_report=a.get("gate_report", ""),
                sign=bool(a.get("sign", True)),
            ),
            # Контур качества (ПРОМТ-И9): возможность pro, на Community
            # отвечает причиной издания, а не отсутствием инструмента.
            "ragraf_contract_pin_status": lambda a: f().op_contract_pin_status(
                limit=int(a.get("limit") or 50),
            ),
            "ragraf_contract_pin_approve": lambda a: f().op_contract_pin_approve(
                op=str(a.get("op") or ""),
                everything=bool(a.get("everything") or False),
                by=str(a.get("by") or ""),
                note=str(a.get("note") or ""),
            ),
            "ragraf_preflight": lambda a: f().op_preflight(
                root=str(a.get("root") or ""),
                sample=int(a.get("sample") or 50),
            ),
            "ragraf_quality_suite": lambda a: f().op_quality_suite(
                label=a.get("label", ""),
                record=bool(a.get("record", True)),
            ),
            "ragraf_quality_diff": lambda a: f().op_quality_diff(
                from_run=int(a.get("from_run") or 0),
                to_run=int(a.get("to_run") or 0),
            ),
            # Прореживание журнала прогонов (ПРОМТ-И10). Инструмент помечен
            # destructiveHint: удаление записей необратимо, и клиент обязан
            # спросить человека хотя бы за счёт этой пометки.
            "ragraf_quality_pair": lambda a: f().op_quality_pair(
                from_run=int(a.get("from_run") or 0),
                to_run=int(a.get("to_run") or 0),
            ),
            "ragraf_quality_trace": lambda a: f().op_quality_trace(
                action=str(a.get("action") or "list"),
                case_id=str(a.get("case_id") or ""),
                trace_id=int(a.get("trace_id") or 0),
                session=str(a.get("session") or ""),
                label=str(a.get("label") or ""),
            ),
            "ragraf_quality_prune": lambda a: f().op_quality_prune(
                # Отсутствие `keep` — «из предела журнала»; явный 0 остаётся
                # отказом по причине «удалить всё — не прореживание».
                keep=None if a.get("keep") is None else int(a.get("keep")),
                dry_run=bool(a.get("dry_run", True)),
                confirm=bool(a.get("confirm", False)),
            ),
            "ragraf_search": lambda a: f().op_search(
                a.get("query", ""), limit=a.get("limit", 20),
                with_text=bool(a.get("with_text")),
                text_width=int(a.get("text_width", 160)),
                answer=bool(a.get("answer")),
                mode=str(a.get("mode") or "auto"),
            ),
            # Поиск «одним ответом» и структура корпуса (P1-1, P1-2).
            "ragraf_search_multi": lambda a: f().op_search_multi(
                a.get("queries") or [], limit=a.get("limit", 20),
                scope=a.get("scope"), regex=bool(a.get("regex")),
                answer=bool(a.get("answer")),
            ),
            "ragraf_duplicates": lambda a: f().op_duplicates(
                scope=a.get("scope"), limit=a.get("limit", 100)
            ),
            "ragraf_entrypoints": lambda a: f().op_entrypoints(
                path=a.get("path"), scope=a.get("scope"), limit=a.get("limit", 200)
            ),
            "ragraf_dead_code": lambda a: f().op_dead_code(
                scope=a.get("scope"), limit=a.get("limit", 200)
            ),
            "ragraf_diff": lambda a: f().op_diff(
                since=a.get("since"), limit=a.get("limit", 50)
            ),
            "ragraf_excludes": lambda a: f().op_excludes(
                action=a.get("action", "show"), dirs=a.get("dirs", ""),
                force=bool(a.get("force")),
            ),
            "ragraf_note": lambda a: f().op_note(
                a.get("text", ""), task=a.get("task", ""),
                tags=list(a.get("tags") or []), path=a.get("path", ""),
            ),
            "ragraf_notes": lambda a: f().op_notes(
                task=a.get("task"), query=a.get("query"), limit=a.get("limit", 50)
            ),
            "ragraf_task_stats": lambda a: f().op_task_stats(
                task=a.get("task"), fmt=a.get("format", "markdown"),
                limit=a.get("limit", 10),
            ),
            "ragraf_task": lambda a: f().op_task_set(
                a.get("name", ""), clear=bool(a.get("clear")),
            ),
            "ragraf_symbol": lambda a: f().op_symbol(
                a.get("name", ""), path=a.get("path"), limit=a.get("limit", 20),
                answer=bool(a.get("answer")),
            ),
            "ragraf_references": lambda a: f().op_references(
                a.get("fqn", ""), limit=a.get("limit", 100),
                min_confidence=a.get("min_confidence"),
            ),
            "ragraf_callers": lambda a: f().op_callers(
                a.get("fqn", ""), depth=a.get("depth", 1),
                hard_only=bool(a.get("hard_only")), limit=a.get("limit", 100),
                answer=bool(a.get("answer")),
                min_confidence=a.get("min_confidence"),
            ),
            "ragraf_callees": lambda a: f().op_callees(
                a.get("fqn", ""), depth=a.get("depth", 1),
                hard_only=bool(a.get("hard_only")), limit=a.get("limit", 100),
                min_confidence=a.get("min_confidence"),
            ),
            "ragraf_edge_evidence": lambda a: f().op_edge_evidence(
                a.get("fqn", ""), kind=a.get("kind"), limit=a.get("limit", 200)
            ),
            "ragraf_impact": lambda a: f().op_impact(
                a.get("target", ""), mode=a.get("mode", "full"),
                depth=a.get("depth", 3), limit=a.get("limit", 200),
                answer=bool(a.get("answer")),
                min_confidence=a.get("min_confidence"),
            ),
            "ragraf_slice": lambda a: f().op_slice(
                a.get("fqn", ""), direction=a.get("direction", "back"),
                limit=a.get("limit", 200),
                min_confidence=a.get("min_confidence"),
            ),
            "ragraf_code": lambda a: f().op_code(
                fqn=a.get("fqn"), path=a.get("path"),
                style=a.get("style", "compact"), limit=a.get("limit", 5),
                entry_ids=a.get("entry_ids"),
                start=a.get("start"), end=a.get("end"),
                source=a.get("source", "auto"),
                include_containers=bool(a.get("include_containers")),
            ),
            "ragraf_repo_map": lambda a: f().op_repo_map(
                scope=a.get("scope"), budget_tokens=a.get("budget_tokens", 2000),
                detail=a.get("detail", "normal"),
            ),
            "ragraf_schema": lambda a: f().op_schema(
                table=a.get("table"), limit=a.get("limit", 200)
            ),
            "ragraf_table_usage": lambda a: f().op_table_usage(
                a.get("table", ""), limit=a.get("limit", 200), mode=a.get("mode", "all")
            ),
            # Трассировка решений о правах (U6): точки применения и поведение
            # по умолчанию. Дефект с расширением прав был невидим при обычном чтении
            # кода, потому что лежал в большой ветви switch.
            "ragraf_policy_trace": lambda a: f().op_policy_trace(
                a.get("symbol", ""), action=a.get("action"),
                depth=a.get("depth", 3), limit=a.get("limit", 300),
                min_confidence=a.get("min_confidence"),
            ),
            "ragraf_routes": lambda a: f().op_routes(
                path=a.get("path"), limit=a.get("limit", 200)
            ),
            # Сверка плана записи со схемой (U1): вызов ДО сохранения закрывает
            # ошибки вроде «Unknown column», которые иначе видны только при запуске.
            # Классы оформления: где определён, где использован, что расходится.
            # 32 класса подсистемы agreements использовались в разметке внутри
            # PHP и не имели ни одного правила в стилях — теперь это видно.
            "ragraf_class_usage": lambda a: f().op_class_usage(
                name=a.get("name"), limit=a.get("limit", 200),
                mode=a.get("mode", "all")
            ),
            # Слой живых баз данных: структура, связи, примеры, сверки.
            # Источник — сама СУБД, а не миграции (для миграций есть ragraf_schema).
            "ragraf_db_schema": lambda a: f().op_db_schema(
                table=a.get("table"), database=a.get("database"),
                limit=a.get("limit", 200),
            ),
            "ragraf_db_table": lambda a: f().op_db_table(
                a.get("table", ""), database=a.get("database"),
            ),
            "ragraf_db_relations": lambda a: f().op_db_relations(a.get("table", "")),
            "ragraf_db_sample": lambda a: f().op_db_sample(
                a.get("table", ""), database=a.get("database"),
            ),
            "ragraf_db_drift": lambda a: f().op_db_drift(
                previous_rev=a.get("previous_rev"),
            ),
            "ragraf_db_cross": lambda a: f().op_db_cross(),
            # Сверка замысла и кода по FQN: манифест пишет проект, операция
            # только читает его и индекс. Нет манифеста — found=false, код 0.
            "ragraf_contract_drift": lambda a: f().op_contract_drift(
                manifest_path=a.get("manifest_path"),
                source=str(a.get("source") or "manifest"),
            ),
            "ragraf_write_check": lambda a: f().op_write_check(
                a.get("table", ""),
                list(a.get("columns") or []),
                operation=(a.get("operation") or "insert"),
                values=a.get("values"),
            ),
            "ragraf_usage": lambda a: self._op_usage(a),
            # Свежесть индекса и реестр разработок (сторож контрольных сумм).
            "ragraf_freshness": lambda a: f().op_freshness(force=bool(a.get("force"))),
            "ragraf_catalog": lambda a: f().op_catalog(),
            "ragraf_route": lambda a: f().op_route(
                a.get("query", ""), limit=a.get("limit", 10),
                verify=bool(a.get("verify", True)),
            ),
            "ragraf_neighbors": lambda a: f().op_neighbors(
                query=str(a.get("query") or ""),
                name=str(a.get("name") or ""),
                limit=int(a.get("limit", 20)),
                per_corpus=int(a.get("per_corpus", 5)),
                stack=a.get("stack"),
                min_shared=int(a.get("min_shared", 1)),
                with_text=bool(a.get("with_text", True)),
                verify=bool(a.get("verify", True)),
                kinds=([str(a["kind"])] if a.get("kind") else None),
            ),
            # Приведение корпуса в актуальное состояние: проверяет сумму и
            # пересобирает индекс при расхождении. Вызывается первым шагом.
            "ragraf_use_project": lambda a: self._op_use_project(a),
            "ragraf_ensure": lambda a: self._op_ensure(a),
            "ragraf_live_read": lambda a: f().op_live_read(
                a.get("path", ""), start=a.get("start"), end=a.get("end")
            ),
            "ragraf_drift_ack": lambda a: f().op_drift_ack(
                [int(x) for x in (a.get("entry_ids") or [])], note=a.get("note")
            ),
            # Хранилище секретов: своя точка входа, потому что это не факт
            # индекса и не фасадная операция корпуса.
            "ragraf_secret_list": lambda a: self._op_vault("secret_list", a),
            "ragraf_secret_show": lambda a: self._op_vault("secret_show", a),
            "ragraf_secret_reveal": lambda a: self._op_vault("secret_reveal", a),
            "ragraf_secret_set": lambda a: self._op_vault("secret_set", a),
            "ragraf_secret_grant": lambda a: self._op_vault("secret_grant", a),
            "ragraf_secret_revoke": lambda a: self._op_vault("secret_revoke", a),
            "ragraf_secret_delete": lambda a: self._op_vault("secret_delete", a),
            # Брокер секретов (этап И18): через фасад, потому что гейт издания
            # один и стоит на `op_*`. Значение проходит в ответ и не пишется ни
            # в журнал обращений, ни в журнал доступа.
            "ragraf_broker_issue": lambda a: f().op_broker_issue(
                secret=str(a.get("secret") or ""),
                agent=str(a.get("agent") or ""),
                ttl_minutes=int(a.get("ttl_minutes") or 0),
                reason=str(a.get("reason") or ""),
                reveal=bool(a.get("reveal", True)),
            ),
            "ragraf_broker_rotate": lambda a: f().op_broker_rotate(
                secret=str(a.get("secret") or ""),
                mode=str(a.get("mode") or "status"),
                every_days=int(a.get("every_days") or 0),
                value=str(a.get("value") or ""),
                reason=str(a.get("reason") or ""),
            ),
            "ragraf_secret_policy": lambda a: self._op_vault("secret_policy", a),
        }

    def _op_vault(self, op: str, args: dict) -> dict:
        """Операция хранилища секретов (реализация — ``vault_ops``).

        Зачем тонкая обёртка. Сервер не должен знать про права и шифрование:
        второе место, где решается «кому можно», неизбежно разойдётся с первым.
        Здесь только имя агента (``self.agent``) и передача аргументов.
        """
        from . import vault_ops

        handler = getattr(vault_ops, op, None)
        if handler is None:
            return {"op": f"secret_{op}", "found": False,
                    "error": {"code": "unknown_operation", "message": op}}
        return handler(self, args)

    def _op_usage(self, args: dict) -> dict:
        """Статистика обращений — в общем конверте ответа.

        Зачем обёртка, а не прямой `usage_summary`. Сводка журнала — «сырой»
        словарь (`calls_total`, `by_agent`, `filter`): её читают CLI
        (`cli.py`), админ-панель (`admin_server.py`) и проверки, и менять её
        форму нельзя. Но ответ ИНСТРУМЕНТА обязан нести конверт контракта
        (`op`, `index_rev`, `capability`, `provenance`, `found`): клиент
        проверяет `outputSchema`, где `op` обязателен, и без него отбрасывает
        ответ ЦЕЛИКОМ. Замерено 13.10.2026 в HARNESS: агент вместо данных
        получал «Structured content does not match the tool's output schema:
        data must have required property 'op'», а шапка текстового блока
        печатала `op=None rev=None capability=None provenance=None found=None`.
        Источник данных остаётся один — `usage_summary`.

        `scope=session` отвечает на вопрос «видит ли журнал МОИ вызовы»: до
        этого проверить его было нечем (замер: 12 вызовов за ход не изменили
        сводку) — B7. Фильтры по агенту: агент может спросить «сколько работал
        я» или «сколько работали все агенты AutoClaw».

        Оговорка о смысле числа, чтобы его не прочитали как «число моих
        вызовов»: журнал считает ОПЕРАЦИИ фасада, а не вызовы инструментов.
        `ragraf_context` читает статус и потому виден как `index_status`;
        `ragraf_ensure` и `ragraf_usage` в журнал не пишутся вовсе. Поэтому
        `calls_in_session` не равен числу обращений агента — различие измерено
        и нарочно не «исправляется» подгонкой числа.
        """
        f = self.facade
        env = f().make_env("usage")
        env.result = f().usage_summary(
            since_session=False,
            agent=args.get("agent"),
            agent_kind=args.get("agent_kind"),
            scope=args.get("scope") or "all",
            session_id=args.get("session_id"),
        )
        env.found = True
        return env.to_dict()

    def _op_use_project(self, args: dict) -> dict:
        """Выбрать корпус по имени разработки в реестре или по каталогу проекта.

        Разрешение намеренно неброское: непонятный корпус — это честный отказ
        с подсказкой, а не выбор «похожего» проекта: агент, работающий не с тем
        корпусом, выдаёт чужие факты за свои.
        """
        from .catalog import Catalog
        from . import ensure as ensure_mod

        target = str(args.get("project") or "").strip()
        if not target:
            env = self.facade().make_env("use_project")
            env.found = False
            env.error = "не указан корпус: нужен аргумент project"
            return env.to_dict()

        config_path: str | None = None
        resolved: str | None = None

        # 1. Имя разработки в реестре. Регистр не важен: имя приходит от человека.
        try:
            catalog = Catalog()
            entries = catalog.entries()
        except Exception:  # noqa: BLE001 — реестр недоступен: пробуем путь
            catalog = None
            entries = {}

        for category, entry in entries.items():
            if str(category).lower() == target.lower():
                candidate = Path(entry.get("config", ""))
                if candidate.is_file():
                    config_path = str(candidate)
                    resolved = str(entry.get("root") or category)
                break

        # 2. Путь к каталогу проекта: корпус берётся из реестра по КОРНЮ, а для
        # чужого проекта с непустым `.ragraf` годится и конфиг рядом с корнем.
        if config_path is None:
            root = Path(target)
            if root.is_dir():
                category = None
                if catalog is not None:
                    try:
                        category = catalog.category_for_root(root)
                    except Exception:  # noqa: BLE001
                        category = None
                if category:
                    entry = catalog.get(category)
                    if entry is not None and Path(entry.config).is_file():
                        config_path = str(entry.config)
                        resolved = str(entry.root)
                if config_path is None:
                    outcome = ensure_mod.ensure(root, create=False, index=False)
                    if outcome.config and Path(outcome.config).is_file():
                        config_path = str(outcome.config)
                        resolved = str(outcome.root)

        if config_path is None:
            env = self.facade().make_env("use_project")
            env.found = False
            env.error = (
                f"корпус не найден: «{target}» нет в реестре и это не каталог проекта. "
                "Список разработок даёт операция catalog."
            )
            return env.to_dict()

        data = self.set_project(config_path)
        t0 = time.perf_counter()
        env = self.facade().make_env("use_project")
        env.result = data
        env.found = True
        if resolved:
            env.result["root"] = resolved
        # Операция меняет корпус сервера и тоже живёт вне фасада: без этой
        # записи нельзя было бы увидеть, каким проектом агент работал.
        try:
            self.facade().record_external_call(
                "use_project", env.to_dict(), (time.perf_counter() - t0) * 1000.0
            )
        except Exception:  # noqa: BLE001
            pass
        return env.to_dict()

    def _op_context(self, args: dict) -> dict:
        """Какой корпус выбран, на каком основании и насколько ему можно верить.

        Зачем отдельная операция (B1). Конфигурация MCP-клиента задаёт рабочий
        каталог дочернего процесса (`cwd`), поэтому корпус определяется по
        каталогу ПРОЦЕССА, а не по проекту задачи. Замерено в HARNESS: `cwd`
        указывал на RAGRAF, задача была о другом проекте, и факты о чужом
        репозитории приходили МОЛЧА. Ни один инструмент не отвечал на вопрос
        «о каком проекте мне сейчас отвечают» — теперь отвечает этот.

        Работает и без выбранного корпуса: это первое, что вызывают, когда
        непонятно, с чем работаешь, поэтому отказ здесь был бы вреден.
        """
        probe = bool(args.get("probe", True))
        payload: dict[str, Any] = {
            "op": "context",
            "index_rev": None,
            "capability": "unknown",
            "provenance": "index",
            "drift": {},
            "limits": [],
            "result": {},
            "stale_warnings": [],
            "found": False,
            "corpus": self._corpus_block(),
        }
        payload["corpus_source"] = self.corpus_source
        payload["corpus_confirmed"] = bool(payload["corpus"].get("confirmed"))
        payload["roots_from_client"] = list(self.roots)
        payload["corpus_mismatch"] = list(self.corpus_mismatch)
        # Отпечаток и число инструментов — здесь тоже: расхождение «в исходнике
        # 30, у клиента 24» объясняется устаревшим живым процессом, и увидеть
        # это можно, только имея отпечаток рядом с ответом.
        payload["build"] = self.build
        # КОД НА ДИСКЕ ПРОТИВ КОДА В ПРОЦЕССЕ. Живой сервер держит модули,
        # загруженные при старте, и правка исходников его не меняет — это
        # измерено не раз (см. `build_fingerprint`). Молчать об этом нельзя:
        # агент получает ответ от кода, которого уже нет на диске.
        drift = self.code_drift()
        payload["code_stale"] = bool(drift)
        if drift:
            payload["code_stale_reason"] = drift
        payload["tools"] = len(self._tools_list()["tools"])
        # Издание и его возможности — в конверте контекста тоже: это первое, что
        # вызывают, когда непонятно, с чем работаешь, и «что мне разрешено»
        # относится к тому же вопросу. Набор берётся тем же кодом, что и в поле
        # `edition` остальных ответов, поэтому разойтись с ними не может.
        try:
            from . import edition as edition_mod

            edition_state = edition_mod.resolve()
            payload["edition"] = edition_state.effective
            payload["features"] = list(edition_state.features)
            if edition_state.notes:
                payload["edition_notes"] = list(edition_state.notes)
        except Exception as exc:  # noqa: BLE001 — контекст обязан ответить всегда
            payload["edition"] = "unresolved"
            payload["features"] = []
            payload["edition_notes"] = [
                f"слой изданий не разрешён: {type(exc).__name__}: {exc}"
            ]
        if not self.config_path:
            payload["note"] = (
                "корпус не выбран: список разработок даёт ragraf_catalog, "
                "выбор — ragraf_use_project"
            )
            return payload
        try:
            status = self.facade().op_index_status()
        except Exception as exc:  # noqa: BLE001 — контекст обязан ответить всегда
            payload["error"] = f"{type(exc).__name__}: {exc}"
            return payload
        res = status.get("result") or {}
        payload["found"] = True
        payload["index_rev"] = status.get("index_rev")
        payload["capability"] = status.get("capability")
        payload["drift"] = status.get("drift") or {}
        payload["limits"] = status.get("limits") or []
        payload["stale_warnings"] = list(status.get("stale_warnings") or [])
        payload["freshness"] = status.get("freshness")
        payload["result"] = {
            "project": res.get("project"),
            "root": res.get("root"),
            "rev": (res.get("rev") or {}).get("rev_id"),
            "coverage": res.get("coverage"),
            "vector_available": res.get("vector_available"),
            "embedding_model": res.get("embedding_model"),
        }
        if probe:
            try:
                fresh = self.facade().op_freshness(force=bool(args.get("force")))
                payload["freshness"] = fresh.get("result")
                payload["stale"] = bool((fresh.get("result") or {}).get("stale"))
            except Exception as exc:  # noqa: BLE001
                payload["freshness"] = {"checked": False, "stale": True,
                                        "warning": type(exc).__name__}
                payload["stale"] = True
        # Расхождение корней клиента с выбранным корпусом идёт в те же
        # предупреждения: агент читает их одним полем, а не тремя.
        for root in self.corpus_mismatch:
            payload["stale_warnings"].append({
                "path": root,
                "reason": "корень клиента не входит в выбранный корпус",
                "suggest": "ragraf_use_project",
            })
        self._warn_if_stale(payload)
        return payload

    def code_drift(self) -> str:
        """Расхождение кода: живой процесс собран раньше, чем файлы на диске.

        Сравнение стоит чтения всех модулей пакета, поэтому результат
        запоминается на 30 секунд: ответ не должен платить за проверку на
        каждом вызове, но и молчать полчаса нельзя.
        """
        now = time.time()
        if now - getattr(self, "_code_checked_at", 0.0) < 30.0:
            return getattr(self, "_code_drift", "")
        self._code_checked_at = now
        current = build_fingerprint()
        if current == self.build:
            self._code_drift = ""
            return ""
        self._code_drift = (
            f"процесс работает на коде сборки {self.build}, на диске {current}: "
            "правки исходников не действуют до перезапуска сервера")
        return self._code_drift

    def _warn_if_stale(self, payload: dict) -> None:
        """Поднять устаревание корпуса в `stale_warnings`, а значит и в текст.

        Измеренный дефект 06.11.2026: `freshness` несла `stale: true` и готовую
        причину («индекс старше 295 мин: пересбор стоит ~335 с при бюджете
        120 с»), но `stale_warnings` оставался ПУСТ, а модель видит только
        текстовую сводку — то есть агент получал «found=True, provenance=index»
        и не знал, что факты отстали. Молчание здесь — тот самый «зелёный
        ответ, который ничего не осмотрел».
        """
        fresh = payload.get("freshness")
        # СТАРЫЙ КОД В ЖИВОМ ПРОЦЕССЕ — тем же полем предупреждений.
        # Ответ от кода, которого на диске уже нет, — тот же класс, что и
        # «факты отстали»: агент видит „found=True“ и не знает, что правка
        # не действует до перезапуска.
        drift = self.code_drift()
        if drift:
            payload["code_stale"] = True
            payload["code_stale_reason"] = drift
            warnings = payload.setdefault("stale_warnings", [])
            if not any(isinstance(item, dict) and str(item.get("reason") or "") == drift
                       for item in warnings):
                warnings.append({"path": "", "reason": drift, "suggest": "restart"})
        if not isinstance(fresh, dict):
            return
        # ОГРАНИЧЕНИЕ СВЕРКИ — ТОЖЕ ПРЕДУПРЕЖДЕНИЕ.
        # Замер 06.11.2026: в процессе с отведённым каталогом состояния
        # свежесть НЕ ПРОВЕРЯЕТСЯ (индекс принадлежит другому каталогу), и
        # ответ молчал об этом: модель видела только `found=True`.
        limitation = str(fresh.get("freshness_limitation") or "")
        if limitation and not fresh.get("stale"):
            warnings = payload.setdefault("stale_warnings", [])
            for item in warnings:
                if isinstance(item, dict) and str(item.get("reason") or "") == limitation:
                    return
            warnings.append({"path": "", "reason": limitation,
                             "suggest": "ragraf_ensure"})
            return
        if not fresh.get("stale"):
            return
        reason = str(fresh.get("warning") or fresh.get("note")
                     or "индекс устарел: факты могут не соответствовать коду")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        root = (payload.get("corpus") or {}).get("root") or result.get("root") or ""
        warnings = payload.setdefault("stale_warnings", [])
        for item in warnings:
            if isinstance(item, dict) and str(item.get("reason") or "") == reason:
                return
        warnings.append({"path": root, "reason": reason, "suggest": "ragraf_reindex"})

    def _op_ensure(self, args: dict) -> dict:
        """Привести корпус в актуальное состояние.

        Своей реализации операции у фасада нет: фасад читает готовый индекс,
        а `ensure` может его изменить. Поэтому вызывается модуль `ensure`
        напрямую, а результат заворачивается в тот же конверт, что отдают
        операции фасада — иначе агент получал бы два разных формата ответа
        на одну и ту же задачу «актуален ли индекс».
        """
        from . import ensure as ensure_mod

        f = self.facade
        root = args.get("root")

        if not root:
            # Без аргумента проверяется СВОЙ корпус: сервер привязан к одному
            # конфигу, и это основной случай — «актуален ли индекс проекта,
            # с которым я работаю».
            outcome = ensure_mod.ensure(
                Path(self.config().root),
                create=False,
                index=not args.get("no_index"),
                force_reindex=bool(args.get("force")),
            )
        else:
            outcome = ensure_mod.ensure(
                Path(root),
                create=not args.get("no_create"),
                index=not args.get("no_index"),
                force_reindex=bool(args.get("force")),
            )

        data = outcome.to_dict()
        t0 = time.perf_counter()
        env = f().make_env("ensure")
        env.result = data
        env.found = data["status"] not in ("not_a_project", "error")
        if not env.found:
            env.extra["note"] = data["reason"]
        # После реиндексации фасад пересоздаётся: он держит дескриптор файла
        # графа и кэш ревизии, поэтому без пересоздания следующие операции
        # отдавали бы факты прежней ревизии — тот самый дефект, ради которого
        # заведён сторож.
        if data["status"] in ("reindexed", "created"):
            try:
                self._reset_facade()
                # Фасад создаётся заново ленивым свойством; свежесть берётся уже
                # у нового экземпляра, поэтому в ответе видна новая ревизия.
                env.freshness = self.facade().op_freshness(force=True).get("result")
            except Exception:  # noqa: BLE001 — пересоздание не должно ронять ответ
                pass
        # Журнал статистики: `ensure` — первый шаг работы агента по правилам,
        # поэтому он обязан быть виден в сводке. Фасадная обёртка его не
        # покрывает (операция живёт в сервере), запись идёт явно.
        try:
            self.facade().record_external_call(
                "ensure", env.to_dict(), (time.perf_counter() - t0) * 1000.0
            )
        except Exception:  # noqa: BLE001
            pass
        payload = env.to_dict()
        self._warn_if_stale(payload)
        return payload

    def _notify_progress(self, progress_id: Any, progress: float, message: str) -> None:
        """Уведомление о ходе — ТОЛЬКО по запросу клиента.

        Клиент HARNESS не передаёт `progressToken` (замерено по его коду), а
        незапрошенное уведомление о ходе он превращает в ОШИБКУ «получено
        уведомление о ходе для неизвестного токена». Поэтому механизм
        поддержан, но молчит, пока его не попросят: уведомление без токена
        навредило бы, а не помогло.
        """
        try:
            self.send({
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {PROGRESS_FIELD: progress_id, "progress": progress,
                           "total": 1.0, "message": message},
            })
        except Exception:  # noqa: BLE001 — уведомление не должно ронять вызов
            pass

    def _brief_view(self, data: dict) -> dict:
        """Убрать служебную часть ответа, не трогая факты.

        Что именно убрано — перечислено в поле `brief_omitted`: «короткий
        ответ» не должен превращаться в «ответ, о котором нельзя сказать, что
        из него выбросили». Факты (ссылки `path:line`, найденные символы, строки
        схемы, шаги обхода) не удаляются ни при каком режиме: ради них операция
        и вызывается. Убирается книжка учёта — суммы контрольных сумм,
        подробности журнала расхождений, хвост предупреждений.

        Повод — замеренные размеры: `catalog` ≈ 10,3 КБ, `index_status` ≈ 3,4 КБ
        одним текстовым блоком, а клиент показывает модели ТОЛЬКО текстовые блоки.
        """
        out = dict(data)
        omitted: list[str] = []

        fresh = out.get("freshness")
        if isinstance(fresh, dict):
            keep = ("checked", "stale", "changed_since_index", "index_present",
                    "rev_id", "reindexed", "from_cache", "reindex_mode",
                    "reindex_rev")
            reduced = {k: fresh.get(k) for k in keep if k in fresh}
            dropped = sorted(k for k in fresh if k not in keep)
            if dropped:
                omitted.append("freshness." + ",freshness.".join(dropped))
            out["freshness"] = reduced

        warnings = out.get("stale_warnings")
        if isinstance(warnings, list) and len(warnings) > 3:
            out["stale_warnings"] = warnings[:3]
            out["stale_warnings_omitted"] = len(warnings) - 3
            omitted.append(f"stale_warnings[{3}:]")

        drift = out.get("drift")
        if isinstance(drift, dict):
            keep_drift = ("entries", "files", "harmless")
            reduced_drift = {k: drift.get(k) for k in keep_drift if k in drift}
            if len(reduced_drift) != len(drift):
                omitted.append("drift:подробности")
            out["drift"] = reduced_drift

        res = out.get("result")
        if isinstance(res, dict) and isinstance(res.get("entries"), list):
            # Реестр разработок: строки реестра — не факты о коде. В кратком
            # ответе достаточно состояния каждой разработки.
            res = dict(res)
            total = len(res["entries"])
            if total > 10:
                res["entries"] = res["entries"][:10]
                res["entries_omitted"] = total - 10
                omitted.append(f"result.entries[10:] из {total}")
            out["result"] = res

        corpus = out.get("corpus")
        if isinstance(corpus, dict):
            keep_corpus = ("category", "root", "source", "confirmed", "mismatch")
            out["corpus"] = {k: corpus.get(k) for k in keep_corpus if k in corpus}

        out["brief"] = True
        out["brief_omitted"] = omitted
        return out

    # ---------------- формат ответов ----------------

    def _content(self, data: dict) -> dict:
        """Ответ инструмента: машиночитаемый JSON + короткая сводка для чтения.

        Сводка ставится первой: агент видит ревизию, режим и предупреждения об
        устаревании ДО самих данных, а не после.
        """
        warnings = data.get("stale_warnings") or []
        parts = [
            f"op={data.get('op')} rev={data.get('index_rev')} "
            f"capability={data.get('capability')} provenance={data.get('provenance')} "
            f"found={data.get('found')}"
        ]
        # Корпус — сразу после конверта: при источнике "auto" он определён по
        # рабочему каталогу ПРОЦЕССА, и молчать об этом нельзя (B1).
        corpus = data.get("corpus") or {}
        if corpus:
            parts.append(
                f"КОРПУС: {corpus.get('category') or '?'} "
                f"({corpus.get('root') or '—'}) источник={corpus.get('source')} "
                f"подтверждён={corpus.get('confirmed')}"
            )
            if not corpus.get("confirmed", True):
                parts.append(
                    "ВНИМАНИЕ: корпус определён по рабочему каталогу процесса, а не "
                    "по проекту задачи. Если задача о другом проекте — вызовите "
                    "ragraf_context, затем ragraf_use_project"
                )
        drift = data.get("drift") or {}
        if drift.get("entries"):
            parts.append(
                f"ВНИМАНИЕ: журнал содержит {drift['entries']} записей о расхождениях "
                f"({drift['files']} файлов) — проверьте поля stale_warnings и при "
                "необходимости вызовите ragraf_live_read"
            )
        for w in warnings[:10]:
            parts.append(f"УСТАРЕЛО: {w.get('path')} ({w.get('reason')}) → {w.get('suggest')}")
        for lim in data.get("limits") or []:
            parts.append(f"ОГРАНИЧЕНИЕ: {lim}")
        if data.get("error"):
            parts.append(f"ОШИБКА: {data['error']}")
        if data.get("brief"):
            parts.append(
                "КРАТКО: выброшено "
                + (", ".join(data.get("brief_omitted") or []) or "ничего")
            )
        summary = "\n".join(parts)
        return {
            "content": [
                {"type": "text", "text": summary},
                {"type": "text", "text": json.dumps(data, ensure_ascii=False)},
            ],
            # Машинная копия того же ответа: клиент валидирует её по
            # `outputSchema` и сохраняет. НО модель видит только текстовые блоки
            # (клиент HARNESS склеивает блоки `text`), поэтому текстовые блоки
            # остаются обязательными и структурой не заменяются.
            "structuredContent": data,
        }

    @staticmethod
    def _ok(req_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _resolve_default_project() -> Path | None:
    """Найти конфиг корпуса, когда `--project` не задан.

    Зачем. Клиент может запускать сервер без аргументов — так удобнее
    подключать его к любому проекту одним блоком конфигурации. Тогда корпус
    определяется по рабочему каталогу процесса: сначала реестр разработок
    (корпус ищется по КОРНЮ проекта, а не по каталогу, в котором лежит конфиг),
    затем — сам каталог как корень корпуса.

    Второй ветвь нужна для ИНДЕКСИРУЕМОГО проекта: для него конфиг живёт не
    в конфиге RAGRAF, а рядом с кодом, и в реестр корпус попадает только после
    первого `ensure`/`init`. Если каталог не похож на проект, `ensure` вернёт
    `not_a_project`, и мы честно сообщаем об этом, а не выдумываем корпус.

    Ничего не создаётся и не индексируется: это только поиск конфигурации.
    Создание нового корпуса — отдельное решение владельца (команда `ensure`).
    """
    from .catalog import Catalog
    from . import ensure as ensure_mod

    cwd = Path.cwd()
    try:
        catalog = Catalog()
    except Exception:
        catalog = None

    category = None
    if catalog is not None:
        try:
            category = catalog.category_for_root(cwd)
        except Exception:
            category = None
    if category:
        entry = catalog.get(category)
        if entry is not None and entry.config:
            candidate = Path(entry.config)
            if candidate.is_file():
                return candidate

    ok, _reason = ensure_mod.looks_like_project(cwd)
    if not ok:
        return None

    outcome = ensure_mod.ensure(cwd, create=False, index=False)
    if outcome.config:
        candidate = Path(outcome.config)
        if candidate.is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    # Диагностика (лог в stderr) должна быть UTF-8 до всего остального.
    force_utf8_stdio()

    parser = argparse.ArgumentParser(prog="ragraf-mcp", description="MCP-сервер RAGRAF (stdio)")
    parser.add_argument(
        "--project", "-p",
        # Значение можно передать переменной окружения: у клиента AutoClaw
        # (тип «Local program») нет поля рабочего каталога, а есть переменные
        # окружения — без этого корпус определялся по каталогу процесса и мог
        # не найтись (замер 15.10.2026).
        default=(os.environ.get("RAGRAF_PROJECT")
                 or os.environ.get("RAGRAF_CONFIG") or None),
        help="конфигурация корпуса; если не задана, берётся из RAGRAF_PROJECT, "
             "затем из реестра по рабочему каталогу, а если и там нет — "
             "выбирается операцией use_project",
    )
    # Имя агента: используется для статистики «какой агент использовал RAGRAF».
    # Не обязательно: если не задано, имя берётся из clientInfo в initialize,
    # а затем из переменной окружения (клиент может дать урезанное окружение).
    parser.add_argument(
        "--agent", default=os.environ.get("RAGRAF_AGENT", "unknown"),
        help="имя агента для статистики (например AutoClaw); если не задано, "
             "берётся из clientInfo запроса initialize или из RAGRAF_AGENT",
    )
    parser.add_argument(
        "--agent-kind", default=os.environ.get("RAGRAF_AGENT_KIND", "unknown"),
        help="семейство агента: autoclaw, external или internal; если не задано, "
             "определяется по имени",
    )
    parser.add_argument(
        "--access-label", default=os.environ.get("RAGRAF_ACCESS_LABEL", ""),
        help="метка выданного доступа, которым вызван сервер; попадает "
             "в статистику и отвечает на вопрос «кто и по какому праву работал»",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    project = args.project
    # Источник корпуса различается, потому что цена ошибки разная: `arg` —
    # владелец назвал корпус явно, `auto` — он угадан по рабочему каталогу
    # ПРОЦЕССА сервера, а клиент задаёт этот каталог своей конфигурацией
    # (в HARNESS профиле — `cwd: RAGRAF`). При `auto` корпус
    # неподтверждён, и это видно в каждом ответе (B1).
    source = "arg" if project else "none"
    if not project:
        found = _resolve_default_project()
        if found is not None:
            project = str(found)
            source = "auto"
        else:
            # Не выход с ошибкой: клиент может работать с НЕСКОЛЬКИМИ проектами
            # одним процессом, и корпус тогда выбирается операцией
            # `ragraf_use_project`. Сервер без корпуса отвечает на catalog,
            # context и use_project и честно отказывает остальным операциям,
            # называя причину, — вместо трассировки и мёртвого процесса.
            sys.stderr.write(
                "[ragraf-mcp] корпус не определён по рабочему каталогу: "
                "он будет выбран операцией ragraf_use_project (список — ragraf_catalog)\n"
            )

    if source == "auto":
        sys.stderr.write(
            f"[ragraf-mcp] корпус определён по рабочему каталогу процесса ({project}) "
            "и НЕ подтверждён: если задача о другом проекте, вызовите ragraf_context "
            "и ragraf_use_project\n"
        )

    server = McpServer(project, agent=args.agent,
                       agent_kind=args.agent_kind,
                       access_label=args.access_label,
                       corpus_source=source)
    # Регистрация живого сервера в реестре. Без неё «restart» в ответе —
    # подсказка без команды: штатный перезапуск (`ragraf server restart`) не
    # смог бы найти процесс. Реестр пишется только при известном корпусе:
    # без корпуса писать некуда, и это состояние законно (сервер поднимается
    # без корпуса и ждёт `ragraf_use_project`).
    registered_cfg = None
    if project:
        try:
            registered_cfg = load_config(project)
            from . import server_control
            server_control.register(registered_cfg, os.getpid(),
                                    build=server.build, corpus=str(project))
        except Exception:  # noqa: BLE001 - реестр не должен мешать серверу
            registered_cfg = None
    try:
        return server.run()
    finally:
        if registered_cfg is not None:
            try:
                from . import server_control
                server_control.unregister(registered_cfg, os.getpid())
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
