"""Виды доказательства рёбер графа: чем именно подтверждена связь.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. До этапа 8 у ребра были `provenance` (stated|derived)
и `confidence`, но не было признака того, КАКИМ СПОСОБОМ получена связь. Между
тем `provenance = stated` означал сразу три разные вещи:

  * CPG (Joern) связал место вызова с целью — настоящее доказательство того,
    что вызов существует и адресован этому методу;
  * цель найдена разбором дерева по квалифицированному имени из текста вызова
    (`Class::method`, `пространство\\функция`) — привязка по имени;
  * вызов встроенной функции языка — цели в проекте нет вовсе.

Замер 30.10.2026 на rev-0896: из 11 621 ребра `calls` признак CPG
(`props.source = cpg`) имели 3 941, то есть треть. Остальные 7 680 попадали в
ответ `callers` как «подтверждённые», хотя CPG их не подтверждала: агент читал
привязку по имени как факт. Это и есть дефект, который закрывает этап 8.

ЧТО СЧИТАЕТСЯ ЧЕМ (правило одно на запись и на чтение):

  cpg_call        — ребро пришло из CPG: Joern видел вызов и назвал цель.
                    Точность привязки цели к нашему узлу видна отдельно, в
                    `evidence_how` (`fullname` — совпало полное имя,
                    `name_file` — имя и файл владельца, `own_class` — метод
                    объявлен в том же классе). Это единственный вид, который
                    ответы называют подтверждением.
  signature_match — CPG не участвовала: цель названа в тексте вызова
                    по-квалифицированному (или найдена разрешением имени с
                    участием файла/класса). Место сужает выбор, доказательства
                    вызова нет.
  name_inference  — цель выведена по ОДНОМУ имени: разрешение `@ext:` по
                    имени, `name_only`, поиск в базовых классах. Кандидатов
                    могло быть несколько.
  structural      — ребро объявления, а не вызова (`contains`, `defines`,
                    `imports`, `extends`, `queries`, `routes`…): вопрос «чем
                    подтверждён вызов» к нему не применим.
  unknown         — вид не определяется: ребро построено кодом, которого этот
                    модуль не знает. Такое ребро НЕ выдаётся за подтверждённое.

ПОЧЕМУ КЛАССИФИКАТОР ОДИН. Запись (`revisions.add_edges`, `cpg.merge_calls`) и
чтение обязаны отвечать одинаково. Как только появляется второй список правил,
ответ `callers` начинает расходиться с тем, что лежит в хранилище, и расходится
молча — ровно тот класс дефектов, который проект запрещает.

ОБРАТНАЯ СОВМЕСТИМОСТЬ. У рёбер, построенных до появления колонки
`evidence_kind`, её значение пустое. `classify` восстанавливает вид по полям
самого ребра (`kind`, `provenance`, `confidence`, `props`) — переиндексация не
обязательна, но и записанное значение не перетирается: явный вид всегда
приоритетнее выведенного.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

#: Виды доказательства, которые может нести ребро.
EVIDENCE_KINDS: tuple[str, ...] = (
    "cpg_call",
    "signature_match",
    "name_inference",
    "external_call",
    "structural",
    "unknown",
)

#: Человеческие названия: ими отвечают операции и документы.
LABELS: dict[str, str] = {
    "cpg_call": "подтверждено CPG",
    "signature_match": "по имени и месту объявления",
    "name_inference": "выведено по имени",
    # Этап 11: вызов ВНЕШНЕГО символа (встроенная функция, метод библиотеки).
    # Отдельный вид, потому что это не «связь с определением проекта, найденная
    # по имени»: определения в корпусе нет вовсе.
    "external_call": "цель вне корпуса (внешний символ)",
    "structural": "структурное ребро (не вызов)",
    "unknown": "вид не определён",
}

#: Виды, которые НЕ являются доказательством вызова.
#:
#: `signature_match` здесь по праву: привязка «по имени и месту объявления» —
#: это по-прежнему ВЫВОД, а не подтверждение. До этапа 11 вид отсутствовал в
#: этом списке, и ответ противоречил сам себе: примечание `edge_evidence`
#: называло `signature_match` выводом, а счётчик `derived` его не считал —
#: доля выводов занижалась (7 % вместо 23 %). Замер 31.10.2026, rev-0991:
#: 11 617 рёбер `signature_match` против 5 068 `cpg_call`.
#:
#: `external_call` здесь НЕ значится намеренно: это не «связь с кодом проекта,
#: которая не доказана», а «цели в корпусе нет». Другая природа, отдельный счёт.
DERIVED_KINDS: tuple[str, ...] = ("signature_match", "name_inference", "unknown")

#: Виды рёбер, к которым вопрос «чем подтверждён вызов» применим.
CALL_KINDS: tuple[str, ...] = ("calls", "calls_heuristic", "calls_builtin")

#: Способ разрешения цели у рёбер, пришедших из CPG (`props.resolved_by`).
#: Значения замерены по коду: `indexer._make_fqn_resolver` даёт `fullname`,
#: `name_file`, `name_only`; `cpg.merge_calls` добавляет `own_class` и варианты
#: поиска в базовых классах (`mro…`).
CPG_HOW: dict[str, str] = {
    "fullname": "cpg_call",
    "fqn": "cpg_call",
    "cpg": "cpg_call",
    "name_file": "cpg_call",
    "own_class": "cpg_call",
    # Этап 10: полное имя Joern, приведённое к нашей форме `путь:метод`.
    # Совпадение по всему fqn, поэтому это такая же привязка, как `fullname`:
    # CPG видела вызов, и цель выбрана по имени И месту, а не по одному имени.
    "fullname_norm": "cpg_call",
    # Имя без места: CPG вызов видела, но цель угадана по одному имени.
    "name_only": "name_inference",
}

#: Способ разрешения от индексатора (`props.resolved_by`): имя, а не CPG.
RESOLVER_HOW: dict[str, str] = {
    "fqn-name": "signature_match",
    "inherit-name": "signature_match",
}


def label(evidence_kind: str | None) -> str:
    """Человеческое название вида доказательства (для ответов и отчётов)."""
    return LABELS.get(str(evidence_kind or "").strip(), "вид не определён")


def is_call_kind(kind: str | None) -> bool:
    """Ребро описывает вызов (а не объявление)?."""
    return str(kind or "") in CALL_KINDS


def is_derived(evidence_kind: str | None) -> bool:
    """Считается ли вид выводом, а не подтверждением.

    `structural` выводом не считается: это не «связь не доказана», а «вопрос
    не про вызов». Но и подтверждением вызова оно не является — поэтому
    отдельное значение, а не `False`/`True`.
    """
    return str(evidence_kind or "") in DERIVED_KINDS


def _props_dict(props: Any) -> dict:
    """Свойства ребра как словарь: хранилище отдаёт JSON-строку."""
    if isinstance(props, Mapping):
        return dict(props)
    if isinstance(props, (bytes, bytearray)):
        try:
            props = props.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — свойства не имеют права ломать ответ
            return {}
    if isinstance(props, str) and props.strip():
        try:
            loaded = json.loads(props)
        except Exception:  # noqa: BLE001
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}
    return {}


def how_of(props: Any) -> str:
    """Способ привязки цели (`props.resolved_by`) — уточнение к виду."""
    return str(_props_dict(props).get("resolved_by") or "").strip()


def classify(
    kind: str | None,
    provenance: str | None = None,
    props: Any = None,
    confidence: float | None = None,
    evidence_path: str | None = None,
    evidence_line: int | None = None,
    target_external: bool = False,
) -> str:
    """Вид доказательства ребра по его собственным полям.

    Функция ЧИСТАЯ и детерминированная: те же поля дают тот же вид. Порядок
    правил важен и совпадает с тем, что записывается при построении.

    `evidence_path`/`evidence_line` — координата вызова. Она участвует в
    решении не для красоты: доказательство, которое нельзя открыть и увидеть
    глазами, доказательством не является. Ребро с признаком CPG, но без
    координаты, получает `unknown` — «вид не определён», а не «подтверждено».
    """
    kind = str(kind or "").strip()
    provenance = str(provenance or "").strip()
    data = _props_dict(props)

    # 1. Явно записанный вид приоритетнее любого вывода: перезапись уже
    #    известного значения выведенным затёрла бы факт догадкой.
    recorded = str(data.get("evidence_kind") or "").strip()
    if recorded in EVIDENCE_KINDS:
        return recorded

    how = str(data.get("resolved_by") or "").strip()
    source = str(data.get("source") or "").strip()

    # 2. Ребро пришло из CPG: вызов подтверждён, уточняется только привязка.
    if source == "cpg":
        # Доказательство обязано иметь координату: без неё связь не проверить,
        # и называть её подтверждённой нельзя.
        if not str(evidence_path or "").strip() or int(evidence_line or 0) <= 0:
            return "unknown"
        if how in CPG_HOW:
            return CPG_HOW[how]
        if how:
            # Варианты поиска в базовых классах (`mro`, `base_class…`):
            # порядок разрешения методов в графе не восстановлен, это вывод.
            return "name_inference"
        # CPG дала связь, но способ не записан (ранние ревизии): происхождение
        # остаётся единственным признаком.
        return "cpg_call" if provenance == "stated" else "name_inference"

    # 3. Разрешение имён индексатором: имя + место (файл, объявление класса).
    if how in RESOLVER_HOW:
        return RESOLVER_HOW[how]
    if how.startswith(("mro", "base")):
        return "name_inference"

    # 3б. ЦЕЛЬ ВНЕ КОРПУСА (этап 11). Если цель ребра — узел `external`
    # (встроенная функция языка, метод библиотеки, адрес-заглушка), то это НЕ
    # «связь с определением проекта, выведенная по имени»: определения в корпусе
    # нет. Замер 31.10.2026 (rev-0986): из 55 632 рёбер с видом `name_inference`
    # ВСЕ 55 632 вели в узлы `external` — то есть вид «выведено по имени»
    # описывал вызовы `print`, `get`, `len`, и доля «выводов 77 %» говорила о
    # внешних вызовах, а не о догадках про код проекта.
    if target_external and is_call_kind(kind):
        return "external_call"

    # 4. Эвристические виды рёбер: вывод по имени по определению.
    if kind.endswith("_heuristic"):
        return "name_inference"

    # 5. Ребро вызова без признака CPG: цель названа в тексте вызова.
    #    Достоверность ниже 0.5 означает, что места в привязке не было.
    if kind == "calls":
        try:
            conf = float(confidence) if confidence is not None else 1.0
        except (TypeError, ValueError):
            conf = 1.0
        return "name_inference" if conf < 0.5 else "signature_match"

    # 6. Встроенная функция языка: цель — не символ проекта. Связь следует из
    #    синтаксиса вызова, поэтому место в ней есть, а CPG её не подтверждала.
    if kind == "calls_builtin":
        return "signature_match"

    # 7. Прочие рёбра графа — объявления и структурные связи.
    if kind:
        return "structural"

    return "unknown"


def summarize(items: Iterable[Mapping[str, Any]], key: str = "evidence_kind") -> dict:
    """Сводка по видам доказательства: сколько каждого и какая доля выводов.

    Ответы `callers`/`callees`/`impact`/`slice` обязаны называть эту сводку:
    «связей 40» без неё читается как «40 доказанных связей», а замер показывает,
    что выведенных среди них большинство.
    """
    counts: dict[str, int] = {}
    total = 0
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
        total += 1
    derived = sum(count for kind, count in counts.items() if is_derived(kind))
    confirmed = counts.get("cpg_call", 0)
    return {
        "total": total,
        "counts": [
            {"evidence_kind": kind, "label": label(kind), "count": count}
            for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "confirmed": confirmed,
        "derived": derived,
        "derived_share": round(derived / total, 4) if total else 0.0,
        "confirmed_share": round(confirmed / total, 4) if total else 0.0,
    }


def describe_counts(counts: Mapping[str, int]) -> str:
    """Строка «подтверждено N, по месту M, по имени K» для текста ответа."""
    parts: list[str] = []
    for kind in EVIDENCE_KINDS:
        count = int(counts.get(kind) or 0)
        if count:
            parts.append(f"{label(kind)} — {count}")
    return "; ".join(parts) if parts else "рёбер нет"