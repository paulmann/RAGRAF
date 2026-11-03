"""Единственный клиент LLM: провайдер, ключ, отказ, кэш, счётчик вызовов.

Зачем один клиент, а не «где нужно — там и запрос»
    LLM появляется в RAGRAF впервые (план «Агентские интеграции и документный
    граф», часть B). Соблазн написать `urllib` в двух местах (извлечение фактов
    и сводки тем) стоит дорого: у двух копий разойдутся температура, таймаут,
    разбор отказа и — самое опасное — обращение с ключом. Поэтому запрос к
    провайдеру существует ровно здесь; остальные модули зовут `complete()`.

Три состояния, и пустота не одно из них
    План (§3.3) требует различать «LLM не настроен», «LLM недоступен» и
    «локальная модель» — и запрещает выдавать пустой список за «сущностей нет».

        configured     настройка полная, провайдер внешний, ключ берётся из
                       хранилища секретов при первом обращении;
        local          выбран локальный провайдер: ключ не нужен, контракт тот
                       же, отпечаток включает модель;
        unavailable    настройка неполная либо провайдер отказал; ВСЕГДА названа
                       причина и код (`not_configured`, `no_key`, `auth`,
                       `timeout`, `network`, `server`, `rate_limit`,
                       `response`).

    Отказ — исключение `LLMError` с кодом, а не `""`: пустая строка в ответе
    означала бы «модель ничего не сказала», и это неотличимо от «мы не смогли
    спросить». Ровно этот класс дефекта вылечен в живом слое баз
    (`src/ragraf/indexer.py`, `describe_db_layer`).

Ключ: только из хранилища секретов
    Значение ключа НЕ хранится в настройках и НЕ читается из конфига корпуса.
    В настройке лежит ИМЯ записи (`llm_secret_name`), значение выдаёт
    `vault.Vault.reveal()` с причиной, и каждая выдача попадает в аудит.
    Выданное значение живёт в поле клиента до конца его жизни: слой собирается
    тысячами обращений, и одно обращение к хранилищу на прогон — это и меньше
    шума в аудите, и один вход в криптографию вместо тысячи.

    Значение не попадает ни в ответ, ни в текст ошибки, ни в журнал: в запрос
    оно уходит заголовком, а в ответных словарях его нет вовсе. Проверяется
    свойством, а не обещанием (`tests/test_docgraph_foundation.py`).

Кэш ответов
    Таблица `llm_cache(key, model, prompt_version, response, created_at)` в
    хранилище лексики. Ключ — `sha256(нормализованный промпт + вход + модель +
    версия промпта)`, то есть кэш адресуется СОДЕРЖИМЫМ, а не ревизией: тот же
    чанк в следующей ревизии даёт тот же ответ и второго платного вызова не
    делает. Нормализация промпта — только пробельное: у строк убираются хвостовые
    пробелы, пустые строки по краям, и это единственное преобразование, которое
    не меняет смысл инструкции.

    Кэш ограничен по числу строк (`CACHE_MAX_ROWS`): это кэш, а не слой фактов,
    и его рост не должен решать, сколько места займёт индекс. Факты живут в
    таблицах слоя (`docgraph.py`) и переживают чистку кэша.

Температура
    По умолчанию 0.0 (`DEFAULT_TEMPERATURE`) — план требует воспроизводимости:
    прогон по неизменному входу обязан дать тот же слой. Значение можно
    переопределить на один вызов, но оно входит в отпечаток слоя, поэтому смена
    температуры не «улучшает» прежние факты молча, а требует пересбора.

Границы
    Модуль не читает корпус, не пишет слой и не решает, что извлекать. Он умеет
    ровно одно: спросить модель и честно сказать, что произошло.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol

from . import settings as settings_mod
from . import store, util

#: Провайдеры, которые обслуживает клиент. Список закрытый: опечатка вида
#: `open-ai` не должна становиться «новым провайдером», который никто не
#: проверяет, — она обязана получить отказ с причиной.
PROVIDERS: tuple[str, ...] = ("openai", "local")

#: Адрес по умолчанию для каждого провайдера. У локального варианта это
#: OpenAI-совместимая точка Ollama: другой протокол означал бы второй разбор
#: ответа, а второй разбор расходится с первым молча.
DEFAULT_BASE_URL: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "local": "http://127.0.0.1:11434/v1",
}

#: Температура по умолчанию: план требует воспроизводимости ответа.
DEFAULT_TEMPERATURE = 0.0

#: Сколько строк кэша держим. Замер снимать не с чего (кэш новый), поэтому
#: число выбрано по назначению: 20 000 ответов по ~1 КБ — около 20 МБ, то есть
#: меньше процента хранилища лексики, и при этом заметно больше одного прогона
#: корпуса документов.
CACHE_MAX_ROWS = 20000

#: Коды отказа, при которых повтор осмыслен: провайдер не ответил или ответил
#: «занят». Остальные коды (ключ, запрос, формат ответа) повтором не лечатся —
#: повторять их значит платить за тот же отказ.
RETRYABLE: tuple[str, ...] = ("timeout", "network", "server", "rate_limit")

#: Схема кэша. Лежит в хранилище лексики: там же документный слой, и второй
#: файл состояния означал бы вторую точку, которую надо не забыть перенести.
LLM_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS llm_cache (
  key TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_model ON llm_cache(model, prompt_version);
"""


class LLMError(Exception):
    """Отказ клиента. Код — машинный, сообщение — для человека.

    Наследование от `Exception`, а не тихий `return ""`: вызывающий код обязан
    решить, что делать с отказом, и не может «не заметить» его по пустой строке.
    """

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(message or code)
        self.code = str(code or "error")
        self.message = str(message or "")
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}


# ---------------------------------------------------------------------------
# Настройка клиента
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMConfig:
    """Настройка клиента, прочитанная из общих настроек RAGRAF.

    `problems` — то, что не разобралось при чтении (неизвестный провайдер,
    значение таймаута не число). Список, а не исключение: состояние обязано
    быть показано администратору, а не уронено в трассировку панели.
    """

    enabled: bool = False
    provider: str = "openai"
    model: str = ""
    base_url: str = ""
    secret_name: str = ""
    prompt_version: str = ""
    timeout_seconds: float = 60.0
    max_attempts: int = 2
    cache_enabled: bool = True
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def endpoint(self) -> str:
        """Адрес API без хвостового слэша: склейка путей в одном месте."""
        base = (self.base_url or "").strip() or DEFAULT_BASE_URL.get(self.provider, "")
        return base.rstrip("/")

    @property
    def needs_key(self) -> bool:
        """Нужен ли ключ. Локальному провайдеру он не нужен."""
        return self.provider != "local"

    def with_problems(self, *extra: str) -> "LLMConfig":
        return replace(self, problems=tuple(self.problems) + tuple(extra))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "да", "on")


def _as_float(value: Any, default: float) -> tuple[float, str]:
    try:
        return float(value), ""
    except (TypeError, ValueError):
        return default, f"значение {value!r} не число"


def _as_int(value: Any, default: int) -> tuple[int, str]:
    try:
        return int(value), ""
    except (TypeError, ValueError):
        return default, f"значение {value!r} не целое"


def config_from_settings(values: dict[str, Any] | None) -> LLMConfig:
    """Собрать настройку из словаря общих настроек.

    Значения по умолчанию берутся из `settings.DEFAULTS`, поэтому новая
    установка и старый файл настроек ведут себя одинаково.
    """
    data = dict(settings_mod.DEFAULTS)
    data.update(values or {})
    problems: list[str] = []

    provider = str(data.get("llm_provider") or "openai").strip().lower()
    if provider not in PROVIDERS:
        problems.append(
            f"провайдер {provider!r} не поддерживается: допустимы "
            + ", ".join(PROVIDERS)
        )
        provider = "openai"

    timeout, problem = _as_float(data.get("llm_timeout_seconds"), 60.0)
    if problem:
        problems.append(f"llm_timeout_seconds: {problem}")
    attempts, problem = _as_int(data.get("llm_max_attempts"), 2)
    if problem:
        problems.append(f"llm_max_attempts: {problem}")

    return LLMConfig(
        enabled=_as_bool(data.get("llm_enabled"), False),
        provider=provider,
        model=str(data.get("llm_model") or "").strip(),
        base_url=str(data.get("llm_base_url") or "").strip(),
        secret_name=str(data.get("llm_secret_name") or "").strip(),
        prompt_version=str(data.get("llm_prompt_version") or "").strip(),
        timeout_seconds=max(1.0, timeout),
        max_attempts=max(1, attempts),
        cache_enabled=_as_bool(data.get("llm_cache_enabled"), True),
        problems=tuple(problems),
    )


def load_config() -> LLMConfig:
    """Настройка из файла общих настроек (каталог состояния — `paths`)."""
    try:
        values = settings_mod.SettingsStore().load()
    except Exception:  # noqa: BLE001 — файла нет либо он не разбирается
        values = {}
    return config_from_settings(values)


def config_from(values: dict[str, Any] | None) -> LLMConfig:
    """Синоним `config_from_settings` для читателей, которым нужен один вход."""
    return config_from_settings(values)


# ---------------------------------------------------------------------------
# Состояние
# ---------------------------------------------------------------------------


def describe(config: LLMConfig) -> dict[str, Any]:
    """Состояние клиента БЕЗ обращения к сети и к хранилищу секретов.

    Почему без хранилища. Проба наличия ключа — это выдача с записью в аудит;
    вызывать её на каждый `docgraph status` значило бы засорять журнал
    служебными строками. Поэтому здесь называется ИСТОЧНИК ключа, а факт его
    наличия выясняется при первом обращении и приходит кодом (`no_key`).
    """
    endpoint = config.endpoint
    base: dict[str, Any] = {
        "provider": config.provider,
        "model": config.model,
        "base_url": endpoint,
        "prompt_version": config.prompt_version,
        "temperature": DEFAULT_TEMPERATURE,
        "timeout_seconds": config.timeout_seconds,
        "max_attempts": config.max_attempts,
        "cache_enabled": config.cache_enabled,
        "secret_name": config.secret_name or None,
        "key_source": "хранилище секретов RAGRAF (значение в настройках не хранится)",
    }
    if not config.enabled:
        base.update({
            "configured": False,
            "state": "unavailable",
            "code": "not_configured",
            "reason": "LLM не настроен: llm_enabled выключен",
        })
        return base
    if config.problems:
        base.update({
            "configured": False,
            "state": "unavailable",
            "code": "not_configured",
            "reason": "настройка LLM не разбирается: " + "; ".join(config.problems),
        })
        return base
    if not config.model:
        base.update({
            "configured": False,
            "state": "unavailable",
            "code": "not_configured",
            "reason": "LLM не настроен: не задана модель (llm_model)",
        })
        return base
    if config.needs_key and not config.secret_name:
        base.update({
            "configured": False,
            "state": "unavailable",
            "code": "not_configured",
            "reason": ("LLM не настроен: не задано имя секрета с ключом "
                       "(llm_secret_name)"),
        })
        return base
    base.update({
        "configured": True,
        "state": "local" if config.provider == "local" else "configured",
        "code": "",
        "reason": ("локальная модель: ключ не требуется" if config.provider == "local"
                   else "ключ будет выдан хранилищем при первом обращении"),
    })
    return base


def status(config: LLMConfig | None = None) -> dict[str, Any]:
    """Состояние по действующим настройкам."""
    return describe(config if config is not None else load_config())


# ---------------------------------------------------------------------------
# Транспорт
# ---------------------------------------------------------------------------


class Transport(Protocol):
    """Один запрос к провайдеру. Подменяется в проверках без сети."""

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        ...


class HttpTransport:
    """HTTP-транспорт OpenAI-совместимого API на стандартной библиотеке.

    Зависимости нет намеренно: `requests`/`httpx` в поставку не входят, а один
    POST с JSON тянет `urllib`. Таймаут ставится на сокет, то есть он жёсткий:
    «зависший» провайдер не держит прогон бесконечно.

    Ключ приходит в заголовке запроса и НЕ попадает ни в одно сообщение об
    ошибке: текст отказа собирается из кода ответа и тела, а заголовки в него
    не входят.
    """

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(request.get("json") or {}, ensure_ascii=False).encode("utf-8")
        headers = dict(request.get("headers") or {})
        req = urllib.request.Request(
            str(request.get("url") or ""), data=body, headers=headers, method="POST"
        )
        timeout = float(request.get("timeout") or 60.0)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return {
                    "status": int(getattr(response, "status", 200)),
                    "body": response.read().decode("utf-8", "replace"),
                }
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — тело отказа необязательно
                text = ""
            return {"status": int(exc.code), "body": text}
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            code = "timeout" if "timed out" in reason.lower() else "network"
            raise LLMError(code, f"провайдер недоступен: {reason}") from exc
        except TimeoutError as exc:
            raise LLMError("timeout", "провайдер не ответил за отведённое время") from exc
        except OSError as exc:
            raise LLMError("network", f"сбой сети: {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# Кэш
# ---------------------------------------------------------------------------


def normalize_prompt(prompt: str) -> str:
    """Нормализация промпта для ключа кэша: только пробельное.

    Убираются хвостовые пробелы строк, пустые строки и пробелы по краям всего
    текста. Никаких других преобразований: любое «улучшение» текста инструкции
    меняет ответ модели, и кэш начал бы отдавать результат другого вопроса.

    Почему края текста тоже: промпт, собранный склейкой шаблона и подстановки,
    отличается от того же промпта с лишними пробелами только оформлением, а
    платить за него второй раз незачем.
    """
    text = str(prompt or "").replace("\r\n", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()


def cache_key(prompt: str, input_text: str, model: str, prompt_version: str) -> str:
    """Ключ кэша: sha256(нормализованный промпт + вход + модель + версия).

    Разделитель — `\\x1f` (unit separator): он не встречается в тексте, поэтому
    склейка не даёт двух разных входов с одинаковым ключом.
    """
    parts = [
        normalize_prompt(prompt),
        str(input_text or ""),
        str(model or ""),
        str(prompt_version or ""),
    ]
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class ResponseCache:
    """Кэш ответов в хранилище лексики.

    Без `ProjectConfig` кэш не работает (`available == False`): путь к
    хранилищу неизвестен. Это состояние названо, а не спрятано, — иначе
    «повторный вызов не платит» перестало бы быть правдой молча.
    """

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg
        self.available = bool(cfg is not None and getattr(cfg, "lexvec_db", None))
        self._ensured = False

    def _connect(self) -> sqlite3.Connection:
        conn = store.connect(self.cfg.lexvec_db)
        if not self._ensured:
            conn.executescript(LLM_CACHE_DDL)
            self._ensured = True
        return conn

    def get(self, key: str) -> str | None:
        if not self.available:
            return None
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error):
            return None
        try:
            row = conn.execute(
                "SELECT response FROM llm_cache WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        if row is None:
            return None
        return str(row["response"]) if hasattr(row, "keys") else str(row[0])

    def put(self, key: str, model: str, prompt_version: str, response: str) -> None:
        if not self.available:
            return
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error):
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache(key, model, prompt_version, "
                "response, created_at) VALUES(?,?,?,?,?)",
                (key, str(model), str(prompt_version), str(response), util.now_iso()),
            )
            # Ограничение размера — часть работы кэша, а не отдельная уборка:
            # иначе первый же большой корпус оставил бы в файле лексики ответы,
            # которые уже никто не прочтёт.
            rows = int(conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] or 0)
            if rows > CACHE_MAX_ROWS:
                conn.execute(
                    "DELETE FROM llm_cache WHERE key IN ("
                    "SELECT key FROM llm_cache ORDER BY created_at ASC LIMIT ?)",
                    (rows - CACHE_MAX_ROWS,),
                )
            conn.commit()
        except sqlite3.Error:
            # Сбой кэша не имеет права отменять полученный ответ.
            return
        finally:
            conn.close()

    def stats(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "rows": 0,
                    "reason": "путь к хранилищу лексики неизвестен"}
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error) as exc:
            return {"available": False, "rows": 0,
                    "reason": f"хранилище лексики не открылось: {type(exc).__name__}"}
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM llm_cache").fetchone()
        except sqlite3.Error:
            return {"available": False, "rows": 0, "reason": "таблицы кэша нет"}
        finally:
            conn.close()
        return {"available": True, "rows": int(row["c"] if hasattr(row, "keys") else row[0]),
                "limit": CACHE_MAX_ROWS}


# ---------------------------------------------------------------------------
# Клиент
# ---------------------------------------------------------------------------


def parse_response(raw: dict[str, Any]) -> str:
    """Текст ответа из OpenAI-совместимого тела.

    Ошибка формата — `LLMError("response")`, а не пустая строка: «модель
    ответила не тем» и «модель ответила пусто» — разные состояния.
    """
    status_code = int(raw.get("status") or 0)
    body = str(raw.get("body") or "")
    if status_code and status_code >= 400:
        code = "auth" if status_code in (401, 403) else (
            "rate_limit" if status_code == 429 else (
                "server" if status_code >= 500 else "response"))
        raise LLMError(code, f"провайдер ответил HTTP {status_code}",
                       {"status": status_code, "body_head": body[:200]})
    try:
        payload = json.loads(body) if body else {}
    except ValueError as exc:
        raise LLMError("response", "тело ответа не разбирается как JSON",
                       {"body_head": body[:200]}) from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LLMError("response", "в ответе нет поля choices",
                       {"keys": sorted(payload)[:8] if isinstance(payload, dict) else []})
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    text = message.get("content")
    if text is None and isinstance(first.get("text"), str):
        text = first.get("text")
    if not isinstance(text, str):
        raise LLMError("response", "в ответе нет текста сообщения")
    return text


class LLMClient:
    """Клиент одного прогона: настройка, транспорт, кэш, счётчик вызовов.

    `transport` подменяется проверкой: она обязана работать без сети, и это
    единственный способ проверить «второго обращения не было» по счётчику.
    """

    def __init__(self, cfg=None, *, config: LLMConfig | None = None,
                 transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 vault=None, cache: ResponseCache | None = None,
                 temperature: float = DEFAULT_TEMPERATURE) -> None:
        self.cfg = cfg
        self.config = config if config is not None else load_config()
        self.transport = transport or HttpTransport()
        self._vault = vault
        self.cache = cache if cache is not None else ResponseCache(cfg)
        self.temperature = float(temperature)
        #: Сколько обращений к провайдеру сделано ЭТИМ клиентом (попадания в кэш
        #: не считаются: платного вызова не было).
        self.calls = 0
        self._key: str | None = None

    # -- состояние ------------------------------------------------------

    def status(self) -> dict[str, Any]:
        data = describe(self.config)
        data["calls"] = self.calls
        data["cache"] = self.cache.stats()
        return data

    # -- ключ -----------------------------------------------------------

    def _secret(self) -> str:
        """Значение ключа из хранилища секретов. Одно обращение на клиента."""
        if self._key is not None:
            return self._key
        if not self.config.needs_key:
            self._key = ""
            return ""
        name = (self.config.secret_name or "").strip()
        if not name:
            raise LLMError("no_key", "не задано имя секрета с ключом провайдера "
                                     "(llm_secret_name)")
        from . import vault as vault_mod

        store_ = self._vault or vault_mod.Vault()
        try:
            row = store_.reveal(name, reason="обращение к LLM из RAGRAF (docgraph)")
        except vault_mod.VaultError as exc:
            raise LLMError("no_key", f"ключ не выдан хранилищем: {exc.code}",
                           {"secret": name}) from exc
        value = str((row or {}).get("value") or "")
        if not value:
            raise LLMError("no_key", "хранилище выдало пустое значение ключа",
                           {"secret": name})
        self._key = value
        return value

    # -- вызов ----------------------------------------------------------

    def complete(self, prompt: str, *, input_text: str = "", system: str = "",
                 temperature: float | None = None, purpose: str = "") -> dict[str, Any]:
        """Спросить модель. Возвращает текст и признак попадания в кэш.

        Отказ — `LLMError` с кодом. Пустой строки не возвращает никогда.
        """
        state = describe(self.config)
        if not state.get("configured"):
            raise LLMError(str(state.get("code") or "not_configured"),
                           str(state.get("reason") or "LLM не настроен"),
                           {"provider": self.config.provider,
                            "model": self.config.model})

        temp = self.temperature if temperature is None else float(temperature)
        key = cache_key(prompt, input_text, self.config.model, self.config.prompt_version)
        base = {
            "model": self.config.model,
            "prompt_version": self.config.prompt_version,
            "temperature": temp,
            "key": key,
            "purpose": purpose,
        }

        if self.config.cache_enabled:
            hit = self.cache.get(key)
            if hit is not None:
                return dict(base, text=hit, cached=True, calls=self.calls)

        messages: list[dict[str, str]] = []
        if system.strip():
            messages.append({"role": "system", "content": system})
        content = str(prompt or "")
        if input_text:
            content = f"{content}\n\n{input_text}"
        messages.append({"role": "user", "content": content})

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        secret = self._secret()
        if secret:
            # Значение уходит только заголовком и никуда больше.
            headers["Authorization"] = f"Bearer {secret}"

        request = {
            "url": f"{self.config.endpoint}/chat/completions",
            "headers": headers,
            "json": {
                "model": self.config.model,
                "messages": messages,
                "temperature": temp,
                "stream": False,
            },
            "timeout": self.config.timeout_seconds,
        }

        last: LLMError | None = None
        for attempt in range(1, max(1, self.config.max_attempts) + 1):
            self.calls += 1
            try:
                raw = self.transport(request)
                text = parse_response(raw if isinstance(raw, dict) else {})
            except LLMError as exc:
                last = exc
                if exc.code not in RETRYABLE or attempt >= self.config.max_attempts:
                    raise
                # Пауза короткая и постоянная: она нужна, чтобы не бить в
                # занятого провайдера, а не чтобы «подождать подольше».
                time.sleep(min(2.0, 0.5 * attempt))
                continue
            if self.config.cache_enabled:
                self.cache.put(key, self.config.model, self.config.prompt_version, text)
            return dict(base, text=text, cached=False, calls=self.calls)
        raise last or LLMError("error", "вызов не состоялся")
