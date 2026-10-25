"""Издание продукта и набор разрешённых возможностей.

Зачем отдельный модуль. До него `editions/` был КАРТОЙ удержания файлов, а не
механизмом: проверить «это Pro или Community» было нечем, поэтому возможности
платных изданий оставались обещанием. Модуль отвечает на один вопрос: какое
издание собрано и что ему разрешено.

Четыре свойства, которые он обязан держать (и которые проверяются поведением,
а не наличием слов в коде):

1. **Закрытый список изданий.** Набор имён — данные (`edition_baseline.json`),
   но проверка идёт по нему: имя вне списка получает ОТКАЗ с названной
   причиной. Подстановки «неизвестное значит community» нет: она превращает
   ошибку в молчаливое понижение прав.
2. **Возможности — данные.** Состав возможностей и операции, которые каждая
   открывает, лежат в файле, а не в условиях кода. Новая возможность
   объявляется там, и гейт узнаёт о ней без правки `edition.py`.
3. **Понижение не расширяет.** Действующее издание — НАИМЕНЬШЕЕ из заявленных,
   а платное издание без проверенной лицензии не действует вовсе. Иначе
   достаточно было бы написать в настройке `enterprise`.
4. **В ответе нет того, чего нет в сборке.** Возможность попадает в список
   только если ВСЕ её операции существуют в этой сборке. Объявленная, но не
   написанная возможность названа в `missing`, а не выдана за доступную.

Лицензия. Издание берётся из ПОДПИСАННОГО файла (`catalog/edition.license`),
подпись проверяется тем же кодом, что подписывает экспорт аудита и SBOM
(`ragraf.signing`, Ed25519). Подделанный файл — отказ с названной причиной
(«содержимое изменилось после подписи», «подпись не сходится с ключом»),
а не «неизвестное издание».

Границы, названные прямо:

* без PyNaCl подпись проверить нельзя — состояние `unverifiable`, действует
  Community, причина видна; это честная деградация, а не «лицензия верна»;
* отсутствие файла базовой линии делает слой неразрешённым (`unresolved`):
  все операции с гейтом получают отказ с причиной. Молчаливое «всё разрешено»
  здесь было бы хуже отказа, потому что проверка перестала бы осматривать.

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import signing, util

REPO = pathlib.Path(__file__).resolve().parents[2]
BASE_DIR = pathlib.Path(__file__).resolve().parent
BASELINE_FILE = BASE_DIR / "edition_baseline.json"
LICENSE_FILE = REPO / "catalog" / "edition.license"

BASELINE_ENV = "RAGRAF_EDITION_BASELINE"
LICENSE_ENV = "RAGRAF_EDITION_LICENSE"
DECLARED_ENV = "RAGRAF_EDITION"
# Доверенный открытый ключ лицензии. Нужен потому, что `signing.verify_file`
# проверяет подпись ключом, ВЛОЖЕННЫМ в файл подписи: такая проверка доказывает
# ЦЕЛОСТНОСТЬ (содержимое не менялось после подписи), но НЕ АВТОРСТВО — подписать
# свой текст своим ключом может кто угодно. Замер 24.10.2026: файл, подписанный
# чужим ключом, проходил проверку как valid, и слой выдавал издание pro.
PUBKEY_ENV = "RAGRAF_EDITION_PUBKEY"
PUBKEY_FILE = REPO / "catalog" / "edition.pub"

SUPPORTED_SCHEMA = 1
# Закрытый список статусов возможности. Значение вне списка — отказ проверки
# данных, а не «неизвестный статус, значит объявлена»: иначе опечатка
# `shippd` тихо означала бы что угодно, и объявленное без кода прошло бы
# в ответ как написанное.
FEATURE_STATUSES = ("shipped", "declared")

#: Состояние слоя, когда разрешить его нечем. Значение намеренно не похоже
#: на имя издания: спутать «не разрешено» с «Community» нельзя.
UNRESOLVED = "unresolved"


class EditionRefusal(RuntimeError):
    """Издание или возможность не разрешены, и причина названа.

    Исключение не подавляется: отказ должен быть виден и попасть в журнал.
    """


# ---------------------------------------------------------------------------
# Данные базовой линии
# ---------------------------------------------------------------------------


def baseline_path(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit)
    from_env = str(os.environ.get(BASELINE_ENV) or "").strip()
    if from_env:
        return pathlib.Path(from_env)
    return BASELINE_FILE


def license_path(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit)
    from_env = str(os.environ.get(LICENSE_ENV) or "").strip()
    if from_env:
        return pathlib.Path(from_env)
    return LICENSE_FILE


def load_baseline(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Прочитать и ПРОВЕРИТЬ базовую линию. Негодные данные — отказ с причиной.

    Проверка данных здесь, а не при использовании: файл разбирается один раз,
    и ошибка в нём не должна проявляться как «возможность просто не нашлась».
    """
    target = baseline_path(path)
    if not target.exists():
        raise EditionRefusal(f"нет файла базовой линии изданий: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — причина называется, а не глотается
        raise EditionRefusal(
            f"базовая линия изданий не разбирается ({type(exc).__name__}): {target}"
        ) from exc
    problems = baseline_problems(raw)
    if problems:
        raise EditionRefusal("базовая линия изданий не годна: " + "; ".join(problems))
    raw["_path"] = str(target)
    return raw


def baseline_problems(raw: Any) -> list[str]:
    """Причины, по которым базовая линия негодна. Пустой список — годна."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ["ожидался объект JSON"]
    if raw.get("schema") != SUPPORTED_SCHEMA:
        problems.append(
            f"schema={raw.get('schema')!r}, поддерживается {SUPPORTED_SCHEMA}"
        )
    order = raw.get("order")
    if not isinstance(order, list) or not order:
        problems.append("поле order пусто: закрытый список изданий не задан")
        return problems
    if len(set(order)) != len(order):
        problems.append("поле order повторяет имена изданий")
    for name in order:
        if not isinstance(name, str) or not name.strip():
            problems.append(f"в order недопустимое имя издания: {name!r}")
    default = raw.get("default")
    if default not in order:
        problems.append(f"default={default!r} отсутствует в order")
    license_block = raw.get("license")
    if not isinstance(license_block, dict):
        problems.append("раздел license не задан: неизвестно, с какого издания нужна лицензия")
    elif license_block.get("required_from") not in order:
        problems.append(
            f"license.required_from={license_block.get('required_from')!r} отсутствует в order"
        )
    features = raw.get("features")
    if not isinstance(features, list) or not features:
        problems.append("раздел features пуст: возможностей нет вовсе")
        return problems
    seen_names: set[str] = set()
    seen_ops: dict[str, str] = {}
    for item in features:
        if not isinstance(item, dict):
            problems.append(f"возможность не объектом: {item!r}")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            problems.append("возможность без имени")
            continue
        if name in seen_names:
            problems.append(f"возможность «{name}» объявлена дважды")
        seen_names.add(name)
        edition = item.get("edition")
        if edition not in order:
            problems.append(f"возможность «{name}»: издание {edition!r} отсутствует в order")
        status = item.get("status")
        if status not in FEATURE_STATUSES:
            problems.append(
                f"возможность «{name}»: статус {status!r} вне закрытого списка "
                f"{', '.join(FEATURE_STATUSES)}"
            )
        planned = str(item.get("planned") or "").strip()
        if status == "declared" and not planned:
            problems.append(
                f"возможность «{name}»: статус declared без поля planned — не названо, "
                "что именно не написано"
            )
        if status == "shipped" and planned:
            problems.append(
                f"возможность «{name}»: статус shipped с полем planned — "
                "данные противоречат сами себе"
            )
        operations = item.get("operations")
        if not isinstance(operations, list) or not operations:
            problems.append(f"возможность «{name}»: не перечислены операции")
            continue
        for op in operations:
            if not isinstance(op, str) or not op.strip():
                problems.append(f"возможность «{name}»: недопустимое имя операции {op!r}")
                continue
            op_name = op.strip()
            if op_name.startswith("op_"):
                problems.append(
                    f"возможность «{name}»: операция названа с префиксом: {op_name}"
                )
                op_name = op_name[3:]
            if op_name in seen_ops and seen_ops[op_name] != name:
                problems.append(
                    f"операция «{op_name}» объявлена в двух возможностях: "
                    f"«{seen_ops[op_name]}» и «{name}»"
                )
            seen_ops.setdefault(op_name, name)
    return problems


def editions(baseline: dict[str, Any]) -> list[str]:
    return [str(name) for name in baseline.get("order") or []]


def rank(baseline: dict[str, Any], name: str) -> int:
    order = editions(baseline)
    if name not in order:
        raise EditionRefusal(
            f"издание «{name}» не входит в закрытый список: {', '.join(order)}"
        )
    return order.index(name)


def feature_index(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["name"]): item for item in baseline.get("features") or []}


def operations_index(baseline: dict[str, Any]) -> dict[str, list[str]]:
    """Операция → возможности, которые её открывают (объявлено в данных)."""
    out: dict[str, list[str]] = {}
    for item in baseline.get("features") or []:
        for op in item.get("operations") or []:
            key = str(op).strip()
            if key.startswith("op_"):
                key = key[3:]
            out.setdefault(key, []).append(str(item["name"]))
    return out


# ---------------------------------------------------------------------------
# Состав операций сборки
# ---------------------------------------------------------------------------


def build_operations(facade_class: Any | None = None) -> frozenset[str]:
    """Какие операции (`op_*`) реально есть в этой сборке.

    Источник — объявления класса фасада, а не список в данных: иначе «нет в
    сборке» определялось бы тем же файлом, который это утверждает.
    """
    if facade_class is None:
        try:
            from .facade import Facade as facade_class  # локальный импорт: тяжёлые зависимости
        except Exception as exc:  # noqa: BLE001 — причина называется
            raise EditionRefusal(
                f"не удалось определить состав операций сборки: {type(exc).__name__}"
            ) from exc
    names = set()
    for attr in vars(facade_class):
        if attr.startswith("op_") and not attr.startswith("op__"):
            names.add(attr[3:])
    return frozenset(names)


# ---------------------------------------------------------------------------
# Лицензия
# ---------------------------------------------------------------------------


@dataclass
class LicenseInfo:
    """Что известно о файле лицензии. Причина названа в любом состоянии."""

    state: str = "absent"          # absent | valid | forged | expired | unverifiable | untrusted | broken
    edition: str = ""
    licensee: str = ""
    expires_at: str = ""
    file: str = ""
    reason: str = ""

    @property
    def valid(self) -> bool:
        return self.state == "valid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "edition": self.edition,
            "licensee": self.licensee,
            "expires_at": self.expires_at,
            "file": self.file,
            "reason": self.reason,
        }


def trusted_public_key(explicit: str | pathlib.Path | None = None) -> tuple[str, str]:
    """Доверенный открытый ключ лицензии и откуда он взят.

    Пустой ключ — доверенного ключа нет, и тогда авторство лицензии не
    подтверждается: подпись говорит только о целостности файла.
    """
    if explicit is not None and str(explicit).strip() == "-":
        # Явный отказ от доверия: нужен, чтобы проверка могла показать
        # самоподписанную лицензию и чтобы владелец мог ответить «не доверять
        # ни одному ключу этой машины», не удаляя файлы.
        return "", "доверие отключено вызовом (аргумент «-»)"
    if explicit:
        return str(explicit).strip(), "аргумент вызова"
    from_env = str(os.environ.get(PUBKEY_ENV) or "").strip()
    if from_env:
        return from_env, f"переменная окружения {PUBKEY_ENV}"
    if PUBKEY_FILE.exists():
        text = PUBKEY_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text, f"файл {PUBKEY_FILE.name}"
    # Ключ подписи этой машины даёт тот же открытый ключ: отдельный файл не
    # обязателен там, где подпись ставится этим же каталогом состояния.
    key_file = signing.key_path()
    if key_file.exists():
        try:
            return signing.public_key_b64(key_file), f"ключ подписи {key_file.name}"
        except Exception:  # noqa: BLE001 — нет ключа — значит нет и доверия
            return "", "ключ подписи есть, но открытый ключ не получен"
    return "", "доверенного ключа нет"


def read_license(path: str | pathlib.Path | None = None,
                 baseline: dict[str, Any] | None = None,
                 public_key: str | None = None) -> LicenseInfo:
    """Проверить подпись файла лицензии и прочитать издание ИЗ ПОДПИСАННОГО.

    Порядок не случаен: сначала подпись, потом данные. Обратный порядок
    позволил бы подделать издание правкой текста, оставив прежнюю подпись.
    """
    target = license_path(path)
    info = LicenseInfo(file=str(target))
    if not target.exists():
        info.reason = f"файла лицензии нет: {target}"
        return info
    try:
        signing.verify_file(target)
    except signing.SigningUnavailable as exc:
        info.state = "unverifiable"
        info.reason = f"проверка подписи невозможна: {exc}"
        return info
    except signing.SignatureBroken as exc:
        info.state = "forged"
        info.reason = f"файл лицензии не прошёл проверку подписи: {exc}"
        return info
    except Exception as exc:  # noqa: BLE001 — неожидаемое не выдаётся за норму
        info.state = "broken"
        info.reason = f"проверка подписи завершилась ошибкой {type(exc).__name__}: {exc}"
        return info

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        info.state = "broken"
        info.reason = f"подписанный файл лицензии не разбирается: {type(exc).__name__}"
        return info
    if not isinstance(payload, dict):
        info.state = "broken"
        info.reason = "подписанный файл лицензии не является объектом JSON"
        return info

    # Целостность подтверждена. Теперь АВТОРСТВО: ключ из файла подписи должен
    # совпасть с доверенным. Без этой сверки подпись доказывала бы только то,
    # что файл не менялся после того, как его подписал кто угодно.
    # Открытый ключ подписи живёт в ФАЙЛЕ ПОДПИСИ, а не в подписанном тексте:
    # именно им проверялась подпись. Читать его из файла лицензии значило бы
    # сравнивать доверенный ключ с полем, которое подписывающий может написать
    # любым — и сверка авторства превратилась бы в формальность.
    # Замер 24.10.2026: в первом варианте читалось неверное поле, и все лицензии
    # отвергались как «другой ключ» (пустая строка вместо ключа).
    sig_file = target.with_name(target.name + ".sig")
    try:
        sig_payload = json.loads(sig_file.read_text(encoding="utf-8"))
        signer = str(sig_payload.get("public_key") or "").strip()
    except Exception as exc:  # noqa: BLE001
        info.state = "broken"
        info.reason = f"файл подписи не разбирается: {type(exc).__name__}"
        return info

    trusted, trusted_source = trusted_public_key(public_key)
    signer = signer or "(пусто)"
    if not trusted:
        info.state = "untrusted"
        info.reason = (
            "целостность файла подтверждена, но доверенного ключа нет "
            f"({trusted_source}): авторство лицензии не подтверждено, платное "
            "издание не действует"
        )
        return info
    if signer != trusted:
        info.state = "forged"
        info.reason = (
            "подпись сделана другим ключом: в файле подписи "
            f"«{signer[:16]}…», доверенный «{trusted[:16]}…» ({trusted_source})"
        )
        return info

    block = (baseline or {}).get("license") if isinstance(baseline, dict) else None
    block = block if isinstance(block, dict) else {}
    edition_field = str(block.get("edition_field") or "edition")
    expiry_field = str(block.get("expiry_field") or "expires_at")
    licensee_field = str(block.get("licensee_field") or "licensee")

    claimed = str(payload.get(edition_field) or "").strip()
    info.licensee = str(payload.get(licensee_field) or "").strip()
    info.expires_at = str(payload.get(expiry_field) or "").strip()

    if baseline is not None:
        order = editions(baseline)
    else:
        order = []
    if not claimed:
        info.state = "broken"
        info.reason = (
            f"в подписанном файле лицензии нет поля «{edition_field}»: издание не названо"
        )
        return info
    if order and claimed not in order:
        info.state = "broken"
        info.reason = (
            f"в файле лицензии названо издание «{claimed}», которого нет в закрытом "
            f"списке ({', '.join(order)})"
        )
        return info
    info.edition = claimed

    if info.expires_at:
        try:
            expires = time.strptime(info.expires_at, "%Y-%m-%d")
        except ValueError:
            info.state = "broken"
            info.reason = (
                f"срок действия лицензии «{info.expires_at}» не разбирается как ГГГГ-ММ-ДД"
            )
            return info
        if expires < time.localtime()[:3]:
            info.state = "expired"
            info.reason = f"срок действия лицензии истёк {info.expires_at}"
            return info

    info.state = "valid"
    info.reason = "подпись сходится, издание названо в подписанном файле"
    return info


# ---------------------------------------------------------------------------
# Состояние слоя
# ---------------------------------------------------------------------------


@dataclass
class EditionState:
    """Разрешённое издание и его возможности. Все поля — измеренные факты."""

    declared: str = "community"
    effective: str = "community"
    features: list[str] = field(default_factory=list)
    license: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    baseline: str = ""
    resolved: bool = True
    operations: list[str] = field(default_factory=list)
    _baseline: dict[str, Any] = field(default_factory=dict, repr=False)

    # ------------- свойства -------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "edition": self.effective,
            "resolved": self.resolved,
            "features": list(self.features),
            "missing": [dict(item) for item in self.missing],
            "license": dict(self.license),
            "notes": list(self.notes),
            "order": list(self.order),
            "baseline": self.baseline,
            "operations_declared": len(self.operations),
        }

    def feature_edition(self, feature: str) -> str:
        item = feature_index(self._baseline).get(feature) or {}
        return str(item.get("edition") or "")

    def decide(self, operation: str) -> dict[str, Any]:
        """Разрешена ли операция. Возвращает решение с причиной в любом случае.

        Причина нужна и при разрешении: она попадает в отчёт и объясняет,
        ПОЧЕМУ операция доступна (`feature`), а не оставляет читателя
        догадываться.
        """
        op = str(operation or "").strip()
        if op.startswith("op_"):
            op = op[3:]
        if not self.resolved:
            return {
                "allowed": False,
                "operation": op,
                "reason": self.notes[0] if self.notes else "слой изданий не разрешён",
                "feature": None,
                "requires_edition": None,
            }
        claims = operations_index(self._baseline).get(op) or []
        if not claims:
            return {
                "allowed": False,
                "operation": op,
                "reason": (
                    f"операция «{op}» не объявлена в базовой линии изданий: "
                    "неизвестная операция не разрешается по умолчанию"
                ),
                "feature": None,
                "requires_edition": None,
            }
        available = set(self.features)
        for name in claims:
            if name in available:
                return {
                    "allowed": True,
                    "operation": op,
                    "reason": f"возможность «{name}» входит в издание {self.effective}",
                    "feature": name,
                    "requires_edition": self.feature_edition(name),
                }
        # Ни одна возможность не действует: выбирается самая близкая причина.
        name = claims[0]
        required = self.feature_edition(name)
        dropped = {str(item.get("name")): item for item in self.missing}
        absent = list((dropped.get(name) or {}).get("absent_operations") or [])
        feature = feature_index(self._baseline).get(name) or {}
        status = str(feature.get("status") or "").strip()
        planned = str(feature.get("planned") or "").strip()
        if status == "declared":
            # Причина отказа обязана называть статус: иначе «нужно издание pro»
            # читается как «допишите лицензию и всё заработает», хотя кода нет.
            reason = (
                f"возможность «{name}» объявлена как запланированная (издание {required}): "
                f"в сборке её операций нет — "
                f"{planned or 'что именно не написано, в данных не сказано'}"
            )
        elif absent:
            reason = (
                f"возможность «{name}» объявлена в издании {required}, но в сборке нет "
                f"её операций: {', '.join(str(x) for x in absent)}"
            )
        else:
            reason = (
                f"операция «{op}» не входит в издание {self.effective}: "
                f"возможность «{name}» появляется в издании {required}"
            )
        return {
            "allowed": False,
            "operation": op,
            "reason": reason,
            "feature": name,
            "requires_edition": required,
        }

    def require(self, operation: str) -> None:
        """Отказ, если операция вне издания. Единственная точка проверки."""
        decision = self.decide(operation)
        if not decision["allowed"]:
            raise EditionRefusal(str(decision["reason"]))


# ---------------------------------------------------------------------------
# Разрешение
# ---------------------------------------------------------------------------


def declared_edition() -> tuple[str, str]:
    """Что ЗАЯВЛЕНО как издание и откуда. Возвращает (издание, источник).

    Читается ФАЙЛ настроек, а не слияние с умолчаниями: `SettingsStore.load()`
    отдаёт значение по умолчанию для незаписанного ключа, и отличить «владелец
    выбрал community» от «ничего не выбрано» по нему нельзя. Замер
    24.10.2026: из-за этого действующая лицензия pro не давала ничего —
    умолчание читалось как явное заявление и понижало издание.
    """
    from_env = str(os.environ.get(DECLARED_ENV) or "").strip()
    if from_env:
        return from_env, f"переменная окружения {DECLARED_ENV}"
    try:
        from .settings import SettingsStore

        store = SettingsStore()
        raw = util.read_json(store.path, default=None)
        source_map: dict[str, Any] = raw if isinstance(raw, dict) else {}
        nested = source_map.get("settings")
        if isinstance(nested, dict):
            source_map = nested
        value = str(source_map.get("edition") or "").strip()
    except Exception:  # noqa: BLE001 — настройки не имеют права ломать слой
        value = ""
    if value:
        return value, "настройка edition"
    return "", "не задано"


def resolve(*, declared: str | None = None,
            license_file: str | pathlib.Path | None = None,
            baseline_file: str | pathlib.Path | None = None,
            public_key: str | None = None,
            operations: Iterable[str] | None = None) -> EditionState:
    """Разрешить издание и его возможности. Отказ — только через состояние.

    Никаких исключений на «нет файла» и «данные негодны»: состояние
    `resolved=False` с причиной в `notes`. Это деградация вместо падения, и
    вызывающий обязан показать причину, а не заменить её догадкой.
    """
    try:
        baseline = load_baseline(baseline_file)
    except EditionRefusal as exc:
        state = EditionState(
            declared=str(declared or ""),
            effective=UNRESOLVED,
            resolved=False,
            notes=[str(exc)],
        )
        return state

    order = editions(baseline)
    default = str(baseline.get("default") or order[0])
    ops = frozenset(operations) if operations is not None else None
    if ops is None:
        try:
            ops = build_operations()
        except EditionRefusal as exc:
            return EditionState(
                declared=default,
                effective=UNRESOLVED,
                resolved=False,
                notes=[str(exc)],
                baseline=str(baseline.get("_path") or ""),
                order=order,
                _baseline=baseline,
            )

    notes: list[str] = []
    if declared is None:
        claimed_raw, source = declared_edition()
        explicit = bool(claimed_raw)
        claimed = claimed_raw or default
        if not explicit:
            source = "значение по умолчанию"
    else:
        claimed, source, explicit = str(declared), "аргумент вызова", True
    try:
        rank(baseline, claimed)
    except EditionRefusal as exc:
        return EditionState(
            declared=claimed,
            effective=UNRESOLVED,
            resolved=False,
            notes=[f"заявленное издание недопустимо ({source}): {exc}"],
            baseline=str(baseline.get("_path") or ""),
            order=order,
            _baseline=baseline,
        )

    license_info = read_license(license_file, baseline, public_key)
    if license_info.state != "absent" and not license_info.valid:
        notes.append(
            f"лицензия не действует ({license_info.state}): {license_info.reason}"
        )

    required_from = str((baseline.get("license") or {}).get("required_from") or default)
    paid = rank(baseline, required_from)

    # Правило: ЛИЦЕНЗИЯ ДАЁТ, ЗАЯВЛЕНИЕ ТОЛЬКО ПОНИЖАЕТ.
    if license_info.valid:
        granted = rank(baseline, license_info.edition)
        if explicit:
            wanted = rank(baseline, claimed)
            if wanted < granted:
                notes.append(
                    f"лицензия допускает издание {license_info.edition}, но заявлено "
                    f"{claimed} ({source}) — действует {claimed}"
                )
            effective_name = order[min(wanted, granted)]
        else:
            effective_name = license_info.edition
    else:
        # Платное издание без действующей лицензии не действует: иначе
        # достаточно было бы написать нужное имя в настройке.
        if rank(baseline, claimed) >= paid:
            notes.append(
                f"заявлено издание {claimed} ({source}), но действующей лицензии нет — "
                f"возможности ограничены изданием {default}"
            )
            effective_name = default
        else:
            effective_name = claimed

    features_available: list[str] = []
    missing: list[dict[str, Any]] = []
    limit = rank(baseline, effective_name)
    for item in baseline.get("features") or []:
        name = str(item["name"])
        edition = str(item.get("edition"))
        # Статус едет в ответ вместе с причиной. Без него `missing` смешивал два
        # разных состояния: «написано, но действует другое издание» и «объявлено,
        # кода нет». Читатель видел одно и то же «появляется в издании pro» и не
        # мог отличить обещание от готовой работы.
        status = str(item.get("status") or "").strip()
        planned = str(item.get("planned") or "").strip()
        if rank(baseline, edition) > limit:
            if status == "declared":
                reason = (
                    "возможность объявлена как запланированная: в сборке её операций "
                    f"нет — {planned or 'что именно не написано, в данных не сказано'}"
                )
            else:
                reason = f"возможность появляется в издании {edition}"
            missing.append({
                "name": name,
                "edition": edition,
                "status": status,
                "reason": reason,
                "absent_operations": [],
            })
            continue
        declared_ops: list[str] = []
        for raw_op in item.get("operations") or []:
            op_name = str(raw_op).strip()
            if op_name.startswith("op_"):
                op_name = op_name[3:]
            declared_ops.append(op_name)
        absent = [op for op in declared_ops if op not in ops]
        if absent:
            if status == "declared":
                reason = (
                    "возможность объявлена как запланированная: в сборке нет её "
                    f"операций — {planned or 'что именно не написано, в данных не сказано'}"
                )
            else:
                reason = "в сборке нет операций, которые открывает возможность"
            missing.append({
                "name": name,
                "edition": edition,
                "status": status,
                "reason": reason,
                "absent_operations": absent,
            })
            continue
        features_available.append(name)

    if missing:
        notes.append(
            f"объявлено, но недоступно возможностей: {len(missing)} — причина у каждой "
            "названа в разделе missing"
        )

    return EditionState(
        declared=claimed,
        effective=effective_name,
        features=features_available,
        license=license_info.to_dict(),
        notes=notes,
        missing=missing,
        order=order,
        baseline=str(baseline.get("_path") or ""),
        operations=sorted(ops),
        _baseline=baseline,
    )


def describe(state: EditionState) -> str:
    """Короткая строка для журнала и отчёта: издание, возможностей, лицензия."""
    return (
        f"edition={state.effective} features={len(state.features)} "
        f"license={state.license.get('state') or 'absent'}"
    )
