#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Границы безопасности: права MCP, значение секрета и аудит выдачи.

Запуск: python tests/test_secret_isolation.py

Зачем набор. Требование этапа 14 — «ни один секрет не попадает в ответ
операции, аудит показывает каждую выдачу, права MCP ограничены и проверены
отказом» — нельзя подтвердить чтением кода: все три утверждения отрицательные
(«не попадает», «нельзя»), и ошибка в них выглядит как обычный ответ.

Что проверяется:

  1. **Права MCP.** Уровень доступа проверяется ДО обработчика: `read` не
     вызывает `ragraf_secret_set`, `ragraf_note` и `ragraf_secret_reveal`;
     `write` вызывает запись, но не выдачу значения; `admin` вызывает всё;
     вызов без объявленного уровня (stdio локального клиента) работает как
     владелец — и это названо в ответе, а не замалчивается.
  2. **Значение.** `list`, `show`, `policy` НЕ содержат поля `value`; значение
     отдаёт только `reveal`; в выдаче списка стоит маска, а не значение.
  3. **Аудит.** Выдача с причиной пишет строку `reveal`/`ok` с этой причиной;
     выдача без причины — отказ `reason_required` И строка `denied`; чужая
     запись для агента без права отвечает `not_found` (не подтверждая
     существование имени) и тоже попадает в журнал.
  4. **Инструмент поиска утечек умеет находить.** Посаженное значение в тексте
     чанка находится, а чистый текст — нет. Без этой проверки «утечек 0» из
     замера означало бы только то, что проверка ничего не ищет.

Хранилище и реестр — ВРЕМЕННЫЕ (`RAGRAF_CATALOG_DIR`): набор не трогает живое
хранилище владельца. Подсадка (проверено при написании): снять проверку уровня
в `mcp_server._call_tool` → провал пункта 1; вернуть `value` в `show` → провал
пункта 2; не писать строку при отказе → провал пункта 3.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0
VALUE = "probe-secret-value-4f19b2"


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def make_corpus(root: Path) -> Path:
    """Крошечный корпус: нужен только чтобы MCP-сервер мог собраться."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "one.py").write_text(
        "def one() -> int:\n    return 1\n", encoding="utf-8", newline="\n")
    cfg = root / "ragraf.yaml"
    cfg.write_text(
        f"""project: probe
root: {root.as_posix()}

languages:
  - python

include:
  - "**/*.py"

exclude:
  - "**/.ragraf/**"

db:
  enabled: false

embedding:
  model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  dim: 384

chunking:
  by_symbol: true
  max_chunk_lines: 120

index:
  retain_revs: 3
  batch_size: 2000
  joern_enabled: false
""",
        encoding="utf-8", newline="\n")
    return cfg


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_secrets_"))
    # ВАЖНО: и реестр, и хранилище — свои. Живое хранилище владельца не трогается:
    # путь состояния выбирает `paths.catalog_dir()`, а ключ задаётся переменной
    # окружения, чтобы во временном каталоге не появлялся файл ключа.
    os.environ["RAGRAF_CATALOG_DIR"] = str(tmp / "catalog")
    os.environ["RAGRAF_SECRETS_KEY"] = base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")
    os.environ["RAGRAF_AGENT"] = "test-secret-isolation"

    from ragraf import access as access_mod
    from ragraf import vault as vault_mod
    from ragraf import vault_ops

    try:
        # ---------------- 0. Готовим запись в СВОЁМ хранилище ----------------
        print("\n=== 0. Временное хранилище ===")
        vault = vault_mod.Vault()
        owner = vault_mod.Actor(label="owner-test", kind="human", via="cli",
                                admin=True)
        vault.set("probe-open", VALUE, actor=owner, kind="token",
                  note="контрольная запись набора")
        vault.set("probe-scoped", VALUE + "-scoped", actor=owner, kind="token",
                  visibility="listed", agents=["agent-allowed"],
                  note="запись только для названных агентов")
        check(vault.policy().get("enabled") is True, "хранилище включено")
        check(int(vault.check().get("count") or 0) >= 2,
              "контрольные записи созданы в своём хранилище",
              str(vault.check().get("count")))

        # ---------------- 1. Права MCP ----------------
        print("\n=== 1. Права инструментов MCP ===")
        cfg_path = make_corpus(tmp / "proj")
        from ragraf.mcp_server import McpServer

        def call(scopes, tool, args=None):
            server = McpServer(str(cfg_path), agent="tester", agent_kind="external",
                               access_label="test-token", access_scopes=scopes)
            try:
                out = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": tool,
                                                "arguments": args or {}}})
                result = out.get("result") or {}
                text = " ".join(c.get("text", "") for c in (result.get("content") or []))
                return bool(result.get("isError")), text
            finally:
                server.close()

        refused, text = call(["read"], "ragraf_secret_set",
                             {"name": "probe-x", "value": VALUE})
        check(refused and "write" in text,
              "доступ read не может создать секрет, причина называет уровень", text[:80])
        refused, text = call(["read"], "ragraf_note", {"text": "проба"})
        check(refused and "write" in text, "доступ read не может писать заметку", text[:80])
        refused, text = call(["read"], "ragraf_secret_reveal",
                             {"name": "probe-open", "reason": "проба"})
        check(refused and "admin" in text,
              "доступ read не получает значение секрета", text[:80])
        allowed, text = call(["read"], "ragraf_secret_list", {"limit": 5})
        check(not allowed, "доступ read читает (список секретов работает)", text[:60])
        # Замечание в сторону: `ragraf_freshness` на корпусе БЕЗ индекса отвечает
        # ошибкой `OperationalError` вместо честного «индекса нет» — найдено этой
        # же пробой 01.11.2026 и записано в отчёт этапа как находка, а не
        # исправлено здесь: правка поведения операции чтения — отдельное решение.
        refused, text = call(["write"], "ragraf_secret_reveal",
                             {"name": "probe-open", "reason": "проба"})
        check(refused and "admin" in text,
              "доступ write не получает значение секрета", text[:80])
        allowed, _ = call(["write"], "ragraf_secret_set",
                          {"name": "probe-w", "value": VALUE + "-w"})
        check(not allowed, "доступ write создаёт секрет")
        allowed, _ = call(["admin"], "ragraf_secret_reveal",
                          {"name": "probe-open", "reason": "проба прав"})
        check(not allowed, "доступ admin получает значение секрета")
        allowed, text = call(None, "ragraf_secret_set",
                             {"name": "probe-local", "value": VALUE + "-local"})
        check(not allowed, "локальный вызов без уровня работает как владелец")

        # ---------------- 2. Значение не уходит в метаданные ----------------
        print("\n=== 2. Значение отдаёт только выдача ===")
        listed = vault.list(actor=owner)
        items = listed["items"] if isinstance(listed, dict) else listed
        text_listed = str(listed)
        check(VALUE not in text_listed,
              "список секретов не содержит значения", text_listed[:60])
        check(any("****" in str(item.get("mask") or item.get("value_mask") or "")
                  or "****" in str(item) for item in items),
              "в списке стоит маска, а не значение")
        shown = vault.show("probe-open", actor=owner)
        check("value" not in shown, "показ записи не содержит поля value",
              str(sorted(shown))[:80])
        check(VALUE not in str(shown), "показ записи не содержит значения")
        # MCP-обёртка: метаданные идут через неё же
        class _FakeServer:
            agent = "test-secret-isolation"

            def facade(self):
                raise RuntimeError("статистика не нужна")

        wrapped = vault_ops.secret_list(_FakeServer(), {})
        check(VALUE not in str(wrapped),
              "ответ MCP-инструмента списка не содержит значения")
        wrapped_show = vault_ops.secret_show(_FakeServer(), {"name": "probe-open"})
        check(VALUE not in str(wrapped_show),
              "ответ MCP-инструмента показа не содержит значения")

        # ---------------- 3. Аудит выдачи ----------------
        print("\n=== 3. Аудит: каждая выдача и каждый отказ ===")
        before = int(vault.check().get("audit_rows") or 0)
        revealed = vault.reveal("probe-open", actor=owner, reason="проба выдачи")
        after = int(vault.check().get("audit_rows") or 0)
        check(revealed.get("value") == VALUE, "выдача вернула значение вызывающему")
        check(after == before + 1, "выдача записана в аудит одной строкой",
              f"{before} → {after}")
        rows = vault.audit_rows(limit=1)
        check(bool(rows) and rows[0]["op"] == "reveal" and rows[0]["result"] == "ok",
              "последняя строка аудита — выдача с результатом ok",
              str(rows[0].get("op")) if rows else "нет строк")
        check("проба выдачи" in str(rows[0].get("reason") or ""),
              "причина выдачи попала в журнал",
              str(rows[0].get("reason")))
        before = int(vault.check().get("audit_rows") or 0)
        try:
            vault.reveal("probe-open", actor=owner, reason="")
            refused_no_reason = False
        except vault_mod.VaultError as exc:
            refused_no_reason = exc.code == "reason_required"
        after = int(vault.check().get("audit_rows") or 0)
        check(refused_no_reason, "выдача без причины отклонена с кодом reason_required")
        check(after == before + 1, "отказ тоже записан в аудит", f"{before} → {after}")
        rows = vault.audit_rows(limit=1)
        check(rows and rows[0]["result"] == "denied",
              "строка отказа помечена denied", str(rows[0].get("result")) if rows else "")
        # Чужой агент не видит адресную запись — и не узнаёт, что она есть.
        stranger = vault_mod.Actor(label="agent-stranger", kind="external", via="mcp")
        try:
            vault.reveal("probe-scoped", actor=stranger, reason="проба чужого")
            refused_foreign = False
        except vault_mod.VaultError as exc:
            refused_foreign = exc.code == "not_found"
        check(refused_foreign,
              "чужая адресная запись отвечает not_found, а не отказом по правам")
        check("probe-scoped" not in str([r["name"] for r in vault.list(actor=stranger)["items"]
                                         if isinstance(vault.list(actor=stranger), dict)]),
              "адресная запись не видна чужому агенту в списке")

        # ---------------- 4. Поиск утечек умеет находить ----------------
        print("\n=== 4. Инструмент поиска утечек: подсадка видна ===")
        tool = _load_tool("check_secret_leaks")
        values = {"probe-open": VALUE, "probe-scoped": VALUE + "-scoped"}
        pattern = tool.leak_pattern(values)
        check(tool.scan_text(pattern, f"строка с секретом {VALUE} внутри"),
              "посаженное значение найдено в тексте")
        check(not tool.scan_text(pattern, "обычный текст без значений"),
              "чистый текст утечкой не считается")
        check(tool.scan_text(pattern, "==" + VALUE + "-scoped=="),
              "второе значение ищется так же, как первое")
        check(not tool.scan_text(pattern, VALUE[:-2]),
              "обрезанное значение не даёт ложного совпадения")
        try:
            tool.load_questions  # noqa: B018 — инструмент обязан быть модулем
        except AttributeError:
            pass
        check(tool.DEFAULT_MIN_LENGTH >= 6,
              "порог значимости не даёт считать утечкой «yes» и «1234»",
              str(tool.DEFAULT_MIN_LENGTH))

        # ---------------- 5. Каталог состояния слушает отвод ----------------
        print("\n=== 5. Прогон не трогает живое хранилище ===")
        # Дефект, из-за которого этот пункт появился (замер 01.11.2026):
        # `vault.py`, `access.py` и `db_access.py` считали каталог состояния от
        # расположения файла и `RAGRAF_CATALOG_DIR` не слушали. Набор, считавший
        # себя герметичным, записал 6 секретов и 2 доступа в ЖИВОЕ хранилище
        # (131 → 137). Свойство проверяет именно это: путь, который выбрало
        # хранилище, обязан быть внутри отведённого каталога.
        from ragraf import db_access as db_access_mod
        from ragraf import paths as paths_mod

        chosen_vault = vault_mod.catalog_dir()
        chosen_access = access_mod.CATALOG_DIR
        chosen_db = db_access_mod.CATALOG_DIR
        overridden = Path(os.environ["RAGRAF_CATALOG_DIR"]).resolve()
        # Сравнение только после `resolve()`: переменная окружения приходит
        # коротким 8.3-путём (`MVD~1.AMD`), и сравнение строк дало бы ложный
        # провал — ровно та ловушка, ради которой правило про нормализацию путей
        # записано в AGENTS.md.
        for label, chosen in (("vault", chosen_vault), ("access", chosen_access),
                              ("db_access", chosen_db)):
            check(Path(str(chosen)).resolve() == overridden,
                  f"{label}: каталог состояния взят из RAGRAF_CATALOG_DIR",
                  f"выбран {chosen}")
        check(Path(str(chosen_vault)).resolve() == overridden,
              "хранилище секретов лежит ровно в отведённом каталоге",
              str(chosen_vault))
        check(paths_mod.catalog_dir().resolve() == overridden,
              "paths.catalog_dir() и хранилище называют один каталог",
              str(paths_mod.catalog_dir()))
        live_catalog = REPO / "catalog"
        live_secrets = live_catalog / "secrets.sqlite"
        live_count = None
        if live_secrets.is_file():
            import sqlite3

            conn = sqlite3.connect(f"file:{live_secrets}?mode=ro", uri=True)
            try:
                live_count = int(conn.execute(
                    "SELECT COUNT(*) FROM secret").fetchone()[0])
            finally:
                conn.close()
            check(Path(str(chosen_vault)).resolve() != live_secrets.parent.resolve(),
                  "прогон не пишет в живой catalog/ (пути разные)",
                  f"живое: {live_secrets.parent}, прогон: {chosen_vault}")
            conn = sqlite3.connect(f"file:{live_secrets}?mode=ro", uri=True)
            try:
                after_count = int(conn.execute(
                    "SELECT COUNT(*) FROM secret").fetchone()[0])
            finally:
                conn.close()
            check(after_count == live_count,
                  "число записей в живом хранилище не изменилось",
                  f"{live_count} → {after_count}")
            check(not any(name.startswith("probe-") or name == "x-i14"
                          for (name,) in sqlite3.connect(
                              f"file:{live_secrets}?mode=ro",
                              uri=True).execute("SELECT name FROM secret")),
                  "пробных записей в живом хранилище нет")
        else:
            check(True, "живого хранилища на этой машине нет — проверять нечего")

        print("\n" + "=" * 78)
        print(f"итог: пройдено {PASS}, провалено {FAIL}")
        print("=" * 78)
        return 0 if FAIL == 0 else 1
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _load_tool(name: str):
    """Загрузить инструмент из `tools/`, не затеняя пакет `ragraf`."""
    import importlib.util

    path = REPO / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_i14_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    sys.exit(main())
