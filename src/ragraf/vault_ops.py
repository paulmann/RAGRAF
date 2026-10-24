"""Хранилище секретов на стороне MCP: тонкая обвязка над ``vault``.

Зачем отдельный модуль. Обработчики MCP-сервера возвращают конверт ответа
проекта (`op`, `found`, `result`, `error`), а хранилище говорит своими кодами
(`VaultError`). Смешивать это в сервере нельзя: сервер про секреты не знает
ничего, и любое знание там станет вторым источником правды о правах доступа.

Инварианты:

1. **Значение отдаёт ровно одна операция — ``secret_reveal``.** Остальные
   возвращают метаданные и маску. Это проверяется тестом по именам операций,
   а не по тексту ответа.
2. **Отказ не отличается от «не найдено» для чужой записи.** Код причины
   остаётся в аудите хранилища.
3. **Каждый вызов виден в статистике RAGRAF** через ``record_external_call``:
   иначе «сколько раз агенты брали пароли» не посчитать.
4. **Значение не попадает в статистику.** В журнал идёт ответ без поля
   ``value`` — маска и метаданные.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from . import vault as vault_mod


def _actor(server: Any, *, admin: bool = False):
    """Участник операции: имя агента берётся у сервера, а не выдумывается."""
    label = str(getattr(server, "agent", "") or "")
    return vault_mod.actor_from_env("mcp", label=label, client="stdio",
                                    admin=admin)


def _env(op: str, *, result: Any = None, error: dict | None = None) -> dict:
    """Конверт ответа MCP для операции хранилища.

    ``capability`` и ``provenance`` заполняются честно: это не факт индекса,
    а запись хранилища, и путать их с ``index_rev`` нельзя.
    """
    payload: dict[str, Any] = {
        "op": op,
        "capability": "secrets",
        "provenance": "ragraf-secrets (catalog/secrets.sqlite)",
        "found": error is None,
        "result": result if result is not None else {},
    }
    if error is not None:
        payload["error"] = error
    return payload


def _call(server: Any, op: str, fn: Callable[[], Any]) -> dict:
    """Выполнить операцию, записать её в статистику и не потерять причину."""
    started = time.perf_counter()
    try:
        result = fn()
        payload = _env(op, result=result)
        return payload
    except vault_mod.VaultError as exc:
        return _env(op, error={"code": exc.code, "message": exc.message})
    except Exception as exc:  # noqa: BLE001 - ответ, а не падение сервера
        return _env(op, error={"code": "internal",
                               "message": f"{type(exc).__name__}: {exc}"})
    finally:
        _remember(server, op, time.perf_counter() - started, locals().get("payload"))


def _remember(server: Any, op: str, elapsed: float, payload: Any) -> None:
    """Записать вызов в журнал статистики тем же путём, что `ensure`."""
    try:
        facade = server.facade()
        data = payload if isinstance(payload, dict) else {"op": op}
        # Значение в журнал не идёт никогда: копия ответа без поля value.
        safe = dict(data)
        result = safe.get("result")
        if isinstance(result, dict) and "value" in result:
            trimmed = dict(result)
            trimmed.pop("value", None)
            trimmed["value_revealed"] = True
            safe["result"] = trimmed
        facade.record_external_call(op, safe, elapsed * 1000.0)
    except Exception:  # noqa: BLE001 - статистика не должна ломать ответ
        pass


def _vault() -> vault_mod.Vault:
    return vault_mod.Vault()


def secret_list(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().list(
            actor=_actor(server),
            query=str(args.get("query") or ""),
            kind=str(args.get("kind") or ""),
            tag=str(args.get("tag") or ""),
            limit=int(args.get("limit", 200)),
        )

    return _call(server, "secret_list", run)


def secret_show(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().show(str(args.get("name") or ""), actor=_actor(server))

    return _call(server, "secret_show", run)


def secret_reveal(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().reveal(str(args.get("name") or ""),
                               actor=_actor(server),
                               reason=str(args.get("reason") or ""))

    return _call(server, "secret_reveal", run)


def secret_set(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().set(
            str(args.get("name") or ""),
            str(args.get("value") or ""),
            actor=_actor(server),
            kind=str(args.get("kind") or "password"),
            service=str(args.get("service") or ""),
            label=str(args.get("label") or ""),
            owner=str(args.get("owner") or ""),
            note=str(args.get("note") or ""),
            tags=args.get("tags") or [],
            project=str(args.get("project") or ""),
            visibility=args.get("visibility"),
            agents=args.get("agents") or [],
            expires_at=str(args.get("expires_at") or ""),
            captured=bool(args.get("captured")),
        )

    return _call(server, "secret_set", run)


def secret_delete(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().delete(str(args.get("name") or ""), actor=_actor(server))

    return _call(server, "secret_delete", run)


def secret_grant(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().grant(
            str(args.get("name") or ""),
            args.get("agents") or [],
            mode=args.get("visibility"),
            actor=_actor(server),
        )

    return _call(server, "secret_grant", run)


def secret_revoke(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().revoke(str(args.get("name") or ""),
                               args.get("agents") or [],
                               actor=_actor(server))

    return _call(server, "secret_revoke", run)


def secret_policy(server: Any, args: dict) -> dict:
    def run() -> Any:
        return _vault().policy()

    return _call(server, "secret_policy", run)


def cli_actor(*, admin: bool | None = None):
    """Участник для командной строки.

    Тонкость, важная для прав: если команду запускает АГЕНТ через оболочку, он
    выставляет ``RAGRAF_AGENT`` — и тогда прав владельца он не получает. Права
    владельца остаются только у вызова, в котором имя агента не задано, то есть
    у человека за клавиатурой.
    """
    env_name = (os.environ.get("RAGRAF_AGENT") or "").strip()
    # Обёртка `tools/ragraf.py` выставляет имя `ragraf-cli`; это сама
    # RAGRAF, то есть локальный оператор, а не сторонний агент. Права
    # владельца у локальной команды остаются в любом случае: она уже
    # владеет ключом, и делать вид, что это ограничение, было бы ложью.
    is_admin = True if admin is None else admin
    return vault_mod.actor_from_env("cli", label=(env_name or "owner"),
                                    admin=is_admin)
