"""Шифрованное хранилище секретов RAGRAF: значения, права по агентам, аудит.

Зачем этот модуль вообще существует. До 2026-10-24 в проекте действовал
инвариант «секретоносные пути не открываются, значения не хранятся — только
реестр мест» (``AGENTS.md``, раздел «Секреты»). Владелец изменил его решением от
2026-10-24: значения нужны, но обязаны лежать ТОЛЬКО в ``catalog/`` (вне
корпуса), только зашифрованными, и выдаваться агенту по праву с записью
в аудит. Этот модуль — исполнение того решения; правило «не индексировать»
осталось в силе и обеспечивается исключением ``catalog/**`` в конфиге корпуса.

Инварианты (каждый — из измеренного дефекта или из прямого требования):

1. **На диске нет открытого значения.** В базе лежат только ``nonce`` и
   ``ciphertext``. Ключ — отдельный файл (или переменная окружения), никогда
   в базе и никогда в git.
2. **Шифротекст привязан к записи.** Ключ записи выводится из мастер-ключа
   с солью ``id`` и ``person``, а в сам шифротекст вложен контекст
   ``id + name + version``. Перестановка шифротекста между двумя записями
   обязана давать ошибку аутентификации — это проверяется тестом, потому что
   иначе «украл строку — выдал её под чужим именем».
3. **Право решает запись, а не вызывающий.** У каждой записи есть режим
   видимости: ``all`` (по умолчанию — доступен всем агентам), ``listed``
   (только перечисленным) и ``owner_only`` (только тому, кто сохранил).
   Отсутствие списка НЕ означает «никому»: это ровно то, что просил владелец.
4. **Отказ не подтверждает существование.** Агент, которому запись не
   разрешена, получает тот же ответ, что и на несуществующее имя, а причина
   остаётся в аудите. Иначе список имён утекает через отказы.
5. **Аудит append-only.** Журнал нельзя переписать или удалить: это
   обеспечено триггерами базы, а не соглашением.
6. **Значение не попадает ни в ошибку, ни в лог.** Ни один путь этого модуля
   не форматирует значение в текст. Длина — можно, отпечаток — можно.
7. **Деградация вместо падения.** Нет ключа, нет библиотеки, хранилище
   выключено настройкой — операция отвечает понятной причиной с кодом,
   а не исключением наружу и не «пустым успехом».

Границы (честно, чтобы не создать ложного ожидания): модуль НЕ защищает от
процесса, работающего под той же учётной записью, — он вправе прочитать ключ.
Он защищает копию ``catalog/``, бэкап, случайную выдачу индекса и логов,
пользователей без прав администратора и перестановку шифротекста между
записями.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as _random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover - проверяется тестом на живом окружении
    from nacl import secret as _nacl_secret
    from nacl.exceptions import CryptoError as _CryptoError
    HAVE_NACL = True
except Exception:  # noqa: BLE001 - отсутствие библиотеки это состояние, а не сбой
    _nacl_secret = None  # type: ignore[assignment]

    class _CryptoError(Exception):  # type: ignore[no-redef]
        """Заглушка: без PyNaCl шифровать нечем, и это честная причина."""

    HAVE_NACL = False

# ---------------------------------------------------------------------------
# Расположение состояния
# ---------------------------------------------------------------------------

#: Каталог состояния RAGRAF. Он исключён из корпуса (``config/ragraf.yaml``),
#: поэтому значения физически не могут попасть в индекс и в поисковую выдачу.
CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"

#: Имя файла мастер-ключа. Отдельный файл, а не поле в базе: ключ и данные
#: обязаны бэкапиться и защищаться по отдельности.
KEY_FILE_NAME = "secrets.key"

#: Имя файла хранилища.
DB_FILE_NAME = "secrets.sqlite"

#: Переменная окружения с мастер-ключом (base64). Приоритет выше файла: так
#: ключ можно не класть на диск вовсе (сервисный запуск, CI).
MASTER_ENV = "RAGRAF_SECRETS_KEY"

#: Контекст вывода ключа записи. Меняется только вместе с версией формата.
PERSON = b"ragraf-secret-v1"

#: Типы секретов. Список закрытый: тип влияет на подсказки и на проверку
#: срока (сертификат истекает, пароль — нет), поэтому «любая строка» не годится.
KINDS: tuple[str, ...] = (
    "password",             # пароль (учётная запись, сервис, база)
    "token",                # bearer/API-токен, PAT, JWT
    "api_key",              # ключ API без схемы предъявления
    "private_key",          # закрытый ключ (SSH, TLS, GPG)
    "certificate",          # сертификат (публичная часть)
    "connection_string",    # строка подключения целиком (DSN)
    "ssh_key",              # пара/закрытый SSH-ключ с комментарием
    "webhook",              # адрес вебхука с встроенным секретом
    "note",                 # прочий секрет: код восстановления, фраза
    "other",                # всё, что не описали выше
)

#: Режимы видимости записи. ``all`` — по умолчанию, как решил владелец.
VISIBILITIES: tuple[str, ...] = ("all", "listed", "owner_only")

#: Максимальная длина значения по умолчанию (байт). Сертификат цепочки бывает
#: длинным, поэтому по умолчанию 8 КиБ, а не «сколько влезет».
DEFAULT_MAX_VALUE_BYTES = 8192

#: Сколько суток до истечения считается поводом предупредить.
DEFAULT_EXPIRY_WARN_DAYS = 30


class VaultError(Exception):
    """Отказ хранилища с машинным кодом причины.

    Код нужен клиенту: агент по тексту «нельзя» не отличает «нет такого
    секрета» от «нет права», а по коду — отличает, не раскрывая лишнего.
    """

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        self.code = code
        self.message = message or code
        self.detail = detail or {}
        super().__init__(f"{code}: {self.message}")


# ---------------------------------------------------------------------------
# Кто выполняет операцию
# ---------------------------------------------------------------------------


@dataclass
class Actor:
    """Кто обратился к хранилищу.

    ``label`` — имя агента (то, что агент о себе сообщает: ``RAGRAF_AGENT``,
    ``clientInfo.name`` или параметр запуска MCP). Именно по нему работает
    адресное право записи, поэтому имя обязано быть устойчивым: если клиент
    каждый раз называет себя по-новому, ограничение «только этому агенту»
    превратится в «никому».
    """

    label: str = "cli"
    kind: str = "cli"          # autoclaw | external | internal | human | cli
    via: str = "cli"           # mcp | cli | admin
    client: str = ""           # ip или stdio
    task: str = ""             # метка задачи RAGRAF_TASK
    admin: bool = False        # человек за локальной панелью
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "via": self.via,
            "client": self.client,
            "task": self.task,
            "admin": bool(self.admin),
            "params": dict(self.params),
        }


def actor_from_env(via: str = "cli", *, label: str = "", client: str = "",
                   admin: bool = False) -> Actor:
    """Собрать участника из окружения процесса.

    Приоритет тот же, что у статистики RAGRAF: явный параметр, затем
    ``RAGRAF_AGENT``/``RAGRAF_AGENT_KIND``. Имя не выдумывается: если клиент
    себя не назвал, остаётся ``unknown``, и это видно в аудите.
    """
    name = (label or os.environ.get("RAGRAF_AGENT") or "unknown").strip()
    kind = (os.environ.get("RAGRAF_AGENT_KIND") or "").strip()
    if not kind:
        kind = "autoclaw" if "autoclaw" in name.lower() else "external"
    if name.lower() in ("cli", "ragraf-cli"):
        kind = "cli"
    task = (os.environ.get("RAGRAF_TASK") or "").strip()
    return Actor(label=name, kind=kind, via=via, client=client, task=task,
                 admin=bool(admin))


# ---------------------------------------------------------------------------
# Мастер-ключ
# ---------------------------------------------------------------------------


def catalog_dir() -> Path:
    return CATALOG_DIR


def key_path() -> Path:
    return catalog_dir() / KEY_FILE_NAME


def db_path() -> Path:
    return catalog_dir() / DB_FILE_NAME


def _restrict(path: Path) -> None:
    """Сузить права на файл: только текущая учётная запись.

    На Windows выставляется ACL через ``icacls`` (без прав администратора
    доступно только «текущий пользователь»), на POSIX — ``0600``. Сбой
    ужесточения прав не отменяет запись, но виден в отчёте ``check``:
    молча оставить файл читаемым всем нельзя.
    """
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if not user:
                return
            os.system(f'icacls "{path}" /inheritance:r /grant:r "{user}:F" >NUL 2>&1')
        else:
            os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001 - ужесточение прав не должно ломать запись
        pass


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def read_master_key() -> bytes:
    """Прочитать мастер-ключ. Нет ключа — отказ, а не выдуманный ключ.

    Порядок: переменная окружения, затем файл. Ключ НЕ создаётся на лету при
    чтении: иначе опечатка в пути к каталогу молча заводила бы новое пустое
    хранилище, а прежние записи становились бы нечитаемыми.
    """
    if not HAVE_NACL:
        raise VaultError("crypto_missing",
                         "нет библиотеки PyNaCl: шифровать нечем")
    env = (os.environ.get(MASTER_ENV) or "").strip()
    if env:
        try:
            raw = _b64d(env)
        except Exception as exc:  # noqa: BLE001
            raise VaultError("key_invalid",
                             f"{MASTER_ENV} не является base64") from exc
        if len(raw) != 32:
            raise VaultError("key_invalid",
                             f"{MASTER_ENV} должен содержать 32 байта, а не {len(raw)}")
        return raw
    path = key_path()
    if not path.exists():
        raise VaultError("no_key",
                         "мастер-ключ не создан: выполните «ragraf secrets init»")
    try:
        raw = _b64d(path.read_text(encoding="utf-8").strip())
    except Exception as exc:  # noqa: BLE001
        raise VaultError("key_invalid", "файл ключа повреждён") from exc
    if len(raw) != 32:
        raise VaultError("key_invalid",
                         f"файл ключа должен содержать 32 байта, а не {len(raw)}")
    return raw


def init_master_key(force: bool = False) -> dict[str, Any]:
    """Создать мастер-ключ. Существующий не перезаписывается без ``force``.

    Возвращается отпечаток, а не ключ: отпечаток можно печатать и сравнивать,
    ключ — нельзя.
    """
    if not HAVE_NACL:
        raise VaultError("crypto_missing", "нет библиотеки PyNaCl")
    path = key_path()
    existed = path.exists()
    if existed and not force:
        raise VaultError("key_exists",
                         "мастер-ключ уже создан; перезапись — только с --force "
                         "(все записи станут нечитаемыми)")
    if existed and force:
        backup = path.with_suffix(path.suffix + ".bak-" + _stamp())
        backup.write_bytes(path.read_bytes())
        _restrict(backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _random.token_bytes(32)
    path.write_text(_b64e(raw), encoding="utf-8")
    _restrict(path)
    return {"created": True, "replaced": existed, "path": str(path),
            "fingerprint": _fingerprint_of(raw)}


def _fingerprint_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def key_fingerprint() -> str:
    """Отпечаток действующего ключа (для сверки «ключ тот же?»)."""
    return _fingerprint_of(read_master_key())


# ---------------------------------------------------------------------------
# Шифрование записи
# ---------------------------------------------------------------------------


def _salt_of(record_id: str) -> bytes:
    """16 байт соли из идентификатора записи.

    blake2b принимает соль не длиннее 16 байт, а идентификатор хранится как
    32 шестнадцатеричных символа — это те же 16 байт. Не-hex идентификатор
    (совместимость с ручными записями) усекается, а не роняет шифрование.
    """
    raw = str(record_id or "")
    try:
        data = bytes.fromhex(raw)
        if len(data) == 16:
            return data
    except ValueError:
        pass
    return hashlib.sha256(raw.encode("utf-8")).digest()[:16]


def _record_key(master: bytes, record_id: str) -> bytes:
    """Ключ одной записи: мастер-ключ + идентификатор как соль.

    Зачем не шифровать всё одним ключом: украденная строка базы не должна
    подходить к другой строке. Соль из ``id`` делает ключи разными, а
    контекст в теле шифротекста запрещает и перестановку.
    """
    return hashlib.blake2b(key=master, digest_size=32,
                           salt=_salt_of(record_id),
                           person=PERSON).digest()


def _context(record_id: str, name: str, version: int) -> bytes:
    return f"{record_id}\x1f{name}\x1f{version}".encode("utf-8")


def _require_nacl() -> Any:
    """Модуль шифрования либо отказ с понятной причиной.

    ``assert`` здесь не годится: без PyNaCl он падает в ``AssertionError``
    без сообщения, и MCP-обёртка отдаёт «internal» вместо кода
    ``crypto_missing``, который читает агент. Возвращает модуль, чтобы
    вызывающий не трогал ``_nacl_secret`` напрямую.
    """
    if _nacl_secret is None:
        raise VaultError("crypto_missing",
                         "нет библиотеки PyNaCl: шифровать нечем")
    return _nacl_secret


def _encrypt(master: bytes, record_id: str, name: str, version: int,
             value: str) -> tuple[str, str]:
    secret_mod = _require_nacl()
    box = secret_mod.SecretBox(_record_key(master, record_id))
    nonce = _random.token_bytes(secret_mod.SecretBox.NONCE_SIZE)
    payload = _context(record_id, name, version) + b"\x00" + value.encode("utf-8")
    blob = bytes(box.encrypt(payload, nonce))
    # PyNaCl возвращает nonce ВНУТРИ блоба (первые 24 байта). Храним nonce и
    # шифротекст отдельно: иначе при явной передаче nonce он попал бы в
    # шифротекст дважды и проверка подлинности падала бы на своей же записи.
    return _b64e(nonce), _b64e(blob[len(nonce):])


def _decrypt(master: bytes, record_id: str, name: str, version: int,
             nonce_text: str, cipher_text: str) -> str:
    secret_mod = _require_nacl()
    box = secret_mod.SecretBox(_record_key(master, record_id))
    try:
        raw = box.decrypt(_b64d(cipher_text), _b64d(nonce_text))
    except _CryptoError as exc:
        raise VaultError("ciphertext_invalid",
                         "запись не расшифровывается: другой ключ или "
                         "повреждённый шифротекст") from exc
    prefix = _context(record_id, name, version) + b"\x00"
    if not raw.startswith(prefix):
        # Сюда попадает перестановка шифротекста между записями: ключ мог
        # совпасть, а контекст — нет. Это отказ, а не «почти получилось».
        raise VaultError("ciphertext_foreign",
                         "шифротекст принадлежит другой записи")
    return raw[len(prefix):].decode("utf-8")


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _parse_ts(text: str) -> datetime | None:
    if not text:
        return None
    try:
        raw = text.replace("Z", "+00:00")
        moment = datetime.fromisoformat(raw)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment
    except ValueError:
        return None


def _days_left(expires_at: str) -> int | None:
    moment = _parse_ts(expires_at)
    if moment is None:
        return None
    return int((moment - datetime.now(timezone.utc)).total_seconds() // 86400)


def _csv(values: Iterable[str]) -> str:
    seen: list[str] = []
    known: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() not in known:
            known.add(text.lower())
            seen.append(text)
    return ",".join(seen)


def _uncsv(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def _load_settings() -> dict[str, Any]:
    """Настройки хранилища. Ошибка чтения не должна прятать настройку.

    Значения по умолчанию берутся из ``settings.DEFAULTS``: если файл
    настроек повреждён, поведение остаётся предсказуемым, а не «как
    получится».
    """
    from . import settings as settings_mod

    try:
        return settings_mod.SettingsStore().load()
    except Exception:  # noqa: BLE001
        return dict(settings_mod.DEFAULTS)


# ---------------------------------------------------------------------------
# Хранилище
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS secret (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,
    service         TEXT NOT NULL DEFAULT '',
    label           TEXT NOT NULL DEFAULT '',
    owner           TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '',
    project         TEXT NOT NULL DEFAULT '',
    visibility      TEXT NOT NULL DEFAULT 'all',
    agents          TEXT NOT NULL DEFAULT '',
    nonce           TEXT NOT NULL,
    ciphertext      TEXT NOT NULL,
    ver             INTEGER NOT NULL DEFAULT 1,
    value_len       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL DEFAULT '',
    created_by_kind TEXT NOT NULL DEFAULT '',
    created_by_via  TEXT NOT NULL DEFAULT '',
    created_by_params TEXT NOT NULL DEFAULT '',
    reveal_count    INTEGER NOT NULL DEFAULT 0,
    last_revealed_at TEXT NOT NULL DEFAULT '',
    last_revealed_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    op          TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    actor_kind  TEXT NOT NULL DEFAULT '',
    via         TEXT NOT NULL DEFAULT '',
    client      TEXT NOT NULL DEFAULT '',
    task        TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    -- RAGRAF-AUDIT-CHAIN: хэш предыдущей записи и хэш этой записи.
    -- Цепочка нужна для внешнего аудита: правка или удаление строки ломает
    -- продолжение, и это видно проверкой, а не «на глаз». Пустые значения
    -- означают запись, сделанную до появления цепочки, — они считаются
    -- отдельно и НЕ объявляются проверенными.
    prev_hash   TEXT NOT NULL DEFAULT '',
    row_hash    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_name ON audit(name);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit is append-only');
END;
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


AUDIT_HASH_FIELDS = ("ts", "op", "name", "actor", "actor_kind", "via", "client",
                     "task", "result", "reason")


def audit_row_hash(prev_hash: str, fields: tuple) -> str:
    """Хэш записи журнала: предыдущий хэш плюс все содержательные поля.

    Разделитель `\x1f` невозможен в полях (они текст), поэтому склейка не даёт
    двух разных строк с одинаковым входом. Хэш считается по UTF-8: журнал
    русскоязычный, и `repr` дал бы разное на разных версиях Python.
    """
    parts = [str(prev_hash), *[str(item) for item in fields]]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class Vault:
    """Хранилище секретов: единственная точка чтения и записи значений."""

    def __init__(self, path: Path | None = None, master: bytes | None = None,
                 settings: dict[str, Any] | None = None) -> None:
        self.path = Path(path) if path else db_path()
        self._master = master
        self._settings = settings

    # -- служебное -------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        if self._settings is not None:
            return self._settings
        return _load_settings()

    def setting(self, name: str) -> Any:
        return self.settings().get(name)

    def key_file(self) -> Path:
        """Файл ключа рядом с данными этого хранилища.

        У рабочего хранилища это ``catalog/secrets.key``; у копии в песочнице —
        ключ рядом с её базой. Иначе ротация в тесте писала бы в живой каталог
        проекта, то есть проверка меняла бы боевое состояние.
        """
        return self.path.parent / KEY_FILE_NAME

    def master(self) -> bytes:
        if self._master is not None:
            return self._master
        env = (os.environ.get(MASTER_ENV) or "").strip()
        local = self.key_file()
        if not env and local.exists():
            try:
                raw = _b64d(local.read_text(encoding="utf-8").strip())
            except Exception as exc:  # noqa: BLE001
                raise VaultError("key_invalid", "файл ключа повреждён") from exc
            if len(raw) != 32:
                raise VaultError("key_invalid", "в файле ключа не 32 байта")
            self._master = raw
            return raw
        self._master = read_master_key()
        return self._master

    def fingerprint(self) -> str:
        return _fingerprint_of(self.master())

    def _safe_fingerprint(self) -> str:
        try:
            return self.fingerprint()
        except VaultError:
            return ""

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Догоняющая миграция журнала аудита: цепочка хэшей.

        Почему ALTER, а не пересоздание таблицы. Журнал — доказательство; его
        пересоздание стёрло бы историю, ради которой он существует. Существующие
        строки остаются как есть и получают пустые хэши: цепочка начинается с
        первой новой записи, и `verify_audit` говорит, с какой именно.
        """
        columns = {str(row["name"]) for row in conn.execute(
            "PRAGMA table_info(audit)").fetchall()}
        for name in ("prev_hash", "row_hash"):
            if name not in columns:
                conn.execute(f"ALTER TABLE audit ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Соединение с теми же правилами, что у остальных хранилищ.

        Ожидание блокировки задано явно (иначе параллельный аудит падает
        «database is locked»), внешние ключи отключены — как в ``store.py``.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(_SCHEMA)
        self._migrate(conn)
        return conn

    def _declined(self) -> None:
        if not bool(self.setting("secrets_enabled")):
            raise VaultError("disabled",
                             "хранилище секретов выключено настройкой "
                             "«secrets_enabled»")

    # -- аудит -----------------------------------------------------------

    def _audit(self, conn: sqlite3.Connection, *, op: str, name: str,
               actor: Actor, result: str, reason: str = "") -> None:
        """Записать операцию. Значение сюда не попадает никогда.

        Каждая запись продолжает цепочку: `prev_hash` — хэш предыдущей записи,
        `row_hash` — хэш этой строки вместе с предыдущим хэшем. Подделка строки
        ломает либо её собственный хэш, либо связь со следующей, и `verify_audit`
        это называет. Записи без хэша (сделанные до цепочки) цепочку не
        продолжают и проверяются отдельно.
        """
        try:
            fields = (_now(), op[:40], name[:200], actor.label[:120],
                      actor.kind[:40], actor.via[:20], actor.client[:64],
                      actor.task[:120], result[:20], reason[:300])
            previous = conn.execute(
                "SELECT row_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = str(previous["row_hash"]) if previous else ""
            row_hash = audit_row_hash(prev_hash, fields)
            conn.execute(
                "INSERT INTO audit(ts, op, name, actor, actor_kind, via, client,"
                " task, result, reason, prev_hash, row_hash)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (*fields, prev_hash, row_hash),
            )
            conn.commit()
        except sqlite3.Error:
            # Журнал не должен отменять операцию; сбой виден в отчёте check
            # по числу строк, а не прячется.
            pass

    def verify_audit(self) -> dict[str, Any]:
        """Пересчитать цепочку журнала и назвать, где она расходится.

        Возвращает отчёт, а не исключение: журнал может быть длинным, и внешнему
        аудитору нужен список расхождений, а не первый из них.

        `ok` истинно только если нарушений нет. Записи без хэшей считаются
        отдельно (`legacy`) и НЕ считаются ни проверенными, ни испорченными:
        цепочка началась позже, это факт, а не дефект.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, " + ", ".join(AUDIT_HASH_FIELDS) + ", prev_hash, row_hash"
                " FROM audit ORDER BY id ASC").fetchall()
        finally:
            conn.close()
        checked = 0
        legacy = 0
        broken: list[dict[str, Any]] = []
        previous = ""
        started = False
        for row in rows:
            row_hash = str(row["row_hash"] or "")
            if not row_hash:
                legacy += 1
                continue
            fields = tuple(row[name] for name in AUDIT_HASH_FIELDS)
            expected = audit_row_hash(str(row["prev_hash"] or ""), fields)
            if expected != row_hash:
                broken.append({"id": int(row["id"]), "problem": "хэш записи не сходится",
                               "expected": expected, "stored": row_hash})
            if started and str(row["prev_hash"] or "") != previous:
                broken.append({"id": int(row["id"]),
                               "problem": "запись не продолжает предыдущую",
                               "expected": previous, "stored": str(row["prev_hash"])})
            if not started and str(row["prev_hash"] or "") not in ("", previous):
                broken.append({"id": int(row["id"]),
                               "problem": "начало цепочки не сходится",
                               "expected": previous, "stored": str(row["prev_hash"])})
            previous = row_hash
            started = True
            checked += 1
        return {
            "ok": not broken,
            "checked": checked,
            "legacy": legacy,
            "head": previous,
            "broken": broken,
        }

    def export_audit(self, path: str | Path, actor: Actor | None = None,
                     sign: bool = True) -> dict[str, Any]:
        """Выгрузить журнал файлом JSONL с итоговым хэшем и подписью.

        Формат строки — одна запись журнала целиком, включая хэши: без них
        получатель не смог бы пересчитать цепочку. Последняя строка — итог
        (`kind: summary`) с числом записей, головой цепочки и результатом
        проверки на момент выгрузки.
        """
        report = self.verify_audit()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, " + ", ".join(AUDIT_HASH_FIELDS)
                + ", prev_hash, row_hash FROM audit ORDER BY id ASC").fetchall()
        finally:
            conn.close()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for row in rows:
            item = {"kind": "audit"}
            item.update({name: row[name] for name in AUDIT_HASH_FIELDS})
            item["id"] = int(row["id"])
            item["prev_hash"] = str(row["prev_hash"] or "")
            item["row_hash"] = str(row["row_hash"] or "")
            lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        summary = {
            "kind": "summary",
            "format": "ragraf-audit-export/1",
            "created": _now(),
            "rows": len(rows),
            "checked": report["checked"],
            "legacy": report["legacy"],
            "head": report["head"],
            "ok": report["ok"],
            "broken": report["broken"],
        }
        lines.append(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        target.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

        signed = False
        signature_error = ""
        if sign:
            try:
                from ragraf import signing as signing_mod

                signing_mod.ensure_key()
                signing_mod.sign_file(target)
                signed = True
            except Exception as exc:  # noqa: BLE001
                signature_error = f"{type(exc).__name__}: {exc}"

        result = {"path": str(target), "rows": len(rows), "ok": report["ok"],
                  "checked": report["checked"], "legacy": report["legacy"],
                  "head": report["head"], "signed": signed}
        if signature_error:
            result["signature_error"] = signature_error
        if actor is not None:
            self._audit_safe(op="audit_export", name=str(target.name),
                             actor=actor, result="ok" if report["ok"] else "attention")
        return result

    def _audit_safe(self, *, op: str, name: str, actor: Actor, result: str) -> None:
        """Запись в журнал о самом журнале: сбой записи не отменяет экспорт."""
        try:
            conn = self._connect()
            try:
                self._audit(conn, op=op, name=name, actor=actor, result=result)
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    def audit_rows(self, limit: int = 50, name: str = "") -> list[dict[str, Any]]:
        """Прочитать журнал. Значения в нём нет по построению."""
        conn = self._connect()
        try:
            if name:
                rows = conn.execute(
                    "SELECT * FROM audit WHERE name = ? ORDER BY id DESC LIMIT ?",
                    (name, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    # -- права -----------------------------------------------------------

    def _may_read(self, row: sqlite3.Row, actor: Actor) -> tuple[bool, str]:
        """Разрешено ли участнику прочитать эту запись.

        Порядок проверок важен. Сначала общий список разрешённых агентов
        (если он задан — он сильнее всего), затем режим самой записи. Человек
        за локальной панелью не ограничен: он и так владеет ключом.
        """
        if actor.admin:
            return True, "admin"
        allowed_agents = [str(a) for a in (self.setting("secrets_allowed_agents") or [])]
        if allowed_agents and not _agent_in(actor.label, allowed_agents):
            return False, "agent_not_allowed"
        mode = str(row["visibility"] or "all")
        if mode == "all":
            return True, "visibility_all"
        if mode == "owner_only":
            return (actor.label.lower() == str(row["created_by"]).lower(),
                    "owner_only")
        listed = _uncsv(str(row["agents"] or ""))
        if not listed:
            # Режим «только перечисленным» с пустым списком: это «никому»,
            # и так и должно читаться — иначе настройка тихо станет «всем».
            return False, "listed_empty"
        return (_agent_in(actor.label, listed), "listed")

    # -- запись ----------------------------------------------------------

    def set(self, name: str, value: str, *, actor: Actor | None = None,
            kind: str = "password", service: str = "", label: str = "",
            owner: str = "", note: str = "", tags: Iterable[str] = (),
            project: str = "", visibility: str | None = None,
            agents: Iterable[str] = (), expires_at: str = "",
            captured: bool = False) -> dict[str, Any]:
        """Завести или заменить секрет. Возвращает метаданные, без значения."""
        self._declined()
        actor = actor or actor_from_env()
        clean = str(name or "").strip()
        if not clean:
            raise VaultError("bad_name", "имя секрета пустое")
        if any(ch in clean for ch in "\r\n\t"):
            raise VaultError("bad_name", "имя не должно содержать переводов строк")
        text = str(value or "")
        if not text:
            raise VaultError("empty_value", "значение пустое")
        limit = int(self.setting("secrets_max_value_bytes")
                    or DEFAULT_MAX_VALUE_BYTES)
        if len(text.encode("utf-8")) > limit:
            raise VaultError("value_too_large", f"значение длиннее {limit} байт")
        kind_norm = str(kind or "password").strip().lower()
        if kind_norm not in KINDS:
            raise VaultError("bad_kind",
                             f"неизвестный тип «{kind}»; допустимы: "
                             + ", ".join(KINDS))
        if captured and not bool(self.setting("secrets_capture_enabled")):
            raise VaultError("capture_disabled",
                             "сохранение «встреченных» секретов выключено "
                             "настройкой «secrets_capture_enabled»")
        mode = (visibility or str(self.setting("secrets_default_visibility")
                                  or "all")).strip().lower()
        if mode not in VISIBILITIES:
            raise VaultError("bad_visibility",
                             "режим видимости: " + ", ".join(VISIBILITIES))
        agent_list = _csv(agents)
        if mode == "listed" and not agent_list:
            raise VaultError("listed_empty",
                             "режим «listed» требует непустого списка агентов")
        expiry = (expires_at or "").strip()
        if expiry and _parse_ts(expiry) is None:
            raise VaultError("bad_expiry",
                             "срок задаётся как ISO-8601: 2027-01-31T00:00:00Z")

        conn = self._connect()
        try:
            existing = conn.execute("SELECT * FROM secret WHERE name = ?",
                                    (clean,)).fetchone()
            now = _now()
            if existing is None:
                record_id = _random.token_hex(16)
                version = 1
                nonce, cipher = _encrypt(self.master(), record_id, clean,
                                         version, text)
                conn.execute(
                    "INSERT INTO secret(id, name, kind, service, label, owner,"
                    " note, tags, project, visibility, agents, nonce, ciphertext,"
                    " ver, value_len, created_at, updated_at, expires_at,"
                    " created_by, created_by_kind, created_by_via,"
                    " created_by_params)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record_id, clean, kind_norm, service.strip(), label.strip(),
                     owner.strip(), note.strip(), _csv(tags), project.strip(),
                     mode, agent_list, nonce, cipher, version,
                     len(text.encode("utf-8")), now, now, expiry,
                     actor.label[:120], actor.kind[:40], actor.via[:20],
                     json.dumps(actor.to_dict(), ensure_ascii=False)),
                )
                created = True
            else:
                if not self._may_read(existing, actor)[0]:
                    self._audit(conn, op="set", name=clean, actor=actor,
                                result="denied", reason="no_access")
                    raise VaultError("no_access",
                                     "запись существует и вам не разрешена")
                record_id = str(existing["id"])
                version = int(existing["ver"]) + 1
                nonce, cipher = _encrypt(self.master(), record_id, clean,
                                         version, text)
                conn.execute(
                    "UPDATE secret SET kind=?, service=?, label=?, owner=?,"
                    " note=?, tags=?, project=?, visibility=?, agents=?,"
                    " nonce=?, ciphertext=?, ver=?, value_len=?, updated_at=?,"
                    " expires_at=? WHERE id=?",
                    (kind_norm, service.strip(), label.strip(), owner.strip(),
                     note.strip(), _csv(tags), project.strip(), mode, agent_list,
                     nonce, cipher, version, len(text.encode("utf-8")), now,
                     expiry, record_id),
                )
                created = False
            conn.commit()
            self._audit(conn, op="set", name=clean, actor=actor, result="ok",
                        reason="created" if created else "replaced")
            row = conn.execute("SELECT * FROM secret WHERE id = ?",
                               (record_id,)).fetchone()
            return self._public(row, actor, reveal_ok=True)
        finally:
            conn.close()

    def _public(self, row: sqlite3.Row, actor: Actor, *,
                reveal_ok: bool = False) -> dict[str, Any]:
        """Метаданные записи. Значение сюда не попадает ни при каких условиях."""
        days_left = _days_left(str(row["expires_at"] or ""))
        warn_days = int(self.setting("secrets_expiry_warn_days")
                        or DEFAULT_EXPIRY_WARN_DAYS)
        params: dict[str, Any] = {}
        try:
            raw = json.loads(str(row["created_by_params"] or "{}"))
            if isinstance(raw, dict):
                params = raw
        except Exception:  # noqa: BLE001
            params = {}
        return {
            "name": row["name"],
            "kind": row["kind"],
            "service": row["service"],
            "label": row["label"],
            "owner": row["owner"],
            "note": row["note"],
            "tags": _uncsv(str(row["tags"] or "")),
            "project": row["project"],
            "visibility": str(row["visibility"] or "all"),
            "agents": _uncsv(str(row["agents"] or "")),
            "value_len": int(row["value_len"] or 0),
            "masked": "****",
            "version": int(row["ver"] or 1),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "days_left": days_left,
            "expired": bool(days_left is not None and days_left < 0),
            "expiring_soon": bool(days_left is not None and 0 <= days_left <= warn_days),
            "created_by": row["created_by"],
            "created_by_kind": row["created_by_kind"],
            "created_by_via": row["created_by_via"],
            "created_by_params": params,
            "reveal_count": int(row["reveal_count"] or 0),
            "last_revealed_at": row["last_revealed_at"],
            "last_revealed_by": row["last_revealed_by"],
            "may_reveal": reveal_ok,
        }

    # -- чтение ----------------------------------------------------------

    def list(self, *, actor: Actor | None = None, query: str = "",
             kind: str = "", tag: str = "", limit: int = 200,
             include_hidden: bool = False) -> dict[str, Any]:
        """Список метаданных. Скрытые записи не видны даже по имени.

        ``include_hidden`` нужен человеку за панелью: администратор обязан
        видеть, что запись есть, но кому-то не разрешена — иначе он не поймёт,
        почему агент получает отказ.
        """
        self._declined()
        actor = actor or actor_from_env()
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM secret ORDER BY name COLLATE NOCASE"
                                ).fetchall()
        finally:
            conn.close()
        needle = str(query or "").strip().lower()
        kind_norm = str(kind or "").strip().lower()
        tag_norm = str(tag or "").strip().lower()
        items: list[dict[str, Any]] = []
        hidden = 0
        for row in rows:
            allowed, why = self._may_read(row, actor)
            if not allowed:
                hidden += 1
                if not include_hidden:
                    continue
            public = self._public(row, actor, reveal_ok=allowed)
            if not allowed:
                public["hidden_reason"] = why
            if needle:
                haystack = " ".join(str(public[key]).lower() for key in
                                    ("name", "service", "label", "owner", "note",
                                     "project")) + " " + " ".join(
                    str(t).lower() for t in public["tags"])
                if needle not in haystack:
                    continue
            if kind_norm and public["kind"] != kind_norm:
                continue
            if tag_norm and tag_norm not in [str(t).lower() for t in public["tags"]]:
                continue
            items.append(public)
            if len(items) >= int(limit):
                break
        return {"count": len(items), "hidden": hidden, "items": items,
                "key_fingerprint": self._safe_fingerprint()}

    def show(self, name: str, *, actor: Actor | None = None) -> dict[str, Any]:
        """Маска и метаданные одной записи."""
        self._declined()
        actor = actor or actor_from_env()
        row = self._row(name)
        allowed, why = self._may_read(row, actor)
        conn = self._connect()
        try:
            self._audit(conn, op="show", name=str(row["name"]), actor=actor,
                        result="ok" if allowed else "denied", reason=why)
        finally:
            conn.close()
        if not allowed:
            raise VaultError("not_found", "секрет не найден")
        return self._public(row, actor, reveal_ok=True)

    def reveal(self, name: str, *, actor: Actor | None = None,
               reason: str = "") -> dict[str, Any]:
        """Выдать значение. Каждый вызов и каждый отказ пишутся в аудит."""
        self._declined()
        actor = actor or actor_from_env()
        if bool(self.setting("secrets_reveal_requires_reason")) and not str(reason or "").strip():
            conn = self._connect()
            try:
                self._audit(conn, op="reveal", name=str(name or ""), actor=actor,
                            result="denied", reason="reason_required")
            finally:
                conn.close()
            raise VaultError("reason_required",
                             "укажите причину выдачи: поле reason")
        row = self._row_or_none(name)
        conn = self._connect()
        try:
            if row is None:
                self._audit(conn, op="reveal", name=str(name or ""), actor=actor,
                            result="not_found", reason="no_such_name")
                raise VaultError("not_found", "секрет не найден")
            allowed, why = self._may_read(row, actor)
            if not allowed:
                # Отказ не должен подтверждать существование имени: текст тот
                # же, что у «не найдено», а причина остаётся только в аудите.
                self._audit(conn, op="reveal", name=str(row["name"]), actor=actor,
                            result="denied", reason=why)
                raise VaultError("not_found", "секрет не найден")
            value = _decrypt(self.master(), str(row["id"]), str(row["name"]),
                             int(row["ver"]), str(row["nonce"]),
                             str(row["ciphertext"]))
            conn.execute(
                "UPDATE secret SET reveal_count = reveal_count + 1,"
                " last_revealed_at = ?, last_revealed_by = ? WHERE id = ?",
                (_now(), actor.label[:120], row["id"]),
            )
            conn.commit()
            self._audit(conn, op="reveal", name=str(row["name"]), actor=actor,
                        result="ok", reason=str(reason or "")[:120])
            out = self._public(row, actor, reveal_ok=True)
            out["value"] = value
            out["revealed_at"] = _now()
            out["reason"] = str(reason or "")
            return out
        finally:
            conn.close()

    def _row(self, name: str) -> sqlite3.Row:
        row = self._row_or_none(name)
        if row is None:
            raise VaultError("not_found", "секрет не найден")
        return row

    def _row_or_none(self, name: str) -> sqlite3.Row | None:
        clean = str(name or "").strip()
        if not clean:
            return None
        conn = self._connect()
        try:
            return conn.execute("SELECT * FROM secret WHERE name = ?",
                                (clean,)).fetchone()
        finally:
            conn.close()

    # -- права и удаление ------------------------------------------------

    def grant(self, name: str, agents: Iterable[str], *,
              mode: str | None = None, actor: Actor | None = None) -> dict[str, Any]:
        """Выдать доступ конкретным агентам (или сменить режим видимости)."""
        self._declined()
        actor = actor or actor_from_env()
        row = self._row(name)
        if not actor.admin and actor.label.lower() != str(row["created_by"]).lower():
            raise VaultError("no_access",
                             "менять права записи может её автор или администратор")
        listed = _uncsv(str(row["agents"] or ""))
        for agent in agents:
            text = str(agent or "").strip()
            if text and text.lower() not in {s.lower() for s in listed}:
                listed.append(text)
        target = (mode or "listed").strip().lower()
        if target not in VISIBILITIES:
            raise VaultError("bad_visibility", "режим видимости: "
                             + ", ".join(VISIBILITIES))
        if target == "listed" and not listed:
            raise VaultError("listed_empty",
                             "режим «listed» требует непустого списка агентов")
        conn = self._connect()
        try:
            conn.execute("UPDATE secret SET visibility = ?, agents = ?,"
                         " updated_at = ? WHERE id = ?",
                         (target, _csv(listed), _now(), row["id"]))
            conn.commit()
            self._audit(conn, op="grant", name=str(row["name"]), actor=actor,
                        result="ok", reason=target)
            fresh = conn.execute("SELECT * FROM secret WHERE id = ?",
                                 (row["id"],)).fetchone()
            return self._public(fresh, actor, reveal_ok=True)
        finally:
            conn.close()

    def revoke(self, name: str, agents: Iterable[str], *,
               actor: Actor | None = None) -> dict[str, Any]:
        """Забрать доступ у конкретных агентов."""
        self._declined()
        actor = actor or actor_from_env()
        row = self._row(name)
        if not actor.admin and actor.label.lower() != str(row["created_by"]).lower():
            raise VaultError("no_access",
                             "менять права записи может её автор или администратор")
        drop = {str(a or "").strip().lower() for a in agents if str(a or "").strip()}
        left = [a for a in _uncsv(str(row["agents"] or "")) if a.lower() not in drop]
        mode = str(row["visibility"] or "all")
        if mode == "listed" and not left:
            mode = "owner_only"
        conn = self._connect()
        try:
            conn.execute("UPDATE secret SET agents = ?, visibility = ?,"
                         " updated_at = ? WHERE id = ?",
                         (_csv(left), mode, _now(), row["id"]))
            conn.commit()
            self._audit(conn, op="revoke", name=str(row["name"]), actor=actor,
                        result="ok", reason=mode)
            fresh = conn.execute("SELECT * FROM secret WHERE id = ?",
                                 (row["id"],)).fetchone()
            return self._public(fresh, actor, reveal_ok=True)
        finally:
            conn.close()

    def delete(self, name: str, *, actor: Actor | None = None) -> dict[str, Any]:
        """Удалить запись. Возвращает метаданные того, что удалено."""
        self._declined()
        actor = actor or actor_from_env()
        row = self._row(name)
        if not actor.admin and actor.label.lower() != str(row["created_by"]).lower():
            raise VaultError("no_access",
                             "удалить запись может её автор или администратор")
        public = self._public(row, actor, reveal_ok=False)
        conn = self._connect()
        try:
            conn.execute("DELETE FROM secret WHERE id = ?", (row["id"],))
            conn.commit()
            self._audit(conn, op="delete", name=str(row["name"]), actor=actor,
                        result="ok", reason="")
        finally:
            conn.close()
        public["deleted"] = True
        return public

    # -- целостность, ротация, бэкап --------------------------------------

    def check(self) -> dict[str, Any]:
        """Проверить, что хранилище читается целиком. Значений в отчёте нет."""
        report: dict[str, Any] = {"ok": True, "problems": [], "warnings": []}
        if not HAVE_NACL:
            return {"ok": False, "problems": ["нет библиотеки PyNaCl"],
                    "warnings": []}
        try:
            master = self.master()
        except VaultError as exc:
            return {"ok": False, "problems": [f"ключ: {exc.code}"],
                    "warnings": []}
        report["key_fingerprint"] = _fingerprint_of(master)
        report["db_path"] = str(self.path)
        report["key_source"] = ("env" if (os.environ.get(MASTER_ENV) or "").strip()
                                else "file")
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM secret ORDER BY name").fetchall()
            report["count"] = len(rows)
            digests: dict[str, list[str]] = {}
            expired = 0
            soon = 0
            for row in rows:
                try:
                    value = _decrypt(master, str(row["id"]), str(row["name"]),
                                     int(row["ver"]), str(row["nonce"]),
                                     str(row["ciphertext"]))
                except VaultError as exc:
                    report["problems"].append(f"{row['name']}: {exc.code}")
                    continue
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                digests.setdefault(digest, []).append(str(row["name"]))
                days = _days_left(str(row["expires_at"] or ""))
                if days is not None and days < 0:
                    expired += 1
                elif days is not None and days <= int(self.setting(
                        "secrets_expiry_warn_days") or DEFAULT_EXPIRY_WARN_DAYS):
                    soon += 1
            report["decrypted"] = len(rows) - len(report["problems"])
            report["expired"] = expired
            report["expiring_soon"] = soon
            duplicates = [names for names in digests.values() if len(names) > 1]
            if duplicates:
                report["warnings"].append(
                    "одинаковое значение у записей: "
                    + "; ".join(", ".join(names) for names in duplicates))
            report["audit_rows"] = int(conn.execute(
                "SELECT COUNT(*) FROM audit").fetchone()[0] or 0)
            triggers = {str(r["name"]) for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
            for needed in ("audit_no_update", "audit_no_delete"):
                if needed not in triggers:
                    report["problems"].append(
                        f"нет триггера {needed}: журнал перестал быть append-only")
        finally:
            conn.close()
        if report["problems"]:
            report["ok"] = False
        return report

    def rotate_key(self, *, actor: Actor | None = None) -> dict[str, Any]:
        """Перешифровать все записи новым ключом.

        Порядок важен: сначала резервная копия ключа, затем перешифровка в одной
        транзакции, затем запись нового ключа. Сбой на любом шаге откатывает
        данные — «наполовину перешифровано» означает потерю всех значений.
        """
        self._declined()
        actor = actor or actor_from_env()
        if (os.environ.get(MASTER_ENV) or "").strip():
            raise VaultError("key_from_env",
                             "ключ задан переменной окружения: ротация на диске "
                             "ничего не изменит, смените значение переменной")
        old_master = self.master()
        conn = self._connect()
        backup_dir = self.path.parent / f"backup-secrets-{_stamp()}"
        try:
            rows = conn.execute("SELECT * FROM secret").fetchall()
            backup_dir.mkdir(parents=True, exist_ok=True)
            target_key = self.key_file()
            if target_key.exists():
                old_copy = backup_dir / (KEY_FILE_NAME + ".old")
                old_copy.write_bytes(target_key.read_bytes())
                _restrict(old_copy)
            new_master = _random.token_bytes(32)
            for row in rows:
                value = _decrypt(old_master, str(row["id"]), str(row["name"]),
                                 int(row["ver"]), str(row["nonce"]),
                                 str(row["ciphertext"]))
                nonce, cipher = _encrypt(new_master, str(row["id"]),
                                         str(row["name"]), int(row["ver"]), value)
                conn.execute(
                    "UPDATE secret SET nonce = ?, ciphertext = ?, updated_at = ?"
                    " WHERE id = ?",
                    (nonce, cipher, _now(), row["id"]),
                )
            conn.commit()
            try:
                target_key.parent.mkdir(parents=True, exist_ok=True)
                target_key.write_text(_b64e(new_master), encoding="utf-8")
                _restrict(target_key)
            except Exception:
                for row in rows:
                    conn.execute(
                        "UPDATE secret SET nonce = ?, ciphertext = ? WHERE id = ?",
                        (row["nonce"], row["ciphertext"], row["id"]),
                    )
                conn.commit()
                raise
            self._master = new_master
            self._audit(conn, op="rotate", name="*", actor=actor, result="ok",
                        reason=f"records={len(rows)}")
            return {"rotated": True, "records": len(rows),
                    "fingerprint": _fingerprint_of(new_master),
                    "backup": str(backup_dir)}
        finally:
            conn.close()

    def export(self, path: str, passphrase: str, *,
               actor: Actor | None = None) -> dict[str, Any]:
        """Шифрованный снимок для бэкапа.

        Снимок шифруется ключом из парольной фразы (scrypt), а не мастер-ключом:
        иначе он был бы бесполезен ровно тогда, когда нужен, — при потере ключа.
        """
        self._declined()
        if not str(passphrase or ""):
            raise VaultError("passphrase_required",
                             "для снимка нужна парольная фраза")
        actor = actor or actor_from_env()
        master = self.master()
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM secret ORDER BY name").fetchall()
        finally:
            conn.close()
        records: list[dict[str, Any]] = []
        for row in rows:
            value = _decrypt(master, str(row["id"]), str(row["name"]),
                             int(row["ver"]), str(row["nonce"]),
                             str(row["ciphertext"]))
            meta = self._public(row, actor, reveal_ok=False)
            meta["value"] = value
            records.append(meta)
        payload = json.dumps({"version": 1, "records": records},
                             ensure_ascii=False).encode("utf-8")
        salt = _random.token_bytes(16)
        snap_key = hashlib.scrypt(str(passphrase).encode("utf-8"), salt=salt,
                                  n=2 ** 14, r=8, p=1, dklen=32)
        secret_mod = _require_nacl()
        nonce = _random.token_bytes(secret_mod.SecretBox.NONCE_SIZE)
        sealed = bytes(secret_mod.SecretBox(snap_key).encrypt(payload, nonce))
        # См. `_encrypt`: nonce хранится отдельно, в шифротексте его нет.
        cipher_text = sealed[secret_mod.SecretBox.NONCE_SIZE:]
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "format": "ragraf-secrets-export",
            "version": 1,
            "kdf": "scrypt n=16384 r=8 p=1",
            "salt": _b64e(salt),
            "nonce": _b64e(nonce),
            "ciphertext": _b64e(cipher_text),
            "created_at": _now(),
            "records": len(records),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        _restrict(target)
        again = self._connect()
        try:
            self._audit(again, op="export", name="*", actor=actor,
                        result="ok", reason=f"records={len(records)}")
        finally:
            again.close()
        return {"exported": True, "path": str(target), "records": len(records)}

    def import_(self, path: str, passphrase: str, *,
                actor: Actor | None = None) -> dict[str, Any]:
        """Восстановить записи из шифрованного снимка."""
        self._declined()
        actor = actor or actor_from_env()
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("format") != "ragraf-secrets-export":
            raise VaultError("bad_export", "это не снимок хранилища RAGRAF")
        salt = _b64d(str(raw["salt"]))
        key = hashlib.scrypt(str(passphrase).encode("utf-8"), salt=salt,
                             n=2 ** 14, r=8, p=1, dklen=32)
        secret_mod = _require_nacl()
        try:
            blob = secret_mod.SecretBox(key).decrypt(
                _b64d(str(raw["ciphertext"])), _b64d(str(raw["nonce"])))
        except _CryptoError as exc:
            raise VaultError("bad_passphrase",
                             "снимок не расшифровывается этой фразой") from exc
        payload = json.loads(blob.decode("utf-8"))
        restored = 0
        # Восстановление идёт от имени владельца: снимок уже расшифрован
        # парольной фразой, то есть вызывающий подтвердил право на ВСЕ записи.
        # Без этого запись с режимом «только HARNESS» нельзя было бы вернуть:
        # set() справедливо счёл бы её чужой.
        owner = Actor(label=actor.label, kind=actor.kind, via=actor.via,
                      client=actor.client, task=actor.task, admin=True,
                      params=dict(actor.params))
        for record in payload.get("records") or []:
            self.set(
                str(record.get("name") or ""),
                str(record.get("value") or ""),
                actor=owner,
                kind=str(record.get("kind") or "other"),
                service=str(record.get("service") or ""),
                label=str(record.get("label") or ""),
                owner=str(record.get("owner") or ""),
                note=str(record.get("note") or ""),
                tags=record.get("tags") or [],
                project=str(record.get("project") or ""),
                visibility=str(record.get("visibility") or "all"),
                agents=record.get("agents") or [],
                expires_at=str(record.get("expires_at") or ""),
            )
            restored += 1
        return {"imported": restored, "path": str(path)}

    # -- политика для агентов --------------------------------------------

    def policy(self) -> dict[str, Any]:
        """Правила работы с секретами, которые агент читает перед задачей.

        Это тот же ответ, что и настройки, но сформулированный как инструкция:
        агенту не нужно знать имена ключей, ему нужно знать, что делать.
        ``capture`` — сохранять ли встреченные секреты.
        """
        enabled = bool(self.setting("secrets_enabled"))
        capture = bool(self.setting("secrets_capture_enabled"))
        visibility = str(self.setting("secrets_default_visibility") or "all")
        allowed_agents = [str(a) for a in (self.setting("secrets_allowed_agents") or [])]
        return {
            "enabled": enabled,
            "capture": capture,
            "default_visibility": visibility,
            "reveal_requires_reason": bool(
                self.setting("secrets_reveal_requires_reason")),
            "allowed_agents": allowed_agents,
            "kinds": list(KINDS),
            "key_fingerprint": self._safe_fingerprint(),
            "instruction": _policy_text(enabled, capture, visibility),
        }


def _agent_in(label: str, allowed: Iterable[str]) -> bool:
    """Совпадение имени агента без учёта регистра; ``*`` разрешает всем."""
    who = str(label or "").strip().lower()
    for item in allowed:
        candidate = str(item or "").strip().lower()
        if candidate == "*" or candidate == who:
            return True
    return False


def _policy_text(enabled: bool, capture: bool, visibility: str) -> str:
    if not enabled:
        return ("Хранилище секретов выключено. Значения не сохранять и не "
                "запрашивать; если секрет нужен для работы — сказать об этом "
                "владельцу.")
    base = ("Если в запросе или в файле встретился секрет (пароль, токен, ключ, "
            "сертификат, строка подключения) и владелец не сказал его удалить "
            "или не сохранять — ")
    if capture:
        tail = ("сохраняй его в RAGRAF через ragraf_secret_set с понятным именем "
                "и типом. Значение в ответ и в чат не печатай: подтверждай "
                "именем и маской")
    else:
        tail = ("сохраняй его не автоматически, а по прямой команде: "
                "сохранение встреченных секретов выключено настройкой")
    mode = ("по умолчанию новая запись доступна всем агентам"
            if visibility == "all" else
            "по умолчанию новая запись доступна только сохранившему агенту")
    # Одна точка в конце: склейка без неё давала «команде..» в ответе агента.
    return base + tail + ". Режим доступа: " + mode + "."


# ---------------------------------------------------------------------------
# Редактирование (redaction)
# ---------------------------------------------------------------------------

#: Типовые формы, в которых значение утекает в текст: присваивание в конфиге,
#: заголовок авторизации, параметр URL, тело закрытого ключа. Шаблоны намеренно
#: не «умные»: лучше замазать лишнее, чем пропустить значение.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}", r"\1 [redacted]"),
    (r"(?i)\b(password|passwd|pwd|token|apikey|api_key|secret|client_secret"
     r"|access_token|refresh_token|authorization)\b(\s*[:=]\s*)"
     r"(\"[^\"]*\"|'[^']*'|\S+)", r"\1\2[redacted]"),
    (r"(?i)([?&](?:password|token|key|apikey|secret)=)[^&\s]+", r"\1[redacted]"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
     "[redacted private key]"),
)


def redact(text: str, *, values: Iterable[str] | None = None) -> str:
    """Замазать секреты в тексте перед записью в лог или в ответ.

    Два слоя: известные значения (если переданы) и типовые формы. Значения
    берутся только по явной передаче: молча расшифровывать всё хранилище при
    каждом вызове — это и медленно, и опасно.
    """
    import re

    out = str(text or "")
    for value in values or ():
        raw = str(value or "")
        if len(raw) >= 4:
            out = out.replace(raw, "[redacted]")
    for pattern, replacement in _PATTERNS:
        out = re.sub(pattern, replacement, out)
    return out
