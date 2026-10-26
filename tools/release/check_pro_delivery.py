#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка поставки закрытого издания: воспроизводимость, состав, привязка лицензии.

Издание определяется ОБЪЯВЛЕНИЕМ (`editions/editions.toml`), а не строкой в этом
файле: инструмент один, манифестов три. Промт-И16 (25.10.2026) потребовал ровно
этого — свойство «манифест издания N+1 = манифест издания N + объявленная папка»
проверяется одной функцией, а не копией проверки на каждое издание. Имя файла
оставлено прежним: его знает 25-я проверка чекера, и переименование сломало бы
её молча.

Зачем отдельный инструмент. У Community-поставки проверяльщик отвечает на вопрос
«можно ли отдавать эти файлы людям». У Pro вопрос другой, и он не сводится к
первому: **что именно получает покупатель и что из этого работает**. Три вещи
здесь не проверяются ничем другим:

1. **Воспроизводимость.** Два прогона сборщика на неизменном дереве обязаны дать
   один и тот же набор файлов и побайтово одинаковый `checksums.sha256`. Если
   состав «дрожит», сравнивать поставки между клиентами нечем, а подписанный
   набор доказательств теряет смысл.
2. **Состав.** Каждый файл сборки обязан быть либо из набора Community, либо
   лежать в объявленной закрытой папке издания. Третьего класса нет: файл в
   поставке, о котором нельзя сказать, откуда он, — это либо утечка, либо
   удержание, о котором забыли сказать. Здесь же проверяется, что поставка Pro
   вообще ОТЛИЧАЕТСЯ от Community: копия, названная изданием, — не издание.
3. **Привязка лицензии.** Сборка без файла лицензии обязана считать себя
   Community и отказывать платным операциям С ПРИЧИНОЙ; подписанная лицензия
   обязана их открывать; подделанная — снова закрывать, называя подпись. Это
   проверяется в СОБРАННОМ дереве, а не в живом: покупатель получает именно его.

Плюс секретоносные пути: ни один файл закрытого каталога состояния (`catalog/`),
ключа, лицензии или локальных настроек в поставке быть не может. Правило
`paths` проверяльщика ловит пути машины автора, правило `secrets` — значения в
тексте; имена файлов не ловит никто, поэтому имя проверяется здесь, по свойству
пути, а не по списку известных случаев.

Границы, названные честно:

* инструмент не подписывает поставку и не выпускает лицензию покупателю: он
  проверяет, что механизм лицензии работает в собранном дереве;
* проверяется один прогон: одновременную работу двух сборщиков в один приёмник
  инструмент не ловит (сборщик отказывается писать в непустой приёмник, и это
  его свойство, а не это);
* «работает под лицензией» подтверждается РАЗРЕШЕНИЕМ операций, а не их
  выполнением: для `sso` нужен провайдер, для `siem` — приёмник. Выполнение
  проверяется в тестах соответствующих возможностей.

Запуск:
    python tools/release/check_pro_delivery.py
    python tools/release/check_pro_delivery.py --json
    python tools/release/check_pro_delivery.py --keep

Коды возврата: 0 — поставка годна, 1 — нарушения, 2 — аргументы, 3 — окружение.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import relcommon as rel  # noqa: E402 - путь к общему модулю добавлен выше

REPO = rel.default_source()
#: Умолчание — поставка Pro: инструмент вызывается без параметров и в чекере, и
#: руками. Enterprise проверяется тем же инструментом с явным --manifest.
MANIFEST = REPO / "tools/release/manifest-enterprise.toml"
MANIFEST_BASE = REPO / rel.MANIFEST_DEFAULT
DECLARATION = REPO / "editions/editions.toml"
BASELINE = REPO / "src/ragraf/edition_baseline.json"

#: Признаки секретоносного ПУТИ. Проверяется свойство имени, а не список
#: известных файлов: новый ключ с незнакомым именем обязан попасть под правило.
SECRET_SUFFIXES = (".key", ".pem", ".license", ".sig", ".pfx", ".p12", ".kdbx", ".ppk")
SECRET_NAMES = {
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    ".env", ".netrc", ".pgpass", "credentials", "secrets.key", "db_access.json",
}
#: Каталоги состояния: реестр корпусов, настройки, ключи, лицензии. Это один
#: класс — «локальное состояние машины», и в поставке ему места нет целиком:
#: перечислять файлы поимённо значит пропустить следующий.
SECRET_DIRS = ("catalog",)

FEATURES_OF_PAID_EDITIONS = ("quality-loop", "shared-index", "sso", "siem-export",
                             "offline-license", "attestation")

FAILURES: list[str] = []
EXAMINED = 0


def expect(condition: bool, message: str) -> None:
    global EXAMINED
    EXAMINED += 1
    if not condition:
        FAILURES.append(message)


def run(script: pathlib.Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, check=False
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def payload_number(output: str, label: str) -> int | None:
    match = re.search(rf"^{label}:\s*(\d+)", output, re.M)
    return int(match.group(1)) if match else None


def tree_files(root: pathlib.Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def build(manifest: pathlib.Path, out: pathlib.Path) -> tuple[int, str]:
    return run(
        REPO / "tools/release/build_release.py",
        "--out", str(out), "--manifest", str(manifest), "--source", str(REPO),
    )


# ---------------------------------------------------------------------------
# 1. Воспроизводимость
# ---------------------------------------------------------------------------


def check_reproducible(manifest: pathlib.Path, sandbox: pathlib.Path) -> tuple[int, pathlib.Path | None]:
    """Два прогона: одинаковый набор файлов и одинаковые контрольные суммы."""
    first, second = sandbox / "run1", sandbox / "run2"
    code_a, out_a = build(manifest, first)
    expect(code_a == 0, f"первая сборка Pro завершилась кодом {code_a}: {out_a.strip()[-300:]}")
    code_b, out_b = build(manifest, second)
    expect(code_b == 0, f"вторая сборка Pro завершилась кодом {code_b}: {out_b.strip()[-300:]}")
    if code_a != 0 or code_b != 0:
        return 0, None

    # Артефакты сборщика в набор файлов поставки не входят: иначе отчёт
    # называл бы на один файл больше, чем сборщик, и расхождение читалось бы
    # как разница состава (измерено на себе).
    artifacts = set(rel.load_manifest(manifest, REPO).artifacts)
    files_a, files_b = tree_files(first) - artifacts, tree_files(second) - artifacts
    expect(files_a == files_b,
           f"два прогона дали разный набор файлов: только в первом {sorted(files_a - files_b)[:3]}, "
           f"только во втором {sorted(files_b - files_a)[:3]}")
    sums_a = (first / "checksums.sha256").read_bytes()
    sums_b = (second / "checksums.sha256").read_bytes()
    expect(sums_a == sums_b, "checksums.sha256 двух прогонов различаются — сборка не воспроизводима")
    expect(len(sums_a.splitlines()) > 0, "checksums.sha256 пуст — перечень файлов не записан")
    for line in sums_a.decode("utf-8").splitlines():
        digest, _, name = line.partition("  ")
        expect(bool(re.fullmatch(r"[0-9a-f]{64}", digest)),
               f"строка контрольных сумм без sha256: {line[:80]!r}")
        expect((first / name).is_file(), f"контрольная сумма записана для отсутствующего файла: {name}")
    return len(files_a), first


# ---------------------------------------------------------------------------
# 2. Состав
# ---------------------------------------------------------------------------


def editions_chain(manifest_path: pathlib.Path) -> list[dict]:
    """Цепочка изданий, к которой относится манифест: от Community до своего.

    Издание определяется ОБЪЯВЛЕНИЕМ, а не именем файла: имя манифеста можно
    переименовать, объявление — нет.

    Про две разные вещи. В `editions.toml` объявлены ПОСТАВКИ, в базовой линии —
    ЛЕСТНИЦА ПРАВ (community → pro → enterprise). С 26.10.2026 поставка одна
    (Enterprise), а уровни лицензии остались: лицензия Pro открывает подмножество.
    Поэтому проверяется не равенство списков, а то, что цепочка поставок —
    подпоследовательность лестницы, начиная с community: опечатка в имени,
    перепутанный порядок и лишнее звено ловятся, а требование «по поставке на
    каждый уровень» снято намеренно.
    """
    declaration = tomllib.loads(DECLARATION.read_text(encoding="utf-8"))
    rows = declaration.get("edition") or {}
    labels = list(rows.keys())
    try:
        relative = manifest_path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        relative = ""
    mine = None
    for name in labels:
        declared = rel.normalise(str((rows.get(name) or {}).get("manifest") or ""))
        if declared and declared == relative:
            mine = name
            break
    expect(mine is not None,
           f"манифест {relative or manifest_path.name} не объявлен ни одним изданием "
           f"в editions/editions.toml — состав нельзя отнести к изданию")
    if mine is None:
        return []

    chain: list[dict] = []
    for name in labels[: labels.index(mine) + 1]:
        item = rows.get(name) or {}
        chain.append({
            "name": name,
            "title": str(item.get("title") or name),
            "folder": str(item.get("folder") or "").rstrip("/"),
            "manifest": rel.normalise(str(item.get("manifest") or "")),
        })

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    order = [str(item) for item in baseline.get("order") or []]
    chain_names = [item["name"] for item in chain]
    expected = [name for name in order if name in chain_names]
    expect(chain_names == expected,
           f"цепочка поставок в editions/editions.toml не следует лестнице прав "
           f"из базовой линии: {chain_names} против {expected}")

    # Звено N+1 наследует звено N — это и есть проверяемое свойство цепочки.
    for previous, current in zip(chain, chain[1:]):
        data = tomllib.loads((REPO / current["manifest"]).read_text(encoding="utf-8"))
        inherited = rel.normalise(str((data.get("release") or {}).get("inherits") or ""))
        expect(inherited == previous["manifest"],
               f"{current['name']}: манифест наследует {inherited or '—'}, а основанием "
               f"издания {current['name']} объявлен {previous['manifest']}")
    expect(bool(chain[-1]["folder"]) if chain[-1]["name"] != "community" else True,
           f"{chain[-1]['name']}: в объявлении нет папки издания — добавлять нечего")
    return chain


def check_composition(tree: pathlib.Path, chain: list[dict],
                      manifest: pathlib.Path | None = None) -> int:
    """Каждый файл поставки — Community, папка издания или объявленный модуль.

    Папки предшествующих изданий законны: Enterprise = Pro + своя папка, и файл
    из `pro/` приходит по наследованию, а не утечкой.

    Третий класс — ЗАКРЫТЫЙ МОДУЛЬ: файл, который лежит в общем дереве (`src/`,
    `tests/`), но публикуется только платным изданием. Объявляется он полем
    `pro_only` в манифесте СВОЕГО издания, и объявление проверяется в обе
    стороны: файл обязан быть в этой поставке и обязан отсутствовать в
    Community-сборке. Иначе «закрытый модуль» — слово, а не граница.
    """
    community = rel.collect(REPO, rel.load_manifest(MANIFEST_BASE, REPO))
    community_files = set(community.files)
    own_manifest = rel.load_manifest(manifest or MANIFEST, REPO)
    declared_modules = set(own_manifest.pro_only)
    folders = [step["folder"] for step in chain if step["folder"]]
    mine = chain[-1] if chain else {"name": "", "title": "", "folder": ""}
    label = mine["title"]

    def belongs(name: str) -> bool:
        if name in declared_modules:
            return True
        return any(name == folder or name.startswith(folder + "/") for folder in folders)

    built = tree_files(tree) - {"checksums.sha256"}
    outside = sorted(name for name in built if name not in community_files and not belongs(name))
    expect(not outside,
           f"файлов вне набора Community и вне папок изданий {folders or '—'}: "
           f"{len(outside)}; первый: {outside[0] if outside else ''}")
    # Объявление закрытого модуля проверяется в обе стороны.
    for module in sorted(declared_modules):
        expect(module in built,
               f"объявленный закрытый модуль {module} не попал в поставку {label}: "
               f"объявление расходится со сборкой")
        expect(module not in community_files,
               f"объявленный закрытый модуль {module} входит в Community-сборку: "
               f"это не закрытая часть, а забытое исключение")

    added = sorted(built - community_files)
    expect(bool(added),
           f"поставка {label} не содержит ни одного файла сверх Community — это копия, а не издание")
    # Своя папка обязана что-то добавить: пустой каталог издания означает, что
    # «отличие от предыдущего издания» существует только в объявлении.
    own = [name for name in built if mine["folder"] and
           (name == mine["folder"] or name.startswith(mine["folder"] + "/"))]
    expect(bool(own),
           f"в поставке {label} нет ни одного файла внутри объявленной папки "
           f"{mine['folder'] or '—'}/")

    declaration = tomllib.loads(DECLARATION.read_text(encoding="utf-8"))
    retained = [str(item.get("path") or "").rstrip("/")
                for item in declaration.get("retained", {}).get("folders", [])]
    retained = [item for item in retained if item]
    for name in retained:
        leaked = sorted(item for item in built if item == name or item.startswith(name + "/"))
        if name in folders:
            continue
        expect(not leaked,
               f"удержанная папка {name}/ попала в поставку {label}: {leaked[:3]}")

    for name in built:
        segments = name.split("/")
        expect(segments[0] not in SECRET_DIRS,
               f"{name}: каталог состояния машины попал в поставку")
        expect(name.lower().endswith(SECRET_SUFFIXES) is False,
               f"{name}: ключ, лицензия или подпись в поставке")
        expect(segments[-1].lower() not in SECRET_NAMES,
               f"{name}: файл с секретоносным именем в поставке")
    return len(added)


# ---------------------------------------------------------------------------
# 3. Инвентарь прав против базовой линии
# ---------------------------------------------------------------------------


def check_inventory(chain: list[dict]) -> int:
    """Инвентари прав по цепочке изданий против базовой линии.

    Инвентарь издания описывает ДОБАВКУ своего издания. Проверка собирает их по
    цепочке (Pro: только pro/, Enterprise: pro/ + enterprise/) и требует:
    возможность своего уровня названа РОВНО в одном инвентаре, а возможность
    более высокого издания в инвентаре не обещана. Дубль прячет актуальную строку
    за устаревшей, обещание чужого — выдаёт несуществующее право.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    features = {str(item.get("name")): item for item in baseline.get("features", [])}
    order = [str(item) for item in baseline.get("order") or []]
    mine = chain[-1] if chain else {"name": "", "title": ""}
    limit = order.index(mine["name"]) if mine["name"] in order else len(order) - 1

    shipped: dict[str, dict] = {}
    declared: dict[str, dict] = {}
    described_by: dict[str, str] = {}
    for step in chain:
        if not step["folder"]:
            continue
        path = REPO / step["folder"] / "inventory.toml"
        relative = path.relative_to(REPO).as_posix()
        expect(path.is_file(),
               f"нет инвентаря прав издания {step['title']}: {relative}")
        if not path.is_file():
            continue
        inventory = tomllib.loads(path.read_text(encoding="utf-8"))
        expect(inventory.get("schema") == 1,
               f"{relative}: схема {inventory.get('schema')!r} не та, которую читает проверка")
        for section, target in (("shipped", shipped), ("declared", declared)):
            for row in inventory.get(section, []):
                name = str(row.get("feature"))
                expect(name not in described_by,
                       f"возможность {name!r} описана дважды: {described_by.get(name)} и "
                       f"{relative} — дубль прячет актуальную строку за устаревшей")
                described_by[name] = relative
                target[name] = row

    expect(not (set(shipped) & set(declared)),
           f"возможность объявлена сразу написанной и не написанной: "
           f"{sorted(set(shipped) & set(declared))}")

    # Полнота в обе стороны на СВОЁМ уровне: возможность платного издания не выше
    # действующего либо описана здесь, либо её отсутствие — находка.
    paid = {name: item for name, item in features.items()
            if str(item.get("edition")) not in {"", "community"}}
    expect(bool(paid), "в базовой линии нет ни одной возможности платных изданий — проверять нечего")
    for name in sorted(paid):
        rank = order.index(str(paid[name].get("edition"))) if str(paid[name].get("edition")) in order else -1
        if rank <= limit:
            expect(name in shipped or name in declared,
                   f"возможность {name!r} ({paid[name].get('edition')}) не названа ни в "
                   f"одном инвентаре цепочки изданий до {mine['name']}")
        else:
            expect(name not in described_by,
                   f"инвентарь издания {mine['name']} обещает возможность {name!r} "
                   f"издания {paid[name].get('edition')} — это право другого издания")

    for name, row in {**shipped, **declared}.items():
        item = features.get(name)
        expect(item is not None, f"инвентарь называет возможность {name!r}, которой нет в базовой линии")
        if item is None:
            continue
        expect(str(item.get("edition")) == str(row.get("edition")),
               f"{name}: издание в инвентаре {row.get('edition')!r}, в базовой линии {item.get('edition')!r}")
        status = str(item.get("status") or "")
        expect(status == ("shipped" if name in shipped else "declared"),
               f"{name}: статус в базовой линии {status!r} не совпадает с разделом инвентаря")

    for name, row in shipped.items():
        files = [str(item) for item in row.get("files") or []]
        expect(bool(files), f"{name}: написанная возможность не называет ни одного файла")
        for relative in files:
            expect((REPO / relative).is_file(), f"{name}: названный файл не существует: {relative}")
        tests = [str(item) for item in row.get("tests") or []]
        expect(bool(tests), f"{name}: написанная возможность не называет ни одной проверки")
        for relative in tests:
            expect((REPO / relative).is_file(), f"{name}: названная проверка не существует: {relative}")
        expect(len(str(row.get("why") or "").strip()) >= 40,
               f"{name}: причина не названа — по ней нельзя понять, за что платят")

    for name, row in declared.items():
        expect(len(str(row.get("why") or "").strip()) >= 40,
               f"{name}: не сказано, почему возможности нет")
        expect(not row.get("files"),
               f"{name}: раздел «не написано» называет файлы — тогда это не объявленная возможность")
    return len(shipped) + len(declared)


# ---------------------------------------------------------------------------
# 4. Привязка лицензии в собранном дереве
# ---------------------------------------------------------------------------

PROBE = '''import json, os, sys
sys.path.insert(0, "__SRC__")
from ragraf import edition
state = edition.resolve()
# Решение по КАЖДОЙ операции платных возможностей берётся из той же функции,
# которую вызывает гейт фасада (`EditionState.decide`): проверка поставки не
# повторяет правило, а спрашивает его.
baseline_path = os.path.join(os.path.dirname(edition.__file__), "edition_baseline.json")
baseline = json.load(open(baseline_path, encoding="utf-8"))
paid = []
for item in baseline.get("features", []):
    if str(item.get("edition") or "community") == "community":
        continue
    for operation in item.get("operations") or []:
        paid.append({
            "feature": str(item.get("name")),
            "edition": str(item.get("edition")),
            "status": str(item.get("status") or ""),
            "operation": str(operation),
            "decision": state.decide(str(operation)),
        })
print(json.dumps({
    "effective": state.effective,
    "declared": state.declared,
    "features": list(state.features),
    "license": dict(state.license),
    "notes": list(state.notes),
    "missing": [dict(item) for item in state.missing],
    "operations": sorted(edition.build_operations()),
    "paid": paid,
}, ensure_ascii=False))
'''


def probe(tree: pathlib.Path, env_extra: dict[str, str]) -> tuple[int, dict | None, str]:
    script = tree.parent / "probe-edition.py"
    # Путь поставки подставляется в текст пробы, а не передаётся окружением:
    # PYTHONPATH фильтруется, и проба падала бы на «нет модуля ragraf», то есть
    # выдавала бы дефект окружения за дефект поставки.
    script.write_bytes(PROBE.replace("__SRC__", (tree / "src").as_posix()).encode("utf-8"))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("RAGRAF_EDITION")}
    env.update(env_extra)
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=False,
        cwd=str(tree), env=env,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return completed.returncode, None, output
    try:
        return 0, json.loads(completed.stdout.strip().splitlines()[-1]), output
    except Exception as error:  # noqa: BLE001 - ответ пробы может быть негодным
        return 1, None, f"{output}\n{type(error).__name__}: {error}"


def make_licenses(sandbox: pathlib.Path) -> tuple[pathlib.Path, str, pathlib.Path]:
    """Действующая и подделанная лицензии с ключом ИЗ ПЕСОЧНИЦЫ.

    Ключ создаётся в песочнице и живёт до конца прогона: доверенный ключ
    передаётся пробе переменной окружения, глобальное состояние машины не
    трогается. Иначе проверка зависела бы от того, есть ли у автора настоящий
    ключ подписи, — то есть от секрета, которого у проверки быть не должно.
    """
    sys.path.insert(0, str(REPO / "src"))
    from ragraf import signing  # noqa: PLC0415 - нужен только этой проверке

    key = sandbox / "probe-signing.key"
    signing.ensure_key(key)
    public = signing.public_key_b64(key)

    valid = sandbox / "valid.license"
    valid.write_bytes(json.dumps({
        "edition": "enterprise",
        "licensee": "probe-installation",
        # Формат срока — ГГГГ-ММ-ДД, и это измерено, а не угадано:
        # `edition.read_license` разбирает поле через strptime("%Y-%m-%d"), а
        # полная отметка времени даёт состояние `broken` с причиной «не
        # разбирается как ГГГГ-ММ-ДД». Лицензия с такой датой не действует, и
        # проба объявила бы дефект собственной фикстуры дефектом поставки.
        "issued_at": "2026-01-01",
        "expires_at": "2099-12-31",
    }, ensure_ascii=False, indent=2).encode("utf-8"))
    signing.sign_file(valid, key)

    forged = sandbox / "forged.license"
    forged.write_bytes(valid.read_bytes().replace(b"probe-installation", b"another-installation"))
    forged.with_name(forged.name + ".sig").write_bytes(valid.with_name(valid.name + ".sig").read_bytes())
    return valid, public, forged


def check_licence_binding(tree: pathlib.Path, sandbox: pathlib.Path) -> int:
    valid, public, forged = make_licenses(sandbox)
    licence_env = "RAGRAF_EDITION_LICENSE"
    pubkey_env = "RAGRAF_EDITION_PUBKEY"

    code, state, output = probe(tree, {})
    expect(code == 0 and state is not None,
           f"проба издания в собранном дереве не выполнена: {output.strip()[-300:]}")
    if state is None:
        return 0
    expect(state["effective"] == "community",
           f"сборка без лицензии считает себя {state['effective']!r}, а не community")
    expect("sso" not in state["features"],
           "сборка без лицензии объявляет enterprise-возможность sso доступной")
    expect(bool(state["license"]), "состояние лицензии не названо вовсе")
    expect(bool(state.get("paid")), "в базовой линии поставки нет платных операций — проверять нечего")
    for row in state.get("paid") or []:
        decision = row.get("decision") or {}
        expect(decision.get("allowed") is False,
               f"без лицензии операция {row['operation']} ({row['feature']}, "
               f"{row['edition']}) разрешена — гейт не работает")
        reason = str(decision.get("reason") or "")
        expect(bool(reason), f"отказ операции {row['operation']} не назван причиной")
        expect("community" in reason or "запланированная" in reason,
               f"отказ операции {row['operation']} не называет ни издание, ни статус: {reason!r}")

    code, unlocked, output = probe(tree, {licence_env: str(valid), pubkey_env: public})
    expect(code == 0 and unlocked is not None,
           f"проба с подписанной лицензией не выполнена: {output.strip()[-300:]}")
    if unlocked is not None:
        expect(unlocked["effective"] == "enterprise",
               f"подписанная лицензия enterprise даёт издание {unlocked['effective']!r}")
        absent = [name for name in FEATURES_OF_PAID_EDITIONS if name not in unlocked["features"]]
        expect(not absent,
               f"под лицензией не открылись возможности: {absent}")
        missing_names = {str(item.get("name")) for item in unlocked["missing"]}
        shipped_missing = sorted(missing_names & set(FEATURES_OF_PAID_EDITIONS))
        expect(not shipped_missing,
               f"под лицензией НАПИСАННЫЕ возможности названы отсутствующими: {shipped_missing}")
        # Свойство переформулировано этапом И18. Прежняя редакция требовала
        # НЕПУСТОГО `missing` — она опиралась на то, что объявленные и не
        # написанные возможности есть всегда. После решения 26.10.2026 их нет
        # (secret-broker написан, connectors и fleet сняты в roadmap), и под
        # полной лицензией `missing` обязан быть пуст. Проверяется смысл:
        # объявленное, если оно есть, названо; написанное не выдано за
        # отсутствующее (строкой выше).
        declared_paid = {
            str(item.get("name"))
            for item in json.loads(BASELINE.read_text(encoding="utf-8")).get("features", [])
            if str(item.get("edition")) not in {"", "community"}
            and str(item.get("status")) != "shipped"
        }
        hidden = sorted(declared_paid - missing_names)
        expect(not hidden,
               "объявленные-но-не-написанные возможности в missing не названы — "
               f"неразрешённое выдано за доступное: {hidden}")
        unlocked_shipped = 0
        for row in unlocked.get("paid") or []:
            decision = row.get("decision") or {}
            if row.get("status") == "shipped":
                unlocked_shipped += 1
                expect(decision.get("allowed") is True,
                       f"под лицензией enterprise операция {row['operation']} "
                       f"({row['feature']}) всё равно запрещена: "
                       f"{decision.get('reason')!r}")
            else:
                expect(decision.get("allowed") is False,
                       f"объявленная-но-не-написанная возможность {row['feature']} "
                       f"разрешила операцию {row['operation']} — ложное разрешение")
        expect(unlocked_shipped >= 8,
               f"под лицензией открылось только {unlocked_shipped} написанных операций — их должно быть не меньше восьми")
        operations = set(unlocked["operations"])
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for item in baseline.get("features", []):
            if str(item.get("name")) not in unlocked["features"]:
                continue
            for operation in item.get("operations") or []:
                expect(str(operation) in operations,
                       f"возможность {item.get('name')} объявлена, а операции {operation} в сборке нет")

    code, rejected, output = probe(tree, {licence_env: str(forged), pubkey_env: public})
    expect(code == 0 and rejected is not None,
           f"проба с подделанной лицензией не выполнена: {output.strip()[-300:]}")
    if rejected is not None:
        expect(rejected["effective"] == "community",
               f"подделанная лицензия даёт издание {rejected['effective']!r}")
        expect(str(rejected["license"].get("state")) == "forged",
               f"подделанная лицензия названа состоянием {rejected['license'].get('state')!r}")
        expect(bool(rejected["notes"]), "подделанная лицензия не названа причиной в edition_notes")
    return 9


# ---------------------------------------------------------------------------
# 5. Поставка проходит собственные проверки
# ---------------------------------------------------------------------------


def check_shipped_tests(tree: pathlib.Path) -> int:
    """Проверки слоя изданий едут вместе с поставкой — но не запускаются здесь.

    Замер 25.10.2026: `tests/test_editions.py`, запущенный в собранном дереве,
    падает на `config/ragraf.yaml` — конфигурации этой машины, которой в
    поставке нет и быть не должно (публикуется только
    `examples/ragraf.example.yaml`). Гонять в проверке доставки индексатор в
    песочнице значило бы проверять индексатор.

    Поэтому здесь проверяется СОСТАВ (файлы проверок на месте и разбираются —
    это делает правило `syntax` проверяльщика), а поведение гейта проверяется
    выше, в пробе лицензии: она спрашивает решение у той же функции, которую
    вызывает гейт фасада, и не требует корпуса.
    """
    shipped_tests = [tree / "tests/test_editions.py", tree / "tests/test_external_access.py",
                     tree / "tests/test_offline_attest.py", tree / "tests/test_quality_loop.py"]
    present = 0
    for path in shipped_tests:
        expect(path.is_file(), f"в поставке нет {path.relative_to(tree).as_posix()} — "
                               f"возможность едет без своей проверки")
        present += 1
    return present


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_pro_delivery.py",
        description="Проверить поставку закрытого издания: состав, воспроизводимость, лицензию.")
    parser.add_argument("--manifest", default=str(MANIFEST),
                        help="манифест издания (по умолчанию Pro; для Enterprise — "
                             "tools/release/manifest-enterprise.toml)")
    parser.add_argument("--json", action="store_true", help="машинный отчёт")
    parser.add_argument("--keep", action="store_true", help="оставить песочницу и напечатать путь")
    # Параметра --source нет намеренно: проверка поставки читает ЖИВОЕ дерево, в
    # котором лежит, и половина путей здесь вычисляется от него при загрузке
    # модуля. Ключ, менявший корень только для части проверок, давал бы
    # сравнение поставки одного дерева с правилами другого.
    args = parser.parse_args(argv)
    manifest = pathlib.Path(args.manifest).resolve()

    for path, label in ((manifest, "манифест издания"),
                        (MANIFEST_BASE, "манифест Community"),
                        (DECLARATION, "декларация изданий"),
                        (BASELINE, "базовая линия изданий")):
        expect(path.is_file(), f"нет файла: {label} ({path})")
    if FAILURES:
        return report(args, None)

    chain = editions_chain(manifest)
    if not chain:
        return report(args, {"manifest": str(manifest)})
    label = chain[-1]["title"]

    # Инвентарь прав сверяется ДО сборки: он описывает права, а не файлы, и
    # расхождение с базовой линией делает бессмысленной остальную проверку.
    inventory_features = check_inventory(chain)

    sandbox = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-edition-delivery-"))
    try:
        files, tree = check_reproducible(manifest, sandbox)
        if tree is not None:
            code, output = run(REPO / "tools/release/verify_release.py",
                               "--tree", str(tree), "--manifest", str(manifest),
                               "--source", str(REPO))
            expect(code == 0,
                   f"проверяльщик забраковал поставку {label}: {output.strip()[-400:]}")
            expect("правил применено: 9" in output,
                   f"на поставке {label} применены не все девять правил проверяльщика")
            expect((payload_number(output, "осмотрено") or 0) > 0,
                   f"проверяльщик осмотрел 0 объектов на поставке {label} — это ложный PASS")
            added = check_composition(tree, chain, manifest)
            check_licence_binding(tree, sandbox)
            check_shipped_tests(tree)
            return report(args, {
                "manifest": str(manifest),
                "edition": chain[-1]["name"],
                "chain": [step["name"] for step in chain],
                "files": files,
                "added_over_community": added,
                "inventory_features": inventory_features,
                "sandbox": str(sandbox) if args.keep else "",
            })
        # Сборка не удалась: отчёт всё равно печатается — иначе провал выглядел бы
        # как молчание (main вернул None, то есть код 0).
        return report(args, {"manifest": str(manifest), "edition": chain[-1]["name"]})
    finally:
        if args.keep:
            print(f"песочница оставлена: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


def report(args: argparse.Namespace, extra: dict | None) -> int:
    if args.json:
        payload = {
            "tool": "check_pro_delivery",
            "format": "ragraf-pro-delivery/1",
            "examined": EXAMINED,
            "failures": len(FAILURES),
            "failure_examples": FAILURES[:10],
            "ok": not FAILURES,
            **(extra or {}),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not FAILURES else 1
    for problem in FAILURES:
        print(f"FAIL: {problem}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
