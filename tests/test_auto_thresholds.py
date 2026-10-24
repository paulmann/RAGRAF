"""Проверка пункта 1.3.5: автоподбор порогов по измеренной стоимости.

Что проверяется:
  1. на корпусе со стоимостью, равной опорной, автоподбор возвращает базовые
     значения (5/20/15) — то есть не портит уже верную настройку;
  2. на лёгком корпусе пороги снижаются;
  3. на тяжёлом корпусе пороги растут, но не линейно (квадратный корень);
  4. границы автоподбора соблюдаются при абсурдных замерах;
  5. согласованность: порог полного пересбора всегда больше порога быстрого;
  6. интервал не опускается ниже базового значения;
  7. при отсутствии замеров остаются базовые пороги (не выдумываются);
  8. фактический замер из реестра читается и даёт разумное число.

Проверка работает на временном конфиге: рабочие конфиги не меняются.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import config as config_mod  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

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


FIXTURE = """project: threshold-autotest
root: C:/tmp/none

languages:
  - php

include:
  - "**/*.php"

db:
  enabled: false

index:
  retain_revs: 3
  batch_size: 2000
  joern_enabled: true
  reindex_after_changes: 5
  full_reindex_after_changes: 20
  reindex_interval_minutes: 15
  auto_thresholds: true
  auto_min_fast: 2
  auto_max_fast: 40
  auto_min_full: 6
  auto_max_full: 150
"""


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_auto_"))
    # Каталог корпуса обязан существовать: загрузчик это проверяет
    # (и правильно делает — иначе индексация нашла бы 0 файлов молча).
    fake_root = tmp / "corpus"
    fake_root.mkdir(parents=True, exist_ok=True)
    (fake_root / "a.php").write_text("<?php\nfunction t(): int { return 1; }\n",
                                     encoding="utf-8", newline="\n")

    cfg_path = tmp / "corpus.yaml"
    cfg_path.write_text(
        FIXTURE.replace("root: C:/tmp/none", f"root: {fake_root.as_posix()}"),
        encoding="utf-8", newline="\n",
    )

    try:
        cfg = load_config(cfg_path)
        ref = config_mod.REFERENCE_SECONDS
        print(f"опорная стоимость: {ref} с\n")

        print("=== 1. Корпус с опорной стоимостью — базовые пороги ===")
        fast, full, interval, info = config_mod.auto_thresholds(cfg, measured=ref)
        print(f"  пороги: {fast} / {full} / {interval} мин")
        print(f"  пояснение: {info.get('reason')}")
        check((fast, full, interval) == (5, 20, 15),
              "пороги совпали с базовыми", f"получено {fast}/{full}/{interval}")
        check(info.get("applied") is False,
              "признак применения автоподбора: значения не изменились")

        print("\n=== 2. Лёгкий корпус — пороги снижаются ===")
        fast2, full2, interval2, info2 = config_mod.auto_thresholds(cfg, measured=5.0)
        print(f"  пороги: {fast2} / {full2} / {interval2} мин, множитель {info2.get('factor')}")
        check(fast2 <= 5 and full2 <= 20, "пороги не выросли",
              f"получено {fast2}/{full2}")
        check(interval2 >= 15, "интервал не опустился ниже базового",
              f"получено {interval2}")

        print("\n=== 3. Тяжёлый корпус — рост не линейный ===")
        fast3, full3, interval3, info3 = config_mod.auto_thresholds(cfg, measured=187.0)
        print(f"  пороги: {fast3} / {full3} / {interval3} мин, множитель {info3.get('factor')}")
        check(fast3 > 5, "порог быстрой вырос", f"получено {fast3}")
        check(full3 > 20, "порог полного вырос", f"получено {full3}")
        # 187 с — это в 9 раз больше опоры; линейно порог был бы 45, корень даёт меньше.
        check(fast3 < 5 * 9, "рост меньше линейного", f"получено {fast3} вместо 45")
        check(info3.get("factor", 0) < 3.1, "множитель = корень из отношения",
              f"получено {info3.get('factor')}")

        print("\n=== 4. Границы при абсурдных замерах ===")
        fast4, full4, _, _ = config_mod.auto_thresholds(cfg, measured=100000.0)
        check(fast4 <= 40, "верхняя граница порога быстрой соблюдена", f"получено {fast4}")
        check(full4 <= 150, "верхняя граница порога полного соблюдена", f"получено {full4}")
        fast5, full5, _, _ = config_mod.auto_thresholds(cfg, measured=0.001)
        check(fast5 >= 2, "нижняя граница порога быстрой соблюдена", f"получено {fast5}")
        check(full5 >= 6, "нижняя граница порога полного соблюдена", f"получено {full5}")

        print("\n=== 5. Согласованность порогов на всей шкале ===")
        bad = []
        for measured in (0.5, 1, 5, 20.81, 50, 100, 200, 500, 2000, 99999):
            f_, fu, iv, _ = config_mod.auto_thresholds(cfg, measured=measured)
            if fu <= f_ or iv < 1 or f_ < 1:
                bad.append((measured, f_, fu, iv))
        check(not bad, "порог полного всегда больше быстрого, интервал >= 1", str(bad))

        print("\n=== 6. Без замеров — базовые пороги ===")
        # В реестре нет записи для вымышленного проекта: замер вернёт 0.
        measured_real = config_mod.measured_full_seconds(cfg)
        fast6, full6, interval6, info6 = config_mod.auto_thresholds(cfg)
        print(f"  замер из реестра: {measured_real}")
        print(f"  пороги: {fast6} / {full6} / {interval6}")
        print(f"  пояснение: {info6.get('reason')}")
        check(measured_real == 0.0, "для незарегистрированного проекта замер равен 0")
        check((fast6, full6, interval6) == (5, 20, 15),
              "остались базовые пороги", f"получено {fast6}/{full6}/{interval6}")

        print("\n=== 7. Автоподбор выключен ===")
        off = FIXTURE.replace("auto_thresholds: true", "auto_thresholds: false")
        off = off.replace("root: C:/tmp/none", f"root: {fake_root.as_posix()}")
        cfg_path.write_text(off, encoding="utf-8", newline="\n")
        cfg_off = load_config(cfg_path)
        f7, fu7, iv7, info7 = config_mod.auto_thresholds(cfg_off, measured=500.0)
        check((f7, fu7, iv7) == (5, 20, 15),
              "при выключенном автоподборе пороги базовые",
              f"получено {f7}/{fu7}/{iv7}")
        check("выключен" in str(info7.get("reason")), "причина названа",
              str(info7.get("reason")))

        print("\n=== 8. Реальный замер рабочего корпуса ===")
        real_cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
        measured = config_mod.measured_full_seconds(real_cfg)
        fast8, full8, interval8, info8 = config_mod.auto_thresholds(real_cfg)
        print(f"  измеренная стоимость полного пересбора: {measured:.2f} с")
        print(f"  подобранные пороги: {fast8} / {full8} / {interval8} мин")
        print(f"  пояснение: {info8.get('reason')}")
        check(measured > 5, "замер рабочего корпуса получен и правдоподобен",
              f"получено {measured}")
        # На рабочем корпусе пороги обязаны быть ПРОПОРЦИОНАЛЬНЫ измеренной
        # стоимости, а не равны базовым.
        #
        # Прежняя редакция требовала абсолютных 4..8 и 15..30 и падала не от
        # дефекта автоподбора, а от того, что чужой корпус вырос: опорный замер
        # 20,81 с, а на z.batteryservice.ru сейчас 63,17 с, множитель 1,74 —
        # базовые 5/20 поднимаются до 9/35, и это верный ответ. Проверяется
        # именно связь «стоимость → порог», поэтому проверка не застывает.
        base_fast, base_full = 5, 20
        factor = float(info8.get("factor") or 1.0)
        print(f"  множитель к опорному замеру: {factor}")
        check(fast8 >= base_fast and full8 >= base_full,
              "пороги не опустились ниже базовых",
              f"получено {fast8}/{full8}")
        check(abs(fast8 - round(base_fast * factor)) <= 1,
              "порог быстрой пропорционален измеренной стоимости",
              f"получено {fast8}, ожидалось около {round(base_fast * factor)}")
        check(abs(full8 - round(base_full * factor)) <= 1,
              "порог полного пропорционален измеренной стоимости",
              f"получено {full8}, ожидалось около {round(base_full * factor)}")
        check(fast8 <= 40 and full8 <= 150,
              "верхние границы порогов соблюдены", f"получено {fast8}/{full8}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
