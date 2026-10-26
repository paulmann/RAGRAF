"""Слой изданий: закрытый список, возможности из данных, отказ вне издания, подпись.

Зачем отдельный файл. ПРОМТ-И6 добавляет механизм, который легко превратить в
проверку написания: достаточно убедиться, что в конверте есть поле `edition`.
Такой прогон зеленел бы и на «издании enterprise без лицензии», и на подделанном
файле лицензии. Поэтому здесь проверяется ПОВЕДЕНИЕ:

  1. данные базовой линии: закрытый порядок изданий, единственность объявления
     операции, покрытие всех операций сборки;
  2. разрешение: заявление только понижает, платное издание без лицензии не
     действует, неизвестное имя получает отказ с названным списком;
  3. подпись лицензии: правка содержимого после подписи, подпись чужим ключом,
     истёкший срок, неизвестное издание внутри файла — каждое состояние названо
     причиной, ни одно не выдаётся за «неизвестное издание»;
  4. гейт на живом фасаде: операция вне издания получает конверт с `error` и
     разделом `refused`, запись в журнале доступа появляется, а сама операция
     не выполняется; битая базовая линия закрывает всё (fail-closed), а не
     разрешает молча;
  5. конверт ответа не завышает `features`: возможность без кода в сборке в
     список не попадает;
  6. командная строка отвечает тем же состоянием, что и конверт.

Приманки, которые обязаны быть отвергнуты: лицензия с подписью от другого ключа,
лицензия без подписи вовсе, издание «pro» одной записью в настройке, операция,
которой нет в базовой линии.
"""

from __future__ import annotations

import json
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import edition, signing  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

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


TMP = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-editions-"))
CLEAN_ENV = (edition.BASELINE_ENV, edition.LICENSE_ENV, edition.DECLARED_ENV)


def clear_env() -> None:
    for name in CLEAN_ENV:
        os.environ.pop(name, None)
    # Изоляция от лицензии машины (25.10.2026): без этого тест наследует
    # действующую Enterprise-лицензию и проверки отказа не выполняются.
    os.environ[edition.LICENSE_ENV] = str(TMP / "absent-machine.license")


def set_env(**values: str) -> None:
    clear_env()
    for name, value in values.items():
        os.environ[{"baseline": edition.BASELINE_ENV,
                    "license": edition.LICENSE_ENV,
                    "declared": edition.DECLARED_ENV}[name]] = str(value)


clear_env()
BASE = edition.load_baseline()
BUILD_OPS = edition.build_operations()


def variant(name: str, mutate) -> pathlib.Path:
    """Копия базовой линии с правкой. Правка — в песочнице, не в пакете."""
    data = json.loads(pathlib.Path(edition.baseline_path()).read_text(encoding="utf-8"))
    mutate(data)
    target = TMP / f"{name}.json"
    target.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    return target


def sign_license(name: str, payload: dict, key_file: pathlib.Path) -> pathlib.Path:
    """Подписать файл лицензии ключом из песочницы."""
    target = TMP / name
    target.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    signing.sign_file(target, key_file)
    return target


try:
    key_a = TMP / "key-a.txt"
    key_b = TMP / "key-b.txt"
    signing.ensure_key(key_a)
    signing.ensure_key(key_b)
    PUB_A = signing.public_key_b64(key_a)
    PUB_B = signing.public_key_b64(key_b)

    print("1. Данные базовой линии изданий")
    check(edition.baseline_problems(json.loads(
        pathlib.Path(edition.baseline_path()).read_text(encoding="utf-8"))) == [],
        "базовая линия проходит собственную проверку")
    check(BASE.get("schema") == edition.SUPPORTED_SCHEMA,
          "схема базовой линии поддерживается", str(BASE.get("schema")))
    check(edition.editions(BASE) == ["community", "pro", "enterprise"],
          "порядок изданий — закрытый список из трёх имён",
          str(edition.editions(BASE)))
    try:
        edition.rank(BASE, "gold")
        check(False, "неизвестное издание отвергается проверкой списка")
    except edition.EditionRefusal as exc:
        check("закрытый список" in str(exc) and "community" in str(exc),
              "неизвестное издание отвергается с названным списком", str(exc))

    declared_ops = set(edition.operations_index(BASE))
    check(BUILD_OPS <= declared_ops,
          "каждая операция сборки объявлена в базовой линии",
          f"не объявлены: {sorted(BUILD_OPS - declared_ops)[:5]}")
    # Число сверяется с документами текущего состояния. ПРОМТ-И9 добавил две
    # операции контура качества (`quality_suite`, `quality_diff`), ПРОМТ-И15 —
    # три операции общего индекса (`shared_index_status`, `shared_index_publish`,
    # `shared_index_query`), этап И18 — две операции брокера секретов
    # (`broker_issue`, `broker_rotate`), поэтому здесь 54: проверка обязана
    # называть действующее число, иначе она охраняет прошлое.
    check(len(BUILD_OPS) == 54, "операций в сборке — 54 (как в документах текущего состояния)",
          str(len(BUILD_OPS)))
    ops_in_two = [op for op, names in edition.operations_index(BASE).items() if len(names) > 1]
    check(not ops_in_two, "ни одна операция не объявлена в двух возможностях", str(ops_in_two[:3]))
    dup = variant("dup-ops", lambda d: d["features"].append(
        {"name": "trap", "edition": "community", "operations": ["search"]}))
    problems = edition.baseline_problems(json.loads(dup.read_text(encoding="utf-8")))
    check(any("объявлена в двух возможностях" in p for p in problems),
          "данные с операцией в двух возможностях отвергаются", str(problems[:2]))
    bad_schema = variant("schema99", lambda d: d.update({"schema": 99}))
    problems = edition.baseline_problems(json.loads(bad_schema.read_text(encoding="utf-8")))
    check(any("schema" in p for p in problems),
          "неизвестная схема базовой линии отвергается", str(problems[:2]))

    # Статус возможности: закрытый список. Проверяется ОТКАЗ на опечатку, а не
    # наличие поля: поле есть и у бессмысленного значения, и именно поэтому
    # «shippd» прошло бы в ответ как обычная возможность.
    statuses = {str(item["name"]): str(item.get("status") or "") for item in BASE["features"]}
    check(all(value in edition.FEATURE_STATUSES for value in statuses.values()),
          "у каждой возможности статус из закрытого списка",
          str(sorted(set(statuses.values()))))
    bad_status = variant("bad-status", lambda d: [item.update({"status": "shippd"})
                                                  for item in d["features"]])
    problems = edition.baseline_problems(json.loads(bad_status.read_text(encoding="utf-8")))
    check(any("вне закрытого списка" in p for p in problems),
          "опечатка в статусе отвергается проверкой данных", str(problems[:2]))
    # Механика статуса `declared` проверяется на СИНТЕТИЧЕСКОЙ базовой линии.
    # До этапа И18 её держала живая: три объявленные возможности. После решения
    # (secret-broker написан, connectors и fleet сняты) объявленных не осталось,
    # и проверка механизма исчезла бы вместе с ними — то есть механизм перестал
    # бы проверяться ровно тогда, когда им снова воспользуются.
    def add_declared(data: dict, *, planned: str = "") -> None:
        entry = {"name": "trap-declared", "title": "ловушка проверки",
                 "edition": "pro", "status": "declared",
                 "operations": ["trap_declared_op"]}
        if planned:
            entry["planned"] = planned
        data["features"].append(entry)

    no_plan = variant("declared-no-plan", add_declared)
    problems = edition.baseline_problems(json.loads(no_plan.read_text(encoding="utf-8")))
    check(any("declared без поля planned" in p for p in problems),
          "запланированная возможность без planned отвергается", str(problems[:2]))
    with_plan = variant("declared-with-plan",
                        lambda d: add_declared(d, planned="не написан код брокера"))
    problems = edition.baseline_problems(json.loads(with_plan.read_text(encoding="utf-8")))
    check(not any("declared без поля planned" in p for p in problems),
          "запланированная возможность с planned принимается", str(problems[:2]))
    extra_plan = variant("shipped-plan", lambda d: [item.update({"planned": "лишнее"})
                                                    for item in d["features"]
                                                    if item.get("status") == "shipped"])
    problems = edition.baseline_problems(json.loads(extra_plan.read_text(encoding="utf-8")))
    check(any("shipped с полем planned" in p for p in problems),
          "готовая возможность с planned отвергается как противоречие", str(problems[:2]))
    # Объявленных и не написанных возможностей после этапа И18 нет: `secret-broker`
    # написан (`src/ragraf/broker.py`, операции в сборке), `connectors` и `fleet`
    # сняты из предмета уровней и названы в разделе `roadmap`. Пустой список
    # назван явно, а не выведен из данных: «пропажа» возможности из статуса
    # declared иначе прошла бы незамеченной.
    declared_names = sorted(name for name, value in statuses.items() if value == "declared")
    check(declared_names == [],
          "объявленных и не написанных возможностей не осталось",
          str(declared_names))
    roadmap_names = sorted(str(item.get("name")) for item in BASE.get("roadmap") or [])
    check(roadmap_names == ["connectors", "fleet"],
          "снятые возможности названы в разделе roadmap: connectors, fleet",
          str(roadmap_names))
    check(all(str(item.get("why") or "").strip() for item in BASE.get("roadmap") or []),
          "у каждой снятой возможности названа причина")
    # Возможность с двумя статусами сразу — тот же класс дефекта, что дубль
    # операции: читатель не может понять, продаётся она или нет.
    both = variant("roadmap-both", lambda d: d["roadmap"].append(
        {"name": "search", "why": "проверка ловушки"}))
    problems = edition.baseline_problems(json.loads(both.read_text(encoding="utf-8")))
    check(any("одновременно в features и в roadmap" in p for p in problems),
          "возможность сразу в features и в roadmap отвергается", str(problems[:2]))

    community_features = [str(item["name"]) for item in BASE["features"]
                          if item.get("edition") == "community"]
    paid_features = [str(item["name"]) for item in BASE["features"]
                     if item.get("edition") != "community"]
    check(bool(community_features) and bool(paid_features),
          "возможности разделены на базовые и платные",
          f"{community_features} / {paid_features}")
    check(not set(community_features) & set(paid_features),
          "ни одна возможность не объявлена и базовой, и платной")
    check("sso" in paid_features and "siem-export" in paid_features,
          "заявленные платные возможности названы (sso, siem-export)")

    print("\n2. Разрешение: заявление только понижает")
    state = edition.resolve(operations=BUILD_OPS)
    payload = state.to_dict()
    check(payload["resolved"] is True and payload["edition"] == "community",
          "по умолчанию действует Community", str(payload["edition"]))
    check(payload["features"] == community_features,
          "возможности Community совпадают с данными базовой линии",
          str(payload["features"]))
    missing_names = [str(item["name"]) for item in payload["missing"]]
    check(all(name in missing_names for name in paid_features),
          "платные возможности названы в missing, а не выданы за доступные",
          str(missing_names))
    check(all("status" in item for item in payload["missing"]),
          "у каждой строки missing есть поле status", str(payload["missing"][:1]))
    # Причина запланированной возможности проверяется на синтетической базовой
    # линии: у живой объявленных нет, и прежняя проверка стала бы пустой
    # (bool([]) — ложь, то есть зелёный результат без осмотра).
    synthetic = edition.resolve(operations=BUILD_OPS,
                                baseline_file=str(with_plan)).to_dict()
    planned_rows = [item for item in synthetic["missing"]
                    if item.get("status") == "declared"]
    check(bool(planned_rows) and all("запланированная" in str(item["reason"])
                                     for item in planned_rows),
          "причина запланированной возможности называет статус, а не только издание",
          str([str(item["reason"])[:50] for item in planned_rows]))
    ready_rows = [item for item in payload["missing"] if item.get("status") == "shipped"]
    check(all("запланированная" not in str(item["reason"]) for item in ready_rows),
          "у готовой возможности причина не называется запланированной",
          str([str(item["reason"])[:50] for item in ready_rows]))

    claimed = edition.resolve(declared="enterprise", operations=BUILD_OPS).to_dict()
    check(claimed["edition"] == "community",
          "заявленное enterprise без лицензии не действует", str(claimed["edition"]))
    check(any("лицензии нет" in note for note in claimed["notes"]),
          "понижение названо причиной, а не произведено молча", str(claimed["notes"]))

    unknown = edition.resolve(declared="gold", operations=BUILD_OPS)
    check(unknown.resolved is False and unknown.effective == edition.UNRESOLVED,
          "неизвестное заявленное издание делает слой неразрешённым",
          str(unknown.effective))
    check(any("закрытый список" in note for note in unknown.notes),
          "причина отказа называет закрытый список", str(unknown.notes))

    move_search_to_pro = variant(
        "search-pro",
        lambda d: [item.update({"edition": "pro"}) for item in d["features"]
                   if item["name"] == "search"])
    no_licence = edition.resolve(baseline_file=move_search_to_pro, operations=BUILD_OPS)
    decision = no_licence.decide("search")
    check(decision["allowed"] is False,
          "операция платной возможности на Community отвергается")
    check("не входит в издание" in decision["reason"] and "pro" in decision["reason"],
          "отказ называет издание, в котором возможность появляется", decision["reason"])
    check("search" not in no_licence.features,
          "отвергнутая возможность отсутствует в features", str(no_licence.features))

    pro_licence = sign_license(
        "pro.license",
        {"schema": 1, "edition": "pro", "licensee": "владелец",
         "issued_at": "2026-10-24", "expires_at": "2027-10-24"},
        key_a)
    with_licence = edition.resolve(baseline_file=move_search_to_pro,
                                   license_file=pro_licence, public_key=PUB_A,
                                   operations=BUILD_OPS)
    check(with_licence.effective == "pro",
          "подписанная лицензия даёт издание pro", str(with_licence.effective))
    check(with_licence.decide("search")["allowed"] is True,
          "с лицензией pro платная возможность разрешена")
    check("search" in with_licence.features,
          "разрешённая возможность появляется в features", str(with_licence.features))

    downgraded = edition.resolve(declared="community", baseline_file=move_search_to_pro,
                                 license_file=pro_licence, public_key=PUB_A,
                                 operations=BUILD_OPS)
    check(downgraded.effective == "community",
          "заявление community понижает издание даже при лицензии pro",
          str(downgraded.effective))
    check("search" not in downgraded.features,
          "понижение не расширяет возможности", str(downgraded.features))
    check(any("заявлено community" in note for note in downgraded.notes),
          "понижение относительно лицензии названо причиной", str(downgraded.notes))

    self_signed = edition.resolve(baseline_file=move_search_to_pro,
                                  license_file=pro_licence, public_key="-",
                                  operations=BUILD_OPS)
    check(self_signed.license.get("state") == "untrusted",
          "без доверенного ключа подпись не даёт издания (состояние untrusted)",
          str(self_signed.license))
    check("авторство" in str(self_signed.license.get("reason", "")),
          "причина называет неподтверждённое авторство, а не подделку",
          str(self_signed.license.get("reason")))
    check(self_signed.effective == "community" and "search" not in self_signed.features,
          "самоподписанная лицензия не открывает платную возможность",
          str(self_signed.effective))

    print("\n3. Подпись файла лицензии")
    forged = sign_license("forged.license",
                          {"schema": 1, "edition": "enterprise", "licensee": "кто-то",
                           "expires_at": "2099-01-01"}, key_a)
    forged.write_bytes(forged.read_bytes().replace(b"enterprise", b"community"))
    state_forged = edition.resolve(license_file=forged, public_key=PUB_A,
                                   operations=BUILD_OPS)
    check(state_forged.license.get("state") == "forged",
          "правка содержимого после подписи — состояние forged",
          str(state_forged.license))
    check("подпис" in str(state_forged.license.get("reason", "")),
          "причина отказа называет подпись, а не «неизвестное издание»",
          str(state_forged.license.get("reason")))
    check(state_forged.effective == "community",
          "подделанная лицензия оставляет Community, а не падает",
          str(state_forged.effective))

    foreign = sign_license("foreign.license",
                          {"schema": 1, "edition": "pro", "licensee": "кто-то",
                           "expires_at": "2099-01-01"}, key_b)   # подписано ЧУЖИМ ключом
    state_foreign = edition.resolve(license_file=foreign, public_key=PUB_A,
                                    operations=BUILD_OPS)
    check(state_foreign.license.get("state") == "forged",
          "подпись чужим ключом отвергается", str(state_foreign.license))
    check("другим ключом" in str(state_foreign.license.get("reason", "")),
          "причина называет другой ключ, а не «неизвестное издание»",
          str(state_foreign.license.get("reason")))
    check(state_foreign.effective == "community",
          "чужая подпись не даёт платного издания", str(state_foreign.effective))

    expired = sign_license("expired.license",
                           {"schema": 1, "edition": "pro", "licensee": "владелец",
                            "expires_at": "2020-01-01"}, key_a)
    state_expired = edition.resolve(license_file=expired, public_key=PUB_A,
                                    operations=BUILD_OPS)
    check(state_expired.license.get("state") == "expired",
          "истёкший срок распознаётся", str(state_expired.license))
    check("2020-01-01" in str(state_expired.license.get("reason", "")),
          "причина называет дату истечения", str(state_expired.license.get("reason")))
    check(state_expired.effective == "community",
          "истёкшая лицензия не даёт платного издания", str(state_expired.effective))

    wrong_edition = sign_license("gold.license",
                                 {"schema": 1, "edition": "gold", "licensee": "владелец"},
                                 key_a)
    state_gold = edition.resolve(license_file=wrong_edition, public_key=PUB_A,
                                 operations=BUILD_OPS)
    check(state_gold.license.get("state") == "broken",
          "неизвестное издание внутри подписанного файла — состояние broken",
          str(state_gold.license))
    check("закрытом" in str(state_gold.license.get("reason", ""))
          and "gold" in str(state_gold.license.get("reason", "")),
          "причина называет и имя, и закрытый список",
          str(state_gold.license.get("reason")))

    no_edition = sign_license("no-edition.license",
                              {"schema": 1, "licensee": "владелец"}, key_a)
    state_no_edition = edition.resolve(license_file=no_edition, public_key=PUB_A,
                                       operations=BUILD_OPS)
    check(state_no_edition.license.get("state") == "broken"
          and "edition" in str(state_no_edition.license.get("reason", "")),
          "файл без названного издания отвергается с указанием поля",
          str(state_no_edition.license.get("reason")))

    absent = edition.resolve(license_file=TMP / "нет-такого.license", operations=BUILD_OPS)
    check(absent.license.get("state") == "absent" and absent.effective == "community",
          "отсутствие файла лицензии — состояние absent, действует Community",
          str(absent.license))

    try:
        state = edition.resolve(baseline_file=TMP / "нет-базовой-линии.json",
                                operations=BUILD_OPS)
        check(state.resolved is False and any("базовой линии" in note for note in state.notes),
              "нет базовой линии — слой неразрешён, причина названа", str(state.notes))
        check(state.decide("search")["allowed"] is False,
              "неразрешённый слой отвергает операцию, а не разрешает её")
    except edition.EditionRefusal as exc:  # запасной путь: отказ тоже верен
        check(True, "нет базовой линии — отказ с причиной", str(exc))

    print("\n4. Гейт на живом фасаде")
    cfg = load_config(str(REPO / "config" / "ragraf.yaml"))
    facade = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    envelope = facade.make_env("selftest").to_dict()
    check(envelope.get("edition") == "community",
          "конверт ответа несёт издание", str(envelope.get("edition")))
    check(envelope.get("features") == community_features,
          "конверт ответа несёт набор возможностей", str(envelope.get("features")))
    allowed = facade.op_drift_report(limit=1)
    # `found` здесь не проверяется: пустой журнал расхождений дает found=false
    # законно (индекс соответствует дереву), и требовать true значило бы
    # требовать дефекта. Проверяется отсутствие отказа.
    check(not allowed.get("error"),
          "разрешённая операция выполняется", str(allowed.get("error")))
    check(allowed.get("edition") == "community" and "audit" in allowed.get("features", []),
          "конверт выполненной операции называет издание и возможность",
          str(allowed.get("features")))

    audit_to_pro = variant(
        "audit-pro",
        lambda d: [item.update({"edition": "pro"}) for item in d["features"]
                   if item["name"] == "audit"])
    set_env(baseline=audit_to_pro)
    try:
        gated = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        refused = gated.op_drift_report(limit=1)
        check(refused.get("found") is False,
              "операция вне издания не выполняется", str(refused.get("found")))
        check(str(refused.get("error", "")).startswith("EditionRefusal"),
              "отказ помечен как EditionRefusal", str(refused.get("error")))
        refused_block = refused.get("refused") or {}
        check(refused_block.get("operation") == "drift_report"
              and refused_block.get("requires_edition") == "pro",
              "раздел refused называет операцию и нужное издание", str(refused_block))
        check("audit" not in refused.get("features", []),
              "отвергнутая возможность отсутствует в features ответа",
              str(refused.get("features")))
        access = gated._access_log_ready  # noqa: SLF001 — проверяется факт записи
        entries = []
        try:
            from ragraf.access import AccessLog

            entries = AccessLog(control=gated.control).summary(limit=3)["entries"]
        except Exception as exc:  # noqa: BLE001
            entries = [{"reason": f"журнал недоступен: {type(exc).__name__}"}]
        denied = [row for row in entries if str(row.get("result")) == "denied"
                  and "edition" in str(row.get("reason", ""))]
        check(access is True and bool(denied),
              "отказ записан в журнал доступа", str(entries[:1]))
    finally:
        clear_env()

    broken = variant("broken", lambda d: d.update({"schema": 99}))
    set_env(baseline=broken)
    try:
        closed = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        closed_env = closed.make_env("selftest").to_dict()
        check(closed_env.get("edition") == edition.UNRESOLVED,
              "битая базовая линия делает конверт неразрешённым",
              str(closed_env.get("edition")))
        check(any("schema" in note for note in closed_env.get("edition_notes", [])),
              "причина неразрешённого слоя названа в конверте",
              str(closed_env.get("edition_notes")))
        closed_refusal = closed.op_drift_report(limit=1)
        check(str(closed_refusal.get("error", "")).startswith("EditionRefusal"),
              "битая базовая линия закрывает операции, а не открывает их",
              str(closed_refusal.get("error")))
        check(closed_refusal.get("features") == [],
              "неразрешённый слой не обещает ни одной возможности",
              str(closed_refusal.get("features")))
    finally:
        clear_env()

    print("\n5. Командная строка отвечает тем же состоянием")
    cli = REPO / "tools" / "ragraf.py"
    proc = subprocess.run([sys.executable, str(cli), "--json", "edition"],
                          cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    check(proc.returncode == 0, "код 0 у действующего издания", proc.stderr[-200:])
    try:
        data = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        data = {}
    check(data.get("edition") == "community" and len(data.get("features") or []) == len(community_features),
          "вывод команды совпадает с конвертом", str(proc.stdout[:120]))

    proc = subprocess.run([sys.executable, str(cli), "--json", "edition", "--declared", "gold"],
                          cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    check(proc.returncode == 1, "код 1 у неразрешённого слоя", str(proc.returncode))
    try:
        data = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        data = {}
    check(data.get("resolved") is False and any("закрытый список" in note
                                                for note in data.get("notes") or []),
          "команда называет закрытый список при неизвестном издании", str(data.get("notes")))

    proc = subprocess.run([sys.executable, str(cli), "--json", "edition",
                           "--license", str(forged)],
                          cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    try:
        data = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        data = {}
    check((data.get("license") or {}).get("state") in ("forged", "untrusted"),
          "команда не считает самоподписанную лицензию действующей",
          str((data.get("license") or {}).get("state")))
    check(proc.returncode == 0,
          "подделка лицензии не ломает базовое издание (код 0)", str(proc.returncode))

    print("\n6. Модуля возможности нет в сборке — отказ, а не трассировка")
    # Дефект назван в `enterprise/PRO-TIER.md` §5: импорт платного модуля стоял ВНЕ
    # try, и при выносе модуля из поставки операция падала ModuleNotFoundError —
    # то есть клиент получал трассировку вместо «возможности нет в этой сборке».
    # Проверяется ПОВЕДЕНИЕ на копии `src` (песочница): живой файл не
    # переименовывается ни на миг. Лицензия enterprise действует, поэтому отказ
    # гейта издания исключён — проверяется ровно наличие кода.
    degraded = TMP / "degraded-src"
    shutil.copytree(REPO / "src" / "ragraf", degraded / "ragraf")
    removed = {"sso.py": "sso_login", "siem.py": "siem_test",
               "attest.py": "attest_bundle", "license_offline.py": "license_offline",
               "quality_loop.py": "quality_suite", "broker.py": "broker_issue"}
    for name in removed:
        target = degraded / "ragraf" / name
        target.rename(target.with_suffix(".absent"))
    degraded_license = sign_license(
        "degraded-enterprise.license",
        {"schema": 1, "edition": "enterprise", "licensee": "проверка деградации",
         "issued_at": "2026-01-01", "expires_at": "2099-12-01"}, key_a)
    probe_code = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from ragraf.config import load
from ragraf.facade import Facade
facade = Facade(load(sys.argv[2]), tool="selftest", telemetry=False, watch=False)
out = {"edition": facade.edition_state().effective}
for op in sys.argv[3].split(","):
    try:
        answer = getattr(facade, "op_" + op)()
        out[op] = {"error": answer.get("error"), "found": answer.get("found"),
                   "refused": answer.get("refused")}
    except BaseException as exc:
        out[op] = {"traceback": f"{type(exc).__name__}: {exc}"}
print(json.dumps(out, ensure_ascii=False))
"""
    probe_env = {key: value for key, value in os.environ.items()
                 if not key.startswith("RAGRAF_EDITION")}
    probe_env[edition.LICENSE_ENV] = str(degraded_license)
    probe_env[edition.PUBKEY_ENV] = PUB_A
    probe_env["PYTHONIOENCODING"] = "utf-8"
    probe = subprocess.run(
        [sys.executable, "-c", probe_code, str(degraded), str(REPO / "config" / "ragraf.yaml"),
         ",".join(removed.values())],
        capture_output=True, text=True, encoding="utf-8", cwd=str(TMP), env=probe_env)
    payload: dict = {}
    for line in (probe.stdout or "").splitlines():
        if line.startswith("{"):
            payload = json.loads(line)
    check(bool(payload), "проба на копии src выполнена", (probe.stderr or "")[-200:])
    check(payload.get("edition") == "enterprise",
          "действует enterprise: отказ идёт по отсутствию модуля, а не по изданию",
          str(payload.get("edition")))
    for module, operation in removed.items():
        row = payload.get(operation) or {}
        refused = row.get("refused") or {}
        reason = str(refused.get("reason") or "")
        check("traceback" not in row,
              f"{operation}: отказ вместо трассировки", str(row)[:160])
        check(str(row.get("error") or "").startswith("FeatureUnavailable"),
              f"{operation}: отказ помечен FeatureUnavailable", str(row.get("error"))[:140])
        check(module in reason and "нет в этой сборке" in reason,
              f"{operation}: причина называет отсутствующий модуль",
              reason[:160])
        check(refused.get("operation") == operation and bool(refused.get("feature")),
              f"{operation}: раздел refused называет операцию и возможность",
              str(refused.get("operation")))

    # Числа и статусы в документах изданий — против ОБЪЯВЛЕНИЯ (базовой линии), а не
    # друг друга. Измерено 26.10.2026: EDITIONS-PRO.md держал `shared-index` в статусе
    # declared («ничего в сборке») при shipped, называл «10 возможностей» enterprise и
    # «шесть» pro при фактических 11 и 7, раздел 5 — 73 и 83 свойства тестов при 95 и
    # 154, а документ уровня Pro дублировал строку `shared-index`. Класс тот же, что
    # числа README: расходится молча и читается как действующее описание.
    baseline_doc = json.loads((REPO / "src/ragraf/edition_baseline.json").read_text(encoding="utf-8"))
    features = baseline_doc.get("features") or []
    pro_text = (REPO / "docs/03_implementation/EDITIONS-PRO.md").read_text(encoding="utf-8")
    delivery_text = (REPO / "enterprise/PRO-TIER.md").read_text(encoding="utf-8")
    shipped = [item for item in features if item.get("status") == "shipped"]
    pro_count = len([item for item in shipped if item.get("edition") in ("community", "pro")])
    enterprise_count = len(shipped)

    for item in features:
        if item.get("edition") not in ("pro", "enterprise"):
            continue  # базовые возможности Community в этом документе не таблицей
        name, status = str(item.get("name")), str(item.get("status"))
        row = re.search(rf"\| `{re.escape(name)}` \| (?:community|pro|enterprise) \| `(\w+)` \|",
                        pro_text)
        rows_found = len(re.findall(rf"\| `{re.escape(name)}` \| (?:community|pro|enterprise) \|",
                                    pro_text))
        check(row is not None and rows_found == 1 and row.group(1) == status,
              f"строка возможности {name} в EDITIONS-PRO.md одна и её статус = {status}",
              f"строк {rows_found}, статус в документе {row.group(1) if row else None}")

    stated_enterprise = re.search(r"у \*\*enterprise\*\* действуют \*\*(\d+) возможностей\*\*",
                                  pro_text)
    check(stated_enterprise is not None and int(stated_enterprise.group(1)) == enterprise_count,
          "число возможностей enterprise в EDITIONS-PRO.md равно объявлению",
          f"документ {stated_enterprise.group(1) if stated_enterprise else None}, "
          f"объявление {enterprise_count}")
    stated_pro = re.search(r"У \*\*pro\*\* действуют\s+\*\*(\d+)\*\*", pro_text)
    check(stated_pro is not None and int(stated_pro.group(1)) == pro_count,
          "число возможностей Pro в EDITIONS-PRO.md равно объявлению",
          f"документ {stated_pro.group(1) if stated_pro else None}, объявление {pro_count}")
    check(f"**{pro_count} возможностей**" in delivery_text,
          f"enterprise/PRO-TIER.md называет {pro_count} возможностей Pro")
    check(delivery_text.count("| `shared-index` | pro |") == 1,
          "в enterprise/PRO-TIER.md ровно одна строка shared-index",
          str(delivery_text.count("| `shared-index` | pro |")))

finally:
    clear_env()
    shutil.rmtree(TMP, ignore_errors=True)

print()
print(f"проверок: {PASS + FAIL}, успешно: {PASS}, провалов: {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
