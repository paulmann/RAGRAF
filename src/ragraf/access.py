"""Доступ к интерфейсу RAGRAF: пароль администратора и именованные bearer-токены.

Зачем отдельный модуль
    До этой правки вход в панель защищался ОДНИМ токеном сессии
    (`admin_server.ACCESS_TOKEN`): он создавался при запуске, нигде не хранился
    и умирал вместе с процессом. Этого мало по двум причинам:

      * нельзя работать с панелью с другого запуска — токен приходится искать
        в выводе процесса;
      * нельзя выдать доступ агенту ОТДЕЛЬНО от администратора и понять,
        кто сколько истратил и с каким результатом.

    Поэтому здесь две независимые вещи: пароль администратора (опциональный) и
    именованные токены с атрибуцией («кому выдан»).

Инварианты, которые модуль обязан держать
    1. **Секрет токена не хранится.** В файле лежит только SHA-256 секрета.
       Показать секрет можно ОДИН раз — при создании. Это то же правило, по
       которому устроены ключи API: утечка файла не даёт доступа.
    2. **Сравнение постоянного времени.** `secrets.compare_digest`, иначе по
       времени ответа можно подбирать секрет посимвольно.
    3. **Причина отказа одна и та же.** «Токена нет», «токен неверный»,
       «токен отозван» и «срок истёк» не различаются в ответе клиенту —
       иначе перебор становится осмысленным. Причина видна в журнале доступа.
    4. **Пароль не логируется и не возвращается** ни в одном ответе.
    5. **Файлы лежат в `catalog/`.** Этот каталог исключён из корпуса
       (`config/ragraf.yaml`), поэтому секретоносные файлы не попадают в индекс
       и в поисковую выдачу.

Формат файла токенов (`catalog/access_tokens.json`)::

    {
      "version": 1,
      "tokens": [
        {
          "token_id": "9f2a1c4b7e03",     # публичный идентификатор (в статистике)
          "label": "AutoClaw",            # КОМУ выдан — обязательное поле
          "owner": "владелец",            # кто выдал
          "note": "рабочая машина",       # зачем выдан
          "scopes": ["read"],             # read | write | admin
          "secret_sha256": "…",           # хэш секрета, НЕ секрет
          "created_at": "…",
          "expires_at": null,             # или ISO-метка
          "revoked_at": null,
          "last_used_at": null,
          "use_count": 0
        }
      ]
    }

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import util

# Репозиторий и каталог состояния: файлы доступа лежат рядом с реестром.
REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "catalog"

# ---------------------------------------------------------------------------
# Пароль администратора
# ---------------------------------------------------------------------------

# Итерации PBKDF2. Число выбрано так, чтобы проверка пароля занимала десятые
# доли секунды на этой машине: этого достаточно, чтобы перебор был бессмысленным,
# и мало, чтобы вход оставался мгновенным для человека.
PBKDF2_ITERATIONS = 600_000
PBKDF2_ALGORITHM = "pbkdf2_sha256"
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8


class AccessError(Exception):
    """Ошибка доступа или работы с хранилищем доступа.

    Текст этой ошибки показывается человеку, поэтому он обязан быть понятным
    и НЕ содержать секретов.
    """


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS,
                  salt: bytes | None = None) -> dict[str, Any]:
    """Собрать запись пароля: алгоритм, число итераций, соль, хэш.

    Формат записи намеренно самодостаточен: соль и число итераций хранятся
    рядом с хэшем, поэтому смена параметров не ломает уже сохранённые пароли.
    """
    if not password:
        raise AccessError("пароль пуст")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccessError(f"пароль короче {MIN_PASSWORD_LENGTH} символов")
    salt = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 iterations, dklen=32)
    return {
        "algorithm": PBKDF2_ALGORITHM,
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
        "created_at": util.now_iso(),
    }


def verify_password(password: str, record: dict[str, Any] | None) -> bool:
    """Проверить пароль против записи. Пустая запись — пароль не задан."""
    if not record or not isinstance(record, dict):
        return False
    try:
        salt = base64.b64decode(str(record.get("salt") or ""))
        expected = base64.b64decode(str(record.get("hash") or ""))
        iterations = int(record.get("iterations") or PBKDF2_ITERATIONS)
    except Exception:
        return False
    if not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                  iterations, dklen=len(expected))
    return hmac.compare_digest(digest, expected)


@dataclass
class PasswordStore:
    """Пароль администратора панели: опциональный.

    «Опционально» означает: пока файла нет, панель работает как раньше —
    по токену сессии, который печатается при запуске. Пароль добавляет
    стабильный вход, но не отменяет прежний путь (иначе владелец потерял бы
    доступ к уже запущенной панели).
    """

    path: Path = field(default_factory=lambda: CATALOG_DIR / "admin_auth.json")

    def load(self) -> dict[str, Any] | None:
        raw = util.read_json(self.path, default=None)
        if not isinstance(raw, dict):
            return None
        record = raw.get("password")
        return record if isinstance(record, dict) else None

    def is_set(self) -> bool:
        return self.load() is not None

    def set(self, password: str) -> None:
        """Задать или сменить пароль. Старый пароль проверяет вызывающий код."""
        record = hash_password(password)
        util.write_json(self.path, {"version": 1, "password": record,
                                    "updated_at": util.now_iso()}, indent=2)
        _restrict(self.path)

    def clear(self, current_password: str) -> None:
        """Снять пароль. Требуется текущий пароль: иначе снятие — обход защиты."""
        if not self.verify(current_password):
            raise AccessError("текущий пароль неверен")
        if self.path.exists():
            self.path.unlink()

    def verify(self, password: str) -> bool:
        return verify_password(password, self.load())

    def state(self) -> dict[str, Any]:
        """Состояние для интерфейса: есть пароль или нет, и когда задан."""
        record = self.load()
        return {
            "password_set": record is not None,
            "password_updated_at": (record or {}).get("created_at"),
            "min_length": MIN_PASSWORD_LENGTH,
        }


# ---------------------------------------------------------------------------
# Токены
# ---------------------------------------------------------------------------

SCOPES = ("read", "write", "admin")
DEFAULT_SCOPES = ("read",)
TOKEN_PREFIX = "ragraf_"
TOKEN_BYTES = 32
TOKEN_ID_BYTES = 6


def _hash_secret(secret: str) -> str:
    """SHA-256 секрета. Секрет не хранится — только этот хэш."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _restrict(path: Path) -> None:
    """Сузить права на файл, если система это позволяет.

    На Windows `chmod` отражается на атрибуте «только чтение», а не на списке
    доступа, поэтому результат не проверяется: неудача не должна мешать работе.
    Полноценное ограничение прав на Windows — задача администратора машины,
    и это записано в документации.
    """
    try:
        path.chmod(0o600)
    except Exception:
        pass


@dataclass
class TokenRecord:
    """Одна запись токена. Секрет здесь НЕ хранится — только его хэш."""

    token_id: str
    label: str
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    owner: str = ""
    note: str = ""
    secret_sha256: str = ""
    created_at: str = ""
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None
    use_count: int = 0
    last_result: str = ""
    issued_by: str = "admin-panel"

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "label": self.label,
            "owner": self.owner,
            "note": self.note,
            "scopes": list(self.scopes),
            "secret_sha256": self.secret_sha256,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "last_result": self.last_result,
            "issued_by": self.issued_by,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "TokenRecord":
        """Прочитать запись, устойчиво к отсутствующим полям."""
        scopes = raw.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            scopes = list(DEFAULT_SCOPES)
        return TokenRecord(
            token_id=str(raw.get("token_id") or ""),
            label=str(raw.get("label") or ""),
            scopes=[str(s) for s in scopes if str(s) in SCOPES] or list(DEFAULT_SCOPES),
            owner=str(raw.get("owner") or ""),
            note=str(raw.get("note") or ""),
            secret_sha256=str(raw.get("secret_sha256") or ""),
            created_at=str(raw.get("created_at") or ""),
            expires_at=raw.get("expires_at"),
            revoked_at=raw.get("revoked_at"),
            last_used_at=raw.get("last_used_at"),
            use_count=int(raw.get("use_count") or 0),
            last_result=str(raw.get("last_result") or ""),
            issued_by=str(raw.get("issued_by") or ""),
        )

    def public(self) -> dict[str, Any]:
        """Вид для интерфейса: без хэша секрета — он не нужен наружу."""
        data = self.to_dict()
        data.pop("secret_sha256", None)
        data["active"] = self.is_active()
        return data

    def is_active(self) -> bool:
        if self.revoked_at:
            return False
        if self.expires_at:
            try:
                from datetime import datetime

                if datetime.fromisoformat(self.expires_at) <= datetime.now(
                        datetime.fromisoformat(self.expires_at).tzinfo):
                    return False
            except Exception:
                # Нечитаемый срок не должен внезапно открывать доступ:
                # считаем токен истёкшим только при явном сравнении, а мусор
                # в поле — ошибка записи, а не разрешение.
                return False
        return True


@dataclass
class Principal:
    """Кто пришёл: администратор по паролю или агент по токену."""

    kind: str                 # "password" | "token" | "session"
    label: str                # кому выдан (для токена) или имя входа
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    token_id: str = ""

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "scopes": list(self.scopes),
                "token_id": self.token_id}


class TokenStore:
    """Хранилище именованных токенов.

    Файл рядом с реестром, в каталоге, исключённом из корпуса. Хэш секрета —
    единственное, что хранится, поэтому файл можно копировать для аудита
    без риска выдать чужой доступ.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (CATALOG_DIR / "access_tokens.json")

    # ---------------- чтение и запись ----------------

    def _read(self) -> dict[str, Any]:
        raw = util.read_json(self.path, default=None)
        if not isinstance(raw, dict):
            return {"version": 1, "tokens": []}
        tokens = raw.get("tokens")
        if not isinstance(tokens, list):
            tokens = []
        return {"version": 1, "tokens": tokens}

    def _write(self, data: dict[str, Any]) -> None:
        util.write_json(self.path, data, indent=2)
        _restrict(self.path)

    def all(self) -> list[TokenRecord]:
        return [TokenRecord.from_dict(t) for t in self._read()["tokens"]
                if isinstance(t, dict)]

    def get(self, token_id: str) -> TokenRecord | None:
        for rec in self.all():
            if rec.token_id == token_id:
                return rec
        return None

    def find_by_label(self, label: str) -> TokenRecord | None:
        wanted = label.strip().lower()
        for rec in self.all():
            if rec.label.strip().lower() == wanted:
                return rec
        return None

    # ---------------- создание ----------------

    def create(self, label: str, *, scopes: list[str] | None = None,
               owner: str = "", note: str = "",
               expires_at: str | None = None) -> tuple[TokenRecord, str]:
        """Создать токен. Возвращает запись и СЕКРЕТ (единственный раз).

        `label` — обязательное поле «кому выдан»: без него статистика по токенам
        бесполезна, а различить два токена в журнале будет нечем.
        """
        label = (label or "").strip()
        if not label:
            raise AccessError("укажите, кому выдан токен (поле «кому выдан» обязательно)")
        if len(label) > 120:
            raise AccessError("поле «кому выдан» длиннее 120 символов")
        if self.find_by_label(label) is not None:
            raise AccessError(f"токен для «{label}» уже существует: "
                              "отзовите его или дайте другое имя")

        wanted = [s for s in (scopes or DEFAULT_SCOPES) if s in SCOPES]
        unknown = [s for s in (scopes or []) if s not in SCOPES]
        if unknown:
            raise AccessError(f"неизвестные права: {', '.join(unknown)}; "
                              f"допустимы {', '.join(SCOPES)}")
        if not wanted:
            wanted = list(DEFAULT_SCOPES)

        secret = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        token_id = secrets.token_hex(TOKEN_ID_BYTES)
        record = TokenRecord(
            token_id=token_id,
            label=label,
            scopes=wanted,
            owner=(owner or "").strip(),
            note=(note or "").strip(),
            secret_sha256=_hash_secret(secret),
            created_at=util.now_iso(),
            expires_at=(expires_at or None),
        )

        data = self._read()
        data["tokens"].append(record.to_dict())
        self._write(data)
        # Секрет возвращается вызывающему и НЕ сохраняется: если он не показан
        # сразу, придётся создавать токен заново.
        return record, secret

    # ---------------- отзыв и удаление ----------------

    def revoke(self, token_id: str, *, reason: str = "") -> TokenRecord:
        """Отозвать токен: он остаётся в списке как история, но не работает."""
        data = self._read()
        found: dict[str, Any] | None = None
        for item in data["tokens"]:
            if isinstance(item, dict) and str(item.get("token_id")) == token_id:
                item["revoked_at"] = util.now_iso()
                if reason:
                    item["revoked_reason"] = reason
                found = item
                break
        if found is None:
            raise AccessError(f"токен не найден: {token_id}")
        self._write(data)
        return TokenRecord.from_dict(found)

    def restore(self, token_id: str) -> TokenRecord:
        """Снять отзыв: токен снова работает (секрет у владельца не изменился)."""
        data = self._read()
        found: dict[str, Any] | None = None
        for item in data["tokens"]:
            if isinstance(item, dict) and str(item.get("token_id")) == token_id:
                item["revoked_at"] = None
                item.pop("revoked_reason", None)
                found = item
                break
        if found is None:
            raise AccessError(f"токен не найден: {token_id}")
        self._write(data)
        return TokenRecord.from_dict(found)

    def delete(self, token_id: str) -> None:
        """Удалить запись токена целиком (историю использования это не трогает)."""
        data = self._read()
        before = len(data["tokens"])
        data["tokens"] = [t for t in data["tokens"]
                          if not (isinstance(t, dict)
                                  and str(t.get("token_id")) == token_id)]
        if len(data["tokens"]) == before:
            raise AccessError(f"токен не найден: {token_id}")
        self._write(data)

    # ---------------- проверка ----------------

    def authorize(self, secret: str) -> tuple[TokenRecord | None, bool, str]:
        """Проверить секрет. Возвращает (запись, допущен, причина отказа).

        Запись возвращается И ПРИ ОТКАЗЕ, если секрет соответствует известному
        токену (отозван или истёк). Это нужно журналу: владелец спрашивает
        «кто и как использовал», и попытка отозванного токена — это ответ,
        а не неизвестный вызов. Причина уходит В ЖУРНАЛ, а не клиенту:
        наружу все отказы выглядят одинаково.
        """
        given = (secret or "").strip()
        if not given:
            return None, False, "значение не передано"
        digest = _hash_secret(given)
        matched: TokenRecord | None = None
        # Сравнение постоянного времени по всем записям: выход по первому
        # совпадению раскрывал бы, сколько токенов заведено, и позволял
        # измерять время поиска.
        for rec in self.all():
            if rec.secret_sha256 and hmac.compare_digest(digest, rec.secret_sha256):
                matched = rec
        if matched is None:
            return None, False, "значение не соответствует ни одному токену"
        if matched.revoked_at:
            return matched, False, f"токен отозван ({matched.revoked_at})"
        if matched.expires_at and not matched.is_active():
            return matched, False, "срок действия токена истёк"
        return matched, True, ""

    def touch(self, token_id: str, *, result: str = "ok") -> None:
        """Отметить использование: время, счётчик и последний результат.

        Счётчик ведётся прямо в записи токена, чтобы список в панели отвечал
        на вопрос «сколько раз этим пользовались» без чтения всего журнала.
        """
        data = self._read()
        for item in data["tokens"]:
            if isinstance(item, dict) and str(item.get("token_id")) == token_id:
                item["last_used_at"] = util.now_iso()
                item["use_count"] = int(item.get("use_count") or 0) + 1
                item["last_result"] = result
                break
        else:
            return
        self._write(data)

    def state(self) -> dict[str, Any]:
        """Список токенов для интерфейса."""
        records = self.all()
        return {
            "scopes": list(SCOPES),
            "default_scopes": list(DEFAULT_SCOPES),
            "tokens": [r.public() for r in records],
            "count": len(records),
            "active": sum(1 for r in records if r.is_active()),
        }


# ---------------------------------------------------------------------------
# Журнал доступа
# ---------------------------------------------------------------------------

# Ограничение на длину значений в журнале: журнал не должен превращаться
# в свалку и не должен хранить тела запросов.
_DETAIL_LIMIT = 300


@dataclass
class AccessLog:
    """Журнал попыток доступа к панели и к MCP по HTTP.

    Append-only, как журнал расхождений: запись не переписывается и не удаляется.
    Хранится в control-хранилище рядом с телеметрией, поэтому сводка может
    связать обращение и его результат.
    """

    control: Any = None

    def ensure_schema(self) -> None:
        if self.control is None:
            return
        self.control.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_access_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                surface     TEXT NOT NULL,      -- panel | mcp-http | mcp-stdio
                operation   TEXT NOT NULL,
                token_id    TEXT,
                label       TEXT,
                principal   TEXT,               -- password | token | session | none
                remote      TEXT,
                result      TEXT NOT NULL,      -- ok | denied | error
                reason      TEXT,
                duration_ms REAL,
                detail      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_access_log_ts ON admin_access_log(ts);
            CREATE INDEX IF NOT EXISTS idx_access_log_token ON admin_access_log(token_id);
            """
        )
        self.control.conn.commit()

    def record(self, *, surface: str, operation: str, result: str,
               principal: Principal | None = None, remote: str = "",
               reason: str = "", duration_ms: float = 0.0,
               detail: str = "") -> None:
        """Записать попытку. Сбой записи не влияет на выполнение операции."""
        if self.control is None:
            return
        try:
            self.control.conn.execute(
                "INSERT INTO admin_access_log(ts, surface, operation, token_id,"
                " label, principal, remote, result, reason, duration_ms, detail)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    str(surface)[:40],
                    str(operation)[:120],
                    (principal.token_id if principal else "")[:40],
                    (principal.label if principal else "")[:120],
                    (principal.kind if principal else "none")[:20],
                    str(remote)[:64],
                    str(result)[:20],
                    str(reason)[:_DETAIL_LIMIT],
                    float(duration_ms or 0.0),
                    str(detail)[:_DETAIL_LIMIT],
                ),
            )
            self.control.conn.commit()
        except Exception:
            pass

    def summary(self, limit: int = 100, token_id: str | None = None) -> dict[str, Any]:
        """Сводка журнала: последние записи и разрез по токенам."""
        if self.control is None:
            return {"entries": [], "by_token": [], "count": 0}
        where = ""
        params: list[Any] = []
        if token_id:
            where = "WHERE token_id = ?"
            params.append(token_id)
        rows = self.control.conn.execute(
            f"SELECT ts, surface, operation, token_id, label, principal, remote,"
            f" result, reason, duration_ms, detail FROM admin_access_log {where}"
            f" ORDER BY id DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        entries = [
            {
                "ts": r[0], "surface": r[1], "operation": r[2], "token_id": r[3],
                "label": r[4], "principal": r[5], "remote": r[6], "result": r[7],
                "reason": r[8], "duration_ms": r[9], "detail": r[10],
            }
            for r in rows
        ]
        by_token = self.control.conn.execute(
            "SELECT COALESCE(NULLIF(label, ''), '(без имени)') AS label,"
            "       token_id,"
            "       COUNT(*) AS calls,"
            "       SUM(CASE WHEN result = 'ok' THEN 1 ELSE 0 END) AS ok_calls,"
            "       SUM(CASE WHEN result = 'denied' THEN 1 ELSE 0 END) AS denied_calls,"
            "       SUM(CASE WHEN result = 'error' THEN 1 ELSE 0 END) AS error_calls,"
            "       MAX(ts) AS last_ts"
            "  FROM admin_access_log"
            " GROUP BY label, token_id ORDER BY calls DESC"
        ).fetchall()
        return {
            "entries": entries,
            "by_token": [
                {"label": r[0], "token_id": r[1], "calls": r[2], "ok": r[3],
                 "denied": r[4], "errors": r[5], "last_ts": r[6]}
                for r in by_token
            ],
            "count": len(entries),
        }
