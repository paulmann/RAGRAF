"""Генератор чисел поверхности и гейт: одно число — из кода, не из текста.

Зачем. Числа (операции, инструменты, подкоманды, языки, форматы, возможности)
жили в текстах вручную и расходились с кодом молча. Теперь число в документе
допустимо ТОЛЬКО между маркерами:

    <!-- surface:figures -->**{ops} operations** …<!-- /surface:figures -->

а содержимое рендерится шаблоном из `tools/surface_templates.json` по числам
`src/ragraf/surface.py`. Всё вне маркеров — история: она не проверяется и не
переписывается.

Режимы:
  --check   сравнить содержимое маркеров и отпечаток `docs/surface.json`;
            расхождение — находка и код 1;
  --write   переписать содержимое маркеров и артефакт; идемпотентно;
  --mark    однократная разметка: обернуть маркерами найденный шаблонный текст
            (нужна при добавлении нового документа в список);
  --json    машинный ответ вместо строк.

Границы, названные прямо:
  * документы вне списка шаблонов не осматриваются и не переписываются:
    числа навыка (`docs/skills/**`) проверяет `tools/check_skill_variants.py`,
    статья и прежние отчёты — история;
  * alt-текст изображения (`![...](...)`) не размечается: HTML-комментарий
    внутри alt попал бы в текст для чтения с экрана. Число в alt меняется
    руками при выпуске, и это названо, а не спрятано;
  * ревизия в артефакте обновляется, но в отпечаток не входит: иначе каждая
    переиндексация делала бы гейт красным на неизменном коде.

Запуск:
    python tools/surface_gen.py --check
    python tools/surface_gen.py --write
    python tools/surface_gen.py --mark
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "tools" / "surface_templates.json"
ARTIFACT = Path("docs") / "surface.json"

MARKER = re.compile(r"<!-- surface:([a-z_]+) -->(.*?)<!-- /surface:\1 -->", re.S)


def load_templates() -> dict[str, dict[str, str]]:
    data = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    docs = data.get("docs")
    if not isinstance(docs, dict) or not docs:
        raise SystemExit("ОТКАЗ: в surface_templates.json нет раздела docs")
    return {str(name): {str(slot): str(text) for slot, text in slots.items()}
            for name, slots in docs.items()}


def load_surface(root: Path):
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from ragraf import surface  # noqa: PLC0415 — путь к корню известен только здесь

    return surface


def ru_plural(value: int, one: str, few: str, many: str) -> str:
    """Форма русского слова для числа. Без неё шаблон верен лишь части чисел.

    Замер: маркер печатал «**71 операций**», потому что форма слова стояла в
    шаблоне. Русское склонение зависит от числа, поэтому решает код, а текст
    документа остаётся текстом.
    """
    number = abs(int(value)) % 100
    if 11 <= number <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def phrase_slots(counts: dict[str, int]) -> dict[str, str]:
    """Числовые фразы для шаблонов, которым нужен язык, а не цифра."""
    ops = int(counts.get("ops") or 0)
    tools = int(counts.get("tools") or 0)
    return {
        "ops_phrase": f"{ops} {ru_plural(ops, 'операция', 'операции', 'операций')}",
        "tools_phrase": (f"{tools} "
                         f"{ru_plural(tools, 'инструмент', 'инструмента', 'инструментов')}"),
    }


def render(template: str, counts: dict[str, int]) -> str:
    text = template
    for key, value in counts.items():
        text = text.replace("{" + key + "}", str(value))
    for key, value in phrase_slots(counts).items():
        text = text.replace("{" + key + "}", value)
    return text


def expected_slots(templates: dict[str, dict[str, str]],
                   counts: dict[str, int]) -> dict[str, dict[str, str]]:
    return {doc: {slot: render(text, counts) for slot, text in slots.items()}
            for doc, slots in templates.items()}


def mark_document(path: Path, slots: dict[str, str], label: str = "") -> list[str]:
    """Обернуть найденный шаблонный текст маркерами. Возвращает находки."""
    problems: list[str] = []
    name = label or path.name
    text = path.read_text(encoding="utf-8")
    changed = False
    for slot, body in slots.items():
        if f"<!-- surface:{slot} -->" in text:
            continue
        if body not in text:
            problems.append(f"{name}:{slot}: не найден текст для разметки")
            continue
        if text.count(body) != 1:
            problems.append(
                f"{name}:{slot}: текст встречается {text.count(body)} раз(а)")
            continue
        text = text.replace(
            body, f"<!-- surface:{slot} -->{body}<!-- /surface:{slot} -->", 1)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")
    return problems


def check_document(path: Path, slots: dict[str, str], label: str = "") -> list[str]:
    """Сравнить содержимое маркеров с ожидаемым."""
    problems: list[str] = []
    name = label or path.name
    text = path.read_text(encoding="utf-8")
    found = {match.group(1): match.group(2) for match in MARKER.finditer(text)}
    for slot, expected in slots.items():
        if slot not in found:
            problems.append(f"{name}:{slot}: маркеров нет — число не проверяется")
            continue
        if found[slot] != expected:
            problems.append(
                f"{name}:{slot}: ожидалось {expected!r}, в документе {found[slot]!r}")
    for slot in sorted(set(found) - set(slots)):
        problems.append(f"{name}:{slot}: маркер без шаблона — такой слот не объявлен")
    return problems


def write_document(path: Path, slots: dict[str, str]) -> list[str]:
    """Заменить содержимое маркеров ожидаемым. Возвращает список изменённых слотов."""
    text = path.read_text(encoding="utf-8")
    changed: list[str] = []

    def replace(match: re.Match) -> str:
        slot = match.group(1)
        if slot not in slots:
            return match.group(0)
        body = f"<!-- surface:{slot} -->{slots[slot]}<!-- /surface:{slot} -->"
        if match.group(0) != body:
            changed.append(slot)
        return body

    updated = MARKER.sub(replace, text)
    if updated != text:
        path.write_text(updated, encoding="utf-8", newline="\n")
    return changed


def artifact_path(root: Path) -> Path:
    return root / ARTIFACT


def read_artifact(root: Path) -> dict:
    path = artifact_path(root)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def write_artifact(root: Path, payload: dict, surface_mod) -> str:
    path = artifact_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = surface_mod.artifact(payload)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return str(path.relative_to(root)).replace("\\", "/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="числа поверхности: генератор и гейт")
    parser.add_argument("--root", default=str(ROOT), help="корень дерева (для песочницы)")
    parser.add_argument("--check", action="store_true", help="только проверить")
    parser.add_argument("--write", action="store_true", help="записать числа и артефакт")
    parser.add_argument("--mark", action="store_true", help="однократно разметить документы")
    parser.add_argument("--json", action="store_true", help="машинный ответ")
    args = parser.parse_args(argv)
    if not (args.check or args.write or args.mark):
        args.check = True

    root = Path(args.root).resolve()
    templates = load_templates()
    surface_mod = load_surface(root)
    payload = surface_mod.collect(root)
    counts = payload["counts"]
    slots = expected_slots(templates, counts)
    findings: list[str] = list(payload.get("problems") or [])

    if args.mark:
        for doc, doc_slots in slots.items():
            path = root / doc
            if not path.is_file():
                findings.append(f"{doc}: файла нет — размечать нечего")
                continue
            findings.extend(mark_document(path, doc_slots, doc))
        if not args.json:
            print(f"разметка: документов {len(slots)}")
    if args.write or args.check:
        for doc, doc_slots in slots.items():
            path = root / doc
            if not path.is_file():
                findings.append(f"{doc}: файла нет в дереве")
                continue
            if args.write:
                changed = write_document(path, doc_slots)
                if changed and not args.json:
                    print(f"  {doc}: обновлено слотов {len(changed)}")
            else:
                findings.extend(check_document(path, doc_slots, doc))

    artifact = read_artifact(root)
    if args.write:
        written = write_artifact(root, payload, surface_mod)
        if not args.json:
            print(f"  артефакт: {written} (отпечаток {payload['digest'][:12]})")
    else:
        if not artifact:
            findings.append(f"{ARTIFACT.as_posix()}: артефакта нет — выполните --write")
        elif artifact.get("digest") != payload["digest"]:
            findings.append(
                f"{ARTIFACT.as_posix()}: отпечаток {artifact.get('digest', '')[:12]}"
                f" не совпадает с текущим {payload['digest'][:12]} — выполните --write")
        old_rev = str((artifact.get("revision") or {}).get("rev_id") or "")
        new_rev = str((payload.get("revision") or {}).get("rev_id") or "")
        if old_rev and new_rev and old_rev != new_rev and not args.json:
            print(f"  предупреждение: артефакт снят на {old_rev}, активная {new_rev};"
                  " это не ошибка чисел (ревизия вне отпечатка)")

    if args.json:
        print(json.dumps({
            "ok": not findings,
            "root": str(root),
            "counts": counts,
            "digest": payload["digest"],
            "revision": payload.get("revision"),
            "notes": payload.get("notes") or [],
            "findings": findings,
        }, ensure_ascii=False))
    else:
        mode = "запись" if args.write else "проверка"
        print(f"поверхность ({mode}): операций {counts['ops']}, инструментов {counts['tools']},"
              f" подкоманд {counts['cli']}, возможностей {counts['caps']},"
              f" отпечаток {payload['digest'][:12]}")
        for note in payload.get("notes") or []:
            print(f"  примечание: {note}")
        if findings:
            print(f"НАХОДОК: {len(findings)}")
            for item in findings:
                print(f"  - {item}")
        else:
            print("чисто: числа в разметке совпадают с кодом")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
