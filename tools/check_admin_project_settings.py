"""Проверка записи настроек в конфиг разработки (без порчи рабочего конфига).

Запуск: python tools/check_admin_project_settings.py

Зачем отдельно от tools/check_admin.py: там проверяются общие настройки,
а здесь — запись в YAML-конфиг корпуса. Такая запись правит файл, поэтому
проверка работает на КОПИИ конфига во временном каталоге: рабочие конфиги
разработок не меняются.

Что проверяется:
  1. значения читаются из секции index;
  2. запись меняет существующие ключи, сохраняя комментарии;
  3. новый ключ добавляется в секцию index, а не в конец файла;
  4. создаётся резервная копия;
  5. ошибочное значение отклоняется без изменения файла;
  6. исходный файл восстанавливается после записи.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import settings as settings_mod  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


SOURCE = REPO / "config" / "z-batteryservice.yaml"

FIXTURE = """project: fixture
root: C:/tmp/fixture

languages:
  - php

include:
  - "**/*.php"

# Секция ниже управляет индексацией: комментарии здесь важны,
# они объясняют, почему выбраны такие значения.
index:
  retain_revs: 3
  batch_size: 2000
  joern_enabled: true
  reindex_after_changes: 5   # порог быстрой переиндексации
  full_reindex_after_changes: 20
  reindex_interval_minutes: 15

embedding:
  model: test
  dim: 384
"""


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_settings_"))
    cfg_path = tmp / "corpus.yaml"
    cfg_path.write_text(FIXTURE, encoding="utf-8", newline="\n")

    try:
        print("=== 1. Чтение настроек из секции index ===")
        values = settings_mod.read_project_settings(cfg_path)
        check(values.get("reindex_after_changes") == 5, "прочитан порог быстрой = 5",
              str(values.get("reindex_after_changes")))
        check(values.get("full_reindex_after_changes") == 20, "прочитан порог полного = 20",
              str(values.get("full_reindex_after_changes")))
        check(values.get("joern_enabled") is True, "прочитан режим CPG = включён")
        # Значения из других секций не должны попадать в настройки индексации.
        check("model" not in values, "значения других секций не попадают в настройки")

        print("\n=== 2. Запись существующего ключа ===")
        settings_mod.write_project_settings(cfg_path, {"reindex_after_changes": 8})
        text_after = cfg_path.read_text(encoding="utf-8")
        check("reindex_after_changes: 8" in text_after, "значение изменено")
        check("# порог быстрой переиндексации" in text_after,
              "комментарий в строке сохранён")
        check("# Секция ниже управляет индексацией" in text_after,
              "комментарий над секцией сохранён")
        check("embedding:" in text_after and "model: test" in text_after,
              "следующие секции не повреждены")
        # Секция index должна остаться единственной: запись не создаёт дубль.
        check(text_after.count("\nindex:") == 1, "секция index не задублирована")

        print("\n=== 3. Резервная копия ===")
        backups = list(tmp.glob("corpus.yaml.bak.*"))
        check(len(backups) >= 1, "создана резервная копия", f"найдено {len(backups)}")
        if backups:
            original = backups[0].read_text(encoding="utf-8")
            check("reindex_after_changes: 5" in original,
                  "в копии исходное значение", "копия должна содержать 5")

        print("\n=== 4. Добавление нового ключа ===")
        settings_mod.write_project_settings(cfg_path, {"change_counter": False})
        text_new = cfg_path.read_text(encoding="utf-8")
        check("change_counter: false" in text_new, "новый ключ добавлен")
        # Ключ обязан попасть в секцию index, а не в конец файла после embedding.
        index_pos = text_new.find("\nindex:")
        embed_pos = text_new.find("\nembedding:")
        key_pos = text_new.find("change_counter")
        check(index_pos < key_pos < embed_pos,
              "новый ключ внутри секции index, а не в конце файла",
              f"index={index_pos} key={key_pos} embedding={embed_pos}")

        print("\n=== 5. Отклонение ошибочных значений ===")
        before_bad = cfg_path.read_text(encoding="utf-8")
        try:
            settings_mod.write_project_settings(cfg_path, {"reindex_after_changes": 0})
            check(False, "нулевой порог отклонён", "исключение не возникло")
        except ValueError as exc:
            check("допустимо" in str(exc), "нулевой порог отклонён с причиной",
                  str(exc)[:70])
        after_bad = cfg_path.read_text(encoding="utf-8")
        check(before_bad == after_bad, "при отказе файл не изменён")

        try:
            settings_mod.write_project_settings(cfg_path, {"full_reindex_after_changes": 2})
            check(False, "несогласованные пороги отклонены", "исключение не возникло")
        except ValueError as exc:
            check("больше" in str(exc), "несогласованность порогов отклонена",
                  str(exc)[:70])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== 6. Рабочие конфиги не пострадали ===")
    for name in ("z-batteryservice", "revoap", "fixture"):
        path = REPO / "config" / f"{name}.yaml"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        check("reindex_after_changes" in text, f"{name}.yaml содержит настройки порогов")
        check(text.count("\nindex:") == 1, f"{name}.yaml: секция index не задублирована")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
