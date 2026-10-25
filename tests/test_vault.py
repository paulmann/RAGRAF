"""Проверки хранилища секретов: крипто, права агентов, аудит, бэкап.

Зачем отдельный набор. Хранилище — единственное место в проекте, где значение
секрета лежит на диске. Ошибка здесь не «неудобство», а утечка, поэтому
проверяются СВОЙСТВА, а не текст реализации:

* шифротекст нельзя переставить между записями (контекстная привязка);
* чужой ключ не расшифровывает;
* агент без права получает ТОТ ЖЕ ответ, что на несуществующее имя;
* журнал нельзя переписать (append-only на уровне базы);
* значение не появляется ни в одном ответе, кроме reveal;
* ротация и снимок не теряют записи.

Запуск: python tests/test_vault.py
Код возврата 0 — все проверки прошли; 1 — есть провал (печатается причина).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ragraf import settings as settings_mod  # noqa: E402
from ragraf import vault as vault_mod  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def expect_error(name: str, code: str, fn) -> None:
    try:
        fn()
    except vault_mod.VaultError as exc:
        check(name, exc.code == code, f"код {exc.code}, ожидался {code}")
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"другое исключение: {type(exc).__name__}: {exc}")
    else:
        check(name, False, "исключения не было")


class Env:
    """Песочница: свой каталог, свой ключ, свои настройки."""

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="ragraf-vault-")
        self.key = vault_mod._random.token_bytes(32)
        self.settings = dict(settings_mod.DEFAULTS)
        self.settings.update({
            "secrets_enabled": True,
            "secrets_capture_enabled": False,
            "secrets_default_visibility": "all",
            "secrets_reveal_requires_reason": False,
            "secrets_max_value_bytes": 8192,
            "secrets_allowed_agents": [],
            "secrets_expiry_warn_days": 30,
        })
        self.db = os.path.join(self.dir, "secrets.sqlite")

    def vault(self, *, master: bytes | None = None) -> vault_mod.Vault:
        # Настройки передаются ССЫЛКОЙ, а не копией: иначе проверка «включил
        # настройку — поведение изменилось» проверяла бы снимок словаря и
        # всегда видела бы прежнее значение (так и случилось на первом прогоне).
        return vault_mod.Vault(path=self.db, master=master or self.key,
                               settings=self.settings)

    def actor(self, label: str = "AutoClaw", admin: bool = False):
        return vault_mod.Actor(label=label, kind="autoclaw", via="cli",
                              admin=admin)

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def main() -> int:
    print("хранилище секретов: свойства")
    env = Env()
    try:
        v = env.vault()

        # --- 1. Крипто: roundtrip и запрет чужих операций ------------------
        print("1. шифрование")
        meta = v.set("db/pg/prod", "S3cr3t-Значение-1", actor=env.actor("AutoClaw"),
                     kind="password", service="postgres", tags=["prod", "db"],
                     expires_at="2030-01-01T00:00:00Z")
        check("запись создана", meta["name"] == "db/pg/prod")
        check("в ответе маска, а не значение",
              meta.get("masked") == "****" and "value" not in meta)
        check("длина значения сохранена", meta["value_len"] ==
              len("S3cr3t-Значение-1".encode("utf-8")))
        got = v.reveal("db/pg/prod", actor=env.actor("AutoClaw"))
        check("значение вернулось целым", got["value"] == "S3cr3t-Значение-1")

        with sqlite3.connect(env.db) as conn:
            row = conn.execute("SELECT nonce, ciphertext FROM secret WHERE name=?",
                               ("db/pg/prod",)).fetchone()
        check("в базе нет открытого значения",
              "S3cr3t" not in row[1] and len(row[1]) > 20)

        # --- 2. Чужой ключ ------------------------------------------------
        print("2. чужой ключ и перестановка шифротекста")
        other = env.vault(master=vault_mod._random.token_bytes(32))
        expect_error("чужим ключом не расшифровать", "ciphertext_invalid",
                     lambda: other.reveal("db/pg/prod", actor=env.actor("AutoClaw")))

        v.set("db/mysql/test", "Second-Value-2", actor=env.actor("AutoClaw"))
        with sqlite3.connect(env.db) as conn:
            c1 = conn.execute("SELECT ciphertext FROM secret WHERE name=?",
                              ("db/pg/prod",)).fetchone()[0]
            c2 = conn.execute("SELECT ciphertext FROM secret WHERE name=?",
                              ("db/mysql/test",)).fetchone()[0]
            conn.execute("UPDATE secret SET ciphertext=? WHERE name=?",
                         (c2, "db/pg/prod"))
            conn.execute("UPDATE secret SET ciphertext=? WHERE name=?",
                         (c1, "db/mysql/test"))
            conn.commit()
        expect_error("шифротекст, переставленный между записями, отвергнут",
                     "ciphertext_invalid",
                     lambda: v.reveal("db/pg/prod", actor=env.actor("AutoClaw")))
        check("отказ по подлинности, а не выдача чужого значения",
              _code_of(lambda: v.reveal("db/pg/prod",
                                        actor=env.actor("AutoClaw")))
              == "ciphertext_invalid")
        with sqlite3.connect(env.db) as conn:
            conn.execute("UPDATE secret SET ciphertext=? WHERE name=?",
                         (c1, "db/pg/prod"))
            conn.execute("UPDATE secret SET ciphertext=? WHERE name=?",
                         (c2, "db/mysql/test"))
            conn.commit()
        check("после восстановления запись снова читается",
              v.reveal("db/pg/prod", actor=env.actor("AutoClaw"))["value"]
              == "S3cr3t-Значение-1")

        # --- 3. Права: по умолчанию всем ---------------------------------
        print("3. права по умолчанию и адресные списки")
        check("по умолчанию доступен другому агенту",
              v.reveal("db/pg/prod", actor=env.actor("HARNESS"))["value"]
              == "S3cr3t-Значение-1")

        v.set("only/one", "OnlyOne-3", actor=env.actor("AutoClaw"),
              visibility="listed", agents=["HARNESS"])
        check("перечисленному агенту доступен",
              v.reveal("only/one", actor=env.actor("HARNESS"))["value"] == "OnlyOne-3")
        expect_error("неперечисленному отказано как «не найдено»", "not_found",
                     lambda: v.reveal("only/one", actor=env.actor("ДругойАгент")))
        check("отказ не подтверждает существование имени",
              "not_found" == _code_of(lambda: v.reveal(
                  "only/one", actor=env.actor("ДругойАгент"))))

        v.set("only/owner", "OwnerOnly-4", actor=env.actor("AutoClaw"),
              visibility="owner_only")
        check("автору доступна своя запись",
              v.reveal("only/owner", actor=env.actor("AutoClaw"))["value"]
              == "OwnerOnly-4")
        expect_error("другому агенту owner_only закрыт", "not_found",
                     lambda: v.reveal("only/owner", actor=env.actor("HARNESS")))

        granted = v.grant("only/owner", ["HARNESS"], actor=env.actor("AutoClaw"))
        check("grant переводит запись в режим списка",
              granted["visibility"] == "listed" and granted["agents"] == ["HARNESS"])
        check("после grant агент читает",
              v.reveal("only/owner", actor=env.actor("HARNESS"))["value"]
              == "OwnerOnly-4")
        revoked = v.revoke("only/owner", ["HARNESS"], actor=env.actor("AutoClaw"))
        check("revoke без остатка переводит в owner_only",
              revoked["visibility"] == "owner_only")
        expect_error("после revoke доступ закрыт", "not_found",
                     lambda: v.reveal("only/owner", actor=env.actor("HARNESS")))

        # --- 4. Список: чужие записи не видны ------------------------------
        print("4. список и скрытые записи")
        listing = v.list(actor=env.actor("Посторонний"))
        names = [item["name"] for item in listing["items"]]
        check("чужие скрытые записи не попали в список",
              "only/owner" not in names, str(names))
        check("счётчик скрытых честный", listing["hidden"] >= 1)
        owner_list = v.list(actor=env.actor("AutoClaw", admin=True),
                            include_hidden=True)
        check("администратор видит всё",
              {item["name"] for item in owner_list["items"]} >= {"only/owner"})
        shown = v.show("db/pg/prod", actor=env.actor("AutoClaw"))
        check("show не содержит значения", "value" not in shown)
        check("в списке видно, кто добавил",
              shown["created_by"] == "AutoClaw" and shown["created_by_via"] == "cli")

        # --- 5. Аудит ------------------------------------------------------
        print("5. аудит append-only")
        rows = v.audit_rows(limit=100)
        ops = {row["op"] for row in rows}
        check("в журнале есть reveal/set/grant/revoke",
              {"reveal", "set", "grant", "revoke"} <= ops, str(ops))
        check("в журнале нет значений",
              all("S3cr3t" not in str(row) for row in rows))
        try:
            with sqlite3.connect(env.db) as conn:
                conn.execute("UPDATE audit SET result='denied' WHERE id=1")
                conn.commit()
            check("журнал нельзя переписать", False, "UPDATE прошёл")
        except sqlite3.Error as exc:
            check("журнал нельзя переписать", "append-only" in str(exc).lower()
                  or "abort" in str(exc).lower(), str(exc))
        try:
            with sqlite3.connect(env.db) as conn:
                conn.execute("DELETE FROM audit WHERE id=1")
                conn.commit()
            check("журнал нельзя удалить", False, "DELETE прошёл")
        except sqlite3.Error:
            check("журнал нельзя удалить", True)

        # --- 6. Настройки и ограничения ------------------------------------
        print("6. настройки, типы, пределы")
        expect_error("причина обязательна, когда требуется", "reason_required",
                     lambda: _with_reason_required(env, "db/pg/prod"))
        expect_error("неизвестный тип отвергнут", "bad_kind",
                     lambda: v.set("bad/kind", "x", actor=env.actor("AutoClaw"),
                                   kind="что-то"))
        env.settings["secrets_max_value_bytes"] = 8
        expect_error("слишком длинное значение отвергнуто", "value_too_large",
                     lambda: v.set("big/value", "0123456789", actor=env.actor("AutoClaw")))
        env.settings["secrets_max_value_bytes"] = 8192
        expect_error("режим listed без списка отвергнут", "listed_empty",
                     lambda: v.set("empty/list", "x", actor=env.actor("AutoClaw"),
                                   visibility="listed", agents=[]))
        expect_error("сохранение «встреченного» без настройки отвергнуто",
                     "capture_disabled",
                     lambda: v.set("captured/one", "x", actor=env.actor("AutoClaw"),
                                   captured=True))
        env.settings["secrets_capture_enabled"] = True
        captured = v.set("captured/two", "y", actor=env.actor("AutoClaw"),
                         captured=True)
        check("при включённой настройке сохранение проходит", captured["name"])
        env.settings["secrets_capture_enabled"] = False

        env.settings["secrets_enabled"] = False
        expect_error("выключенное хранилище запрещает операции", "disabled",
                     lambda: v.list(actor=env.actor("AutoClaw")))
        env.settings["secrets_enabled"] = True

        env.settings["secrets_allowed_agents"] = ["AutoClaw"]
        expect_error("общий список разрешённых агентов отсекает чужих",
                     "not_found",
                     lambda: v.reveal("db/pg/prod", actor=env.actor("HARNESS")))
        env.settings["secrets_allowed_agents"] = []

        # --- 7. Срок годности ---------------------------------------------
        print("7. срок годности")
        v.set("cert/old", "cert-body", actor=env.actor("AutoClaw"),
              kind="certificate", expires_at="2020-01-01T00:00:00Z")
        old = v.show("cert/old", actor=env.actor("AutoClaw"))
        check("просроченная запись помечена", old["expired"] is True)
        v.set("cert/soon", "cert-body-2", actor=env.actor("AutoClaw"),
              kind="certificate", expires_at="2030-01-01T00:00:00Z")
        soon = v.show("cert/soon", actor=env.actor("AutoClaw"))
        check("далёкий срок не считается скорым", soon["expiring_soon"] is False)

        # --- 8. Ротация ----------------------------------------------------
        print("8. ротация ключа")
        before = v.reveal("db/pg/prod", actor=env.actor("AutoClaw"))["value"]
        rotated = v.rotate_key(actor=env.actor("AutoClaw"))
        check("ротация отчиталась", rotated["rotated"] is True)
        check("после ротации значение читается через новый ключ",
              v.reveal("db/pg/prod", actor=env.actor("AutoClaw"))["value"] == before)
        restore = env.vault(master=env.key)
        expect_error("старый ключ после ротации не подходит", "ciphertext_invalid",
                     lambda: restore.reveal("db/pg/prod", actor=env.actor("AutoClaw")))

        # --- 9. Снимок -----------------------------------------------------
        print("9. шифрованный снимок")
        snap = os.path.join(env.dir, "snap.json")
        exported = v.export(snap, "phrase-1", actor=env.actor("AutoClaw"))
        check("снимок создан", exported["records"] >= 3)
        raw = open(snap, encoding="utf-8").read()
        check("в снимке нет открытого значения", "S3cr3t" not in raw)
        v.delete("db/mysql/test", actor=env.actor("AutoClaw"))
        restored = v.import_(snap, "phrase-1", actor=env.actor("AutoClaw"))
        check("импорт вернул запись", restored["imported"] >= 3)
        check("удалённая запись восстановлена",
              v.reveal("db/mysql/test", actor=env.actor("AutoClaw"))["value"]
              == "Second-Value-2")
        expect_error("неверная фраза снимка отвергнута", "bad_passphrase",
                     lambda: v.import_(snap, "phrase-2", actor=env.actor("AutoClaw")))

        # --- 10. Целостность ------------------------------------------------
        print("10. проверка целостности")
        report = v.check()
        check("check проходит на целом хранилище", report["ok"] is True,
              str(report.get("problems")))
        with sqlite3.connect(env.db) as conn:
            conn.execute("UPDATE secret SET ciphertext='AAAA' WHERE name=?",
                         ("db/pg/prod",))
            conn.commit()
        broken = v.check()
        check("check видит повреждённую запись", broken["ok"] is False
              and any("db/pg/prod" in p for p in broken["problems"]))

        # --- 11. Редактирование --------------------------------------------
        print("11. замазывание секретов в тексте")
        text = ("host=db\npassword=S3cr3t-Значение-1\nauthorization: Bearer "
                "abcdef123456789\n---BEGIN RSA PRIVATE KEY--- x")
        masked = vault_mod.redact(text, values=["S3cr3t-Значение-1"])
        check("известное значение замазано", "S3cr3t" not in masked)
        check("заголовок авторизации замазан", "abcdef123456789" not in masked)
        check("остальной текст сохранён", "host=db" in masked)

        # --- 12. Настройки в модуле settings --------------------------------
        print("12. блок настроек")
        for key in ("secrets_enabled", "secrets_capture_enabled",
                    "secrets_default_visibility", "secrets_reveal_requires_reason",
                    "secrets_max_value_bytes", "secrets_allowed_agents",
                    "secrets_expiry_warn_days"):
            check(f"настройка {key} объявлена",
                  key in settings_mod.DEFAULTS and key in settings_mod.STORE_KEYS
                  and key in settings_mod.DESCRIPTIONS)
        check("видимость по умолчанию — всем",
              settings_mod.DEFAULTS["secrets_default_visibility"] == "all")
        check("сохранение встреченного по умолчанию выключено",
              settings_mod.DEFAULTS["secrets_capture_enabled"] is False)
    finally:
        env.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО {len(FAILED)} проверок, прошло {PASSED}:")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"все проверки прошли: {PASSED}")
    return 0


def _code_of(fn) -> str:
    try:
        fn()
    except vault_mod.VaultError as exc:
        return exc.code
    return ""


def _with_reason_required(env: Env, name: str) -> None:
    env.settings["secrets_reveal_requires_reason"] = True
    try:
        env.vault().reveal(name, actor=env.actor("AutoClaw"))
    finally:
        env.settings["secrets_reveal_requires_reason"] = False


if __name__ == "__main__":
    sys.exit(main())
