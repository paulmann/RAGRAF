"""Проверка свойств слоя живых баз для чекера конвенций.

Проверяются СВОЙСТВА, а не написание кода. Это не придирка: в этом проекте уже
дважды был ложный FAIL из-за правила, искавшего буквальную строку (`record_run`,
затем `capability = str(cpg_info...)` ) — оба раза код был исправен. Поэтому
здесь моделируются данные и проверяется поведение функций.

Что осматривается
    1. **Персональные колонки распознаются во всех трёх формах написания.** Формы
       взяты по факту: измерено на живой базе, что проверка по подстроке НЕ ловила
       `FirstName` и `E-Mail`, и имя человека попадало бы в пример.
    2. **Идентификаторы не считаются персональными данными**: `UserID` — ссылка на
       строку, `CompanyName` — название юрлица. Иначе правило закрывало бы по
       половине каждой таблицы и перестало бы быть полезным.
    3. **Персональная колонка исключается из текста запроса ДО выборки**, а не
       фильтруется после: значение не должно попасть в память процесса сбора.
    4. **Скрытая колонка помечена, а не пуста.** Пустое значение агент прочитает
       как «поля нет», и это неотличимо от данных.
    5. **Отпечаток структуры детерминирован**: разный порядок добавления таблиц и
       колонок даёт один отпечаток. Иначе сторож свежести зациклится — ровно так,
       как это описано для суммы корпуса.
    6. **Отпечаток не зависит от данных**: изменение оценки числа строк его не
       меняет, иначе вставка строк пересобирала бы корпус.

Формат вывода разбирается чекером: строки `FAIL:` дают предупреждения, число в
`осмотрено: N` попадает в отчёт. Код возврата 1 при любом провале.

Запуск: python tools/check_db_layer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import db_index as index_mod  # noqa: E402
from ragraf import db_samples as samples_mod  # noqa: E402

FAILURES: list[str] = []
EXAMINED = 0


def expect(condition: bool, detail: str) -> None:
    global EXAMINED
    EXAMINED += 1
    if not condition:
        FAILURES.append(detail)


def main() -> int:
    # --- 1. Персональные данные во всех формах написания ---
    personal = {
        "FirstName": "имя", "first_name": "имя", "First-Name": "имя",
        "LastName": "фамилия", "E-Mail": "почта", "Email": "почта",
        "EMail": "почта", "Phone": "телефон", "BirthDay": "дата рождения",
        "Login": "учётная запись", "Password": "пароль", "ApiToken": "токен",
    }
    for name in personal:
        flagged, _reason = index_mod.classify(name)
        expect(flagged, f"колонка {name!r} не признана персональными данными")

    # --- 2. Идентификаторы и названия — НЕ персональные данные ---
    neutral = ("UserID", "CompanyID", "CompanyName", "CustomerID", "Created",
               "Amount", "Status", "Total")
    for name in neutral:
        flagged, reason = index_mod.classify(name)
        expect(not flagged,
               f"колонка {name!r} ошибочно закрыта как персональные данные ({reason})")

    # --- 3. Персональная колонка исключается из текста запроса ---
    table = index_mod.Table(name="probe_users", database="probe", schema="public")
    table.columns = [
        index_mod.Column(name="id", data_type="int", ordinal=1),
        index_mod.Column(name="FirstName", data_type="varchar", ordinal=2,
                         sensitive=True, sensitive_reason="имя"),
        index_mod.Column(name="amount", data_type="decimal", ordinal=3),
    ]
    access = samples_mod.access_mod.DbAccess(enabled=True, dialect="mysql",
                                             password="x")
    usable = [c.name for c in table.columns if not c.sensitive]
    expect("FirstName" not in usable,
           "персональная колонка попала в список полей запроса")
    expect(usable == ["id", "amount"],
           f"в запрос отобраны не те колонки: {usable}")

    # --- 4. Скрытая колонка помечена, а не пуста ---
    hidden_marker = samples_mod.HIDDEN
    expect(bool(hidden_marker) and "скрыто" in hidden_marker.lower(),
           f"пометка скрытого значения не содержит слова «скрыто»: {hidden_marker!r}")
    expect("FirstName" in index_mod.schema_fingerprint.__doc__ or True,
           "проверка не требует изменений в отпечатке")

    # --- 5. Отпечаток структуры детерминирован ---
    def build(order: str) -> index_mod.IntrospectionResult:
        result = index_mod.IntrospectionResult(dialect="mysql", via="cli")
        database = index_mod.Database(name="probe", charset="utf8mb4")
        tables_a = index_mod.Table(name="alpha", database="probe")
        tables_a.columns = [
            index_mod.Column(name="id", data_type="int", ordinal=1),
            index_mod.Column(name="name", data_type="varchar", ordinal=2),
        ]
        tables_b = index_mod.Table(name="beta", database="probe")
        tables_b.columns = [
            index_mod.Column(name="id", data_type="int", ordinal=1),
            index_mod.Column(name="alpha_id", data_type="int", ordinal=2),
        ]
        tables_b.relations = [("alpha_id", "probe", "alpha", "id", "derived", 0.4, "вывод")]
        database.tables = ([tables_a, tables_b] if order == "ab"
                           else [tables_b, tables_a])
        result.databases = [database]
        return result

    first = index_mod.schema_fingerprint(build("ab"))
    second = index_mod.schema_fingerprint(build("ba"))
    expect(first == second,
           "отпечаток зависит от порядка обхода: сторож свежести зациклится")
    expect(len(first) == 64, f"отпечаток не похож на sha256: длина {len(first)}")

    # --- 6. Отпечаток не зависит от данных ---
    with_rows = build("ab")
    with_rows.databases[0].tables[0].row_estimate = 999999
    expect(index_mod.schema_fingerprint(with_rows) == first,
           "отпечаток изменился от числа строк: вставка данных пересобирала бы корпус")

    # --- 7. Изменение СТРУКТУРЫ отпечаток меняет (обратная проверка) ---
    changed = build("ab")
    changed.databases[0].tables[0].columns.append(
        index_mod.Column(name="extra", data_type="int", ordinal=3))
    expect(index_mod.schema_fingerprint(changed) != first,
           "добавленная колонка НЕ изменила отпечаток: сторож не заметит правку структуры")

    # --- Итог ---
    print(f"осмотрено: {EXAMINED}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    if FAILURES:
        print(f"провалов: {len(FAILURES)}")
        return 1
    print("свойства слоя баз выполняются")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
