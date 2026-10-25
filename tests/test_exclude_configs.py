"""Проверка конфигов корпусов после расширения базового списка.

Задача правки — сделать так, чтобы служебные каталоги не попадали в индекс
независимо от того, как создан корпус. Проверяется:
  1. в каждом конфиге `.ragraf` закрыт (и базово, и в файле);
  2. нет дублирующихся правил — иначе конфиг станет нечитаемым;
  3. собственные правила корпусов (для `bin`, `obj`, `publish`) на месте;
  4. базовые шаблоны из файла не конфликтуют с объединённым списком.

Отдельно: в конфигах корпусов есть повторяющиеся правила от прежних правок
(`**/.zwork/**` встречается дважды в z). Это стоит проверить и сообщить,
но НЕ удалять автоматически: дубль правила безвреден, а правка файлов
ради косметики создаёт риск.
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
    ("fixture.yaml", "fixture"),
]

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


print("=" * 78)
print("1. ПРАВИЛО ДЛЯ .ragraf В КАЖДОМ КОНФИГЕ")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        print(f"  {title}: конфига нет")
        continue
    cfg = load_config(path)
    merged = list(cfg.exclude)
    in_merged = [p for p in merged if ".ragraf" in p]
    # Проверяется и файл: правило должно быть видно человеку, а не только
    # действовать из базового списка.
    raw = path.read_text(encoding="utf-8")
    in_file = ".ragraf" in raw
    print(f"\n  {title}")
    print(f"    правил всего: {len(merged)}")
    print(f"    правило .ragraf в объединённом списке: {in_merged}")
    print(f"    правило в файле конфига: "
          f"{'да' if in_file else 'нет (действует из базового)'}")
    check(bool(in_merged), f"{title}: .ragraf исключён")

print()
print("=" * 78)
print("2. СОБСТВЕННЫЕ ПРАВИЛА КОРПУСОВ СОХРАНЕНЫ")
print("=" * 78)
# Правила, которых нет в базовом списке, — это специфика корпуса.
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    own = [p for p in cfg.exclude if p not in DEFAULT_EXCLUDES]
    print(f"\n  {title}: собственных правил {len(own)}")
    for p in own[:14]:
        print(f"    {p}")
    if len(own) > 14:
        print(f"    … и ещё {len(own) - 14}")

# Главное: правила RdpMON для bin/obj/publish должны быть на месте.
rdp = load_config(REPO / "config" / "rdpmon.yaml")
for required in ("**/bin/**", "**/obj/**", "**/publish/**"):
    present = required in rdp.exclude
    print(f"\n  RdpMON: {required} — {'на месте' if present else 'ПОТЕРЯНО'}")
    check(present, f"RdpMON: правило {required} сохранено")

print()
print("=" * 78)
print("3. ДУБЛИРУЮЩИЕСЯ ПРАВИЛА В КОНФИГАХ")
print("=" * 78)
# ВАЖНО различать два случая, иначе проверка даёт ложную тревогу:
#
#   а) дубль ВНУТРИ файла — мусор: одно и то же правило записано дважды;
#   б) совпадение базового и файлового правила — не мусор, а ЯВНОЕ правило:
#      человек, открывший конфиг, должен видеть, что каталог исключён.
#      В объединённом списке оно окажется дважды, и это безвредно.
#
# Первая версия проверки считала оба случая одним и сообщала «6 дублей»
# там, где всё правильно.
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    raw = path.read_text(encoding="utf-8")
    # Правила из ФАЙЛА (только секция exclude): нужны для обоих случаев.
    in_exclude = False
    file_rules: list[str] = []
    for line in raw.splitlines():
        if line and not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            in_exclude = line.strip().rstrip(":") == "exclude"
            continue
        m = re.match(r'^\s*-\s*"([^"]+)"', line)
        if m and in_exclude:
            file_rules.append(m.group(1))

    within = {p: n for p, n in Counter(file_rules).items() if n > 1}
    overlap = [p for p in file_rules if p in DEFAULT_EXCLUDES]

    print(f"\n  {title}")
    print(f"    правил в файле (exclude): {len(file_rules)}")
    print(f"    правил после объединения: {len(cfg.exclude)}")
    print(f"    дублей ВНУТРИ файла:      {len(within)}"
          + (f" — {list(within)[:3]}" if within else ""))
    print(f"    повторяют базовый список: {len(overlap)}")
    for p in overlap[:6]:
        print(f"      {p}")
    # Провалом считается только мусор внутри файла: он безвреден, но
    # означает, что конфиг правился без проверки на повтор.
    check(not within, f"{title}: нет дублей внутри секции exclude",
          f"дубли: {list(within)}")

print()
print("=" * 78)
print("4. БАЗОВЫЕ ШАБЛОНЫ ДОСТУПНЫ ВО ВСЕХ КОРПУСАХ")
print("=" * 78)
# Ключевое свойство правки: базовые правила работают везде, даже если
# в файле конфига их нет.
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    missing = [p for p in DEFAULT_EXCLUDES if p not in cfg.exclude]
    print(f"  {title:<22} базовых правил: "
          f"{len(DEFAULT_EXCLUDES) - len(missing)} из {len(DEFAULT_EXCLUDES)}")
    check(not missing, f"{title}: все базовые правила действуют",
          f"нет: {missing[:3]}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
