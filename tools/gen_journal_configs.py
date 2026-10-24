"""Сгенерировать конфигурации журналов подсистемы /agreements/ (ТЗ §3.1).

Почему генератором: пять журналов имеют одну структуру конфигурации (колонки,
формы, защищённые поля, переходы), и писать их вручную пять раз — источник
расхождений. Скрипт собирает файлы дата-ориентированно из таблиц, снятых
через RAGRAF (op_schema), поэтому имена колонок соответствуют схеме фактически,
а не по памяти.

Табель отличается: у него нет No и Status (§4.0), права считаются по состоянию
периода — поэтому он описывается отдельной ветвью.
"""

from __future__ import annotations

import pathlib

OUT = pathlib.Path("C:/AutoClaw/RAGRAF/.staging/agreements/journals")

HEADER = """<?php

declare(strict_types=1);

/**
 * journals/{fname}.php — конфигурация журнала «{title}» ({code}-).
 *
 * Назначение
 *   Один файл = один журнал (ТЗ §3.1). Здесь только ДАННЫЕ: колонки списка,
 *   поля формы, защищённые поля, карта переходов состояний. Общий код
 *   (Registry, Access, шаблоны) читает этот массив и не содержит ветвлений
 *   вида «если журнал такой, то…».
 *
 * Источник описания
 *   Набор колонок и их порядок сняты из схемы (миграции 20261004_0013…0019)
 *   через операцию schema индекса RAGRAF — то есть соответствуют фактической
 *   таблице {table}, а не перечню по памяти. Это исключает класс дефектов,
 *   при котором код пишет в несуществующую колонку.
 *
 * Возврат: массив без побочных эффектов.
 */

return [
"""

FOOTER = """];

"""


def php_array(items: list[str], indent: str = "    ") -> str:
    return "".join(f"{indent}{line}\n" for line in items)


def make_config(
    *,
    key: str,
    code: str,
    title: str,
    prefix: str,
    table: str,
    fname: str,
    protected: list[str],
    columns: list[tuple[str, str, str, str]],
    form: list[tuple[str, list[tuple[str, str, str, str, str]]]],
    status_titles: dict[str, str],
    transitions: dict[str, list[str]],
    subject_field: str,
    scope_field: str,
) -> str:
    """Собрать текст конфигурации журнала.

    columns: (имя колонки, подпись, ширина, вид вывода)
    form:    (legend группы, [(колонка, подпись, тип, обязательность, справочник)])
    """

    out = [HEADER.format(fname=fname, title=title, code=code, table=table)]

    def s(value: str) -> str:
        return "'" + str(value).replace("'", "\\'") + "'"

    out.append("    'code'        => " + s(code) + ",\n")
    out.append("    'key'         => " + s(key) + ",\n")
    out.append("    'title'       => " + s(title) + ",\n")
    out.append("    'prefix'      => " + s(prefix) + ",\n")
    out.append("    'table'       => " + s(table) + ",\n")
    out.append(f"\n    // ЗАЩИЩЁННЫЕ ПОЛЯ (§5.7): после согласования правятся только\n"
               f"    // с причиной и с записью в аудит.\n")
    out.append("    'protected'   => [\n")
    out.append(php_array([f"{s(p)}," for p in protected], indent="        "))
    out.append("    ],\n")

    out.append(f"\n    // Колонки списка: одинаковый набор для страницы, поиска и экспорта.\n")
    out.append("    'columns'     => [\n")
    for name, label, width, kind in columns:
        out.append(f"        {s(name):<20} => ['title' => {s(label)}, 'width' => {s(width)},"
                   f" 'kind' => {s(kind)}],\n")
    out.append("    ],\n")

    out.append("\n    // Поля формы по смысловым группам (§8.4).\n")
    out.append("    'form'        => [\n")
    for legend, fields in form:
        out.append(f"        {s(legend)} => [\n")
        out.append(f"            'legend' => {s(legend)},\n")
        out.append("            'fields' => [\n")
        for col, flabel, ftype, required, dict_kind in fields:
            parts = [f"'title' => {s(flabel)}", f"'type' => {s(ftype)}"]
            if required:
                parts.append("'required' => true")
            if dict_kind:
                parts.append(f"'dict' => {s(dict_kind)}")
            if ftype in ("text", "textarea"):
                parts.append("'maxlength' => " + ("1000" if ftype == "textarea" else "255"))
            out.append(f"                {s(col):<18} => [" + ", ".join(parts) + "],\n")
        out.append("            ],\n")
        out.append("        ],\n")
    out.append("    ],\n")

    out.append("\n    // Подписи статусов журнала.\n")
    out.append("    'status_titles' => [\n")
    for st, ttl in status_titles.items():
        out.append(f"        {s(st):<15} => {s(ttl)},\n")
    out.append("    ],\n")

    out.append("\n    // Что допустимо в каком состоянии (§6.2). Действие вне карты\n"
               "    // для состояния не выполняется.\n")
    out.append("    'transitions' => [\n")
    for st, acts in transitions.items():
        joined = ", ".join(s(a) for a in acts)
        out.append(f"        {s(st):<15} => [{joined}],\n")
    out.append("    ],\n")

    out.append("\n    // Поле предмета (для поиска и подписи записи).\n")
    out.append(f"    'subject_field' => {s(subject_field)},\n")
    out.append(f"    'scope_field'   => {s(scope_field)},\n")

    out.append(FOOTER)
    return "".join(out)


# --------------------------------------------------------------------------
# Общие карты статусов и переходов: одинаковые для нумеруемых журналов.
# --------------------------------------------------------------------------

STATUS_TITLES = {
    "draft": "Черновик",
    "on_approval": "На согласовании",
    "approved": "Согласован",
    "rejected": "Отклонён",
    "rework": "На доработке",
    "cancelled": "Аннулирован",
    "archived": "В архиве",
}

BASE_TRANSITIONS = {
    "draft": ["submit", "update", "delete", "copy", "cancel"],
    "on_approval": ["approve", "reject", "withdraw", "cancel"],
    "rework": ["submit", "update", "cancel", "copy"],
    "on_review": ["approve", "reject", "withdraw", "cancel"],
    "rejected": ["submit", "update", "cancel", "copy"],
    "postponed": ["resume", "cancel", "copy"],
    "cancelled": ["copy"],
    "archived": ["copy"],
}

CONFIGS: list[dict] = [
    # ---------------- Договоры (DG) ----------------
    dict(
        key="contracts", code="DG", title="Договоры", prefix="DG-",
        table="DocContracts", fname="dg",
        subject_field="Subject", scope_field="DepartmentID",
        protected=["CounterpartyID", "OrgID", "Amount", "Currency", "KindCode",
                   "DocDate", "ValidFrom", "ValidTo", "Subject"],
        columns=[
            ("No", "Номер", "120px", "link"),
            ("DocDate", "Дата", "110px", "date"),
            ("CounterpartyID", "Контрагент", "auto", "counterparty"),
            ("Subject", "Предмет", "auto", "text"),
            ("Amount", "Сумма", "130px", "money"),
            ("KindCode", "Вид", "140px", "dict"),
            ("ValidTo", "Действует до", "120px", "date"),
            ("Status", "Статус", "150px", "status"),
        ],
        form=[
            ("Реквизиты договора", [
                ("DocDate", "Дата договора", "date", True, ""),
                ("No", "Номер договора", "text", False, ""),
                ("KindCode", "Вид договора", "dict", False, "contract_kind"),
                ("Subject", "Предмет договора", "text", True, ""),
            ]),
            ("Стороны и сумма", [
                ("OrgID", "Организация компании", "org", False, ""),
                ("CounterpartyID", "Контрагент", "counterparty", True, ""),
                ("Amount", "Сумма", "money", False, ""),
                ("Currency", "Валюта", "text", False, ""),
            ]),
            ("Сроки", [
                ("ValidFrom", "Действует с", "date", False, ""),
                ("ValidTo", "Действует до", "date", False, ""),
                ("SignedDate", "Дата подписания", "date", False, ""),
                ("OriginalPlace", "Место хранения оригинала", "text", False, ""),
            ]),
            ("Примечание", [
                ("CommentText", "Комментарий", "textarea", False, ""),
            ]),
        ],
        status_titles=dict(STATUS_TITLES, signed="Подписан"),
        transitions=dict(BASE_TRANSITIONS, approved=["sign", "cancel", "copy", "archive"]),
    ),
    # ---------------- Нарушения и депремирования (SH) ----------------
    dict(
        key="penalties", code="SH", title="Нарушения и депремирования", prefix="SH-",
        table="DocPenalties", fname="sh",
        subject_field="Description", scope_field="DepartmentID",
        protected=["UserID", "KindCode", "ViolationDate", "DeprimeAmount", "Description"],
        columns=[
            ("No", "Номер", "120px", "link"),
            ("ViolationDate", "Дата нарушения", "120px", "date"),
            ("UserID", "Сотрудник", "auto", "user"),
            ("KindCode", "Вид", "140px", "dict"),
            ("Description", "Описание", "auto", "text"),
            ("DeprimeAmount", "Депремирование", "140px", "money"),
            ("Status", "Статус", "150px", "status"),
        ],
        form=[
            ("Нарушение", [
                ("ViolationDate", "Дата нарушения", "date", True, ""),
                ("UserID", "Сотрудник", "employee", True, ""),
                ("KindCode", "Вид нарушения", "dict", True, "penalty_kind"),
                ("Description", "Описание", "textarea", True, ""),
                ("DeprimeAmount", "Сумма депремирования", "money", False, ""),
                ("BasisNote", "Основание", "text", False, ""),
            ]),
            ("Период", [
                ("PeriodMonth", "Месяц учёта", "text", False, ""),
            ]),
            ("Примечание", [
                ("CommentText", "Комментарий", "textarea", False, ""),
            ]),
        ],
        status_titles=dict(STATUS_TITLES, assigned="Назначено",
                           acknowledged="Ознакомлен"),
        transitions=dict(
            BASE_TRANSITIONS,
            approved=["acknowledge", "refuse_ack", "account", "cancel", "copy"],
        ),
    ),
    # ---------------- Отпуска (OT) ----------------
    dict(
        key="vacations", code="OT", title="Отпуска", prefix="OT-",
        table="DocVacations", fname="ot",
        subject_field="KindCode", scope_field="DepartmentID",
        protected=["UserID", "KindCode", "DateFrom", "DateTo", "Days"],
        columns=[
            ("No", "Номер", "120px", "link"),
            ("UserID", "Сотрудник", "auto", "user"),
            ("KindCode", "Вид отпуска", "150px", "dict"),
            ("DateFrom", "С", "110px", "date"),
            ("DateTo", "По", "110px", "date"),
            ("Days", "Дней", "80px", "text"),
            ("Status", "Статус", "150px", "status"),
        ],
        form=[
            ("Заявление", [
                ("UserID", "Сотрудник", "employee", True, ""),
                ("KindCode", "Вид отпуска", "dict", True, "vacation_kind"),
                ("DateFrom", "Дата начала", "date", True, ""),
                ("DateTo", "Дата окончания", "date", True, ""),
                ("Days", "Количество дней", "text", False, ""),
                ("SubstituteUserID", "Замещающий", "employee", False, ""),
            ]),
            ("Примечание", [
                ("CommentText", "Комментарий", "textarea", False, ""),
            ]),
        ],
        status_titles=dict(STATUS_TITLES, recalled="Отозван"),
        transitions=dict(BASE_TRANSITIONS, approved=["recall", "cancel", "copy", "archive"]),
    ),
    # ---------------- Задачи (TD) ----------------
    dict(
        key="tasks", code="TD", title="Задачи", prefix="TD-",
        table="DocTasks", fname="td",
        subject_field="Title", scope_field="DepartmentID",
        protected=["Title", "ResultCriteria", "DueDate", "AssigneeID", "Resources"],
        columns=[
            ("No", "Номер", "120px", "link"),
            ("Title", "Задача", "auto", "text"),
            ("AssigneeID", "Исполнитель", "auto", "user"),
            ("DueDate", "Срок", "110px", "date"),
            ("PriorityCode", "Приоритет", "120px", "dict"),
            ("Progress", "Готовность", "110px", "text"),
            ("Status", "Статус", "150px", "status"),
        ],
        form=[
            ("Постановка задачи", [
                ("Title", "Задача", "text", True, ""),
                ("ResultCriteria", "Критерий результата", "textarea", True, ""),
                ("Resources", "Ресурсы", "text", False, ""),
                ("DueDate", "Срок", "date", False, ""),
                ("PriorityCode", "Приоритет", "dict", False, "task_priority"),
                ("AssigneeID", "Исполнитель", "employee", False, ""),
            ]),
            ("Периодичность", [
                ("PeriodKind", "Тип периодичности", "text", False, ""),
                ("PeriodValue", "Значение периодичности", "text", False, ""),
            ]),
            ("Примечание", [
                ("CommentText", "Комментарий", "textarea", False, ""),
            ]),
        ],
        # Белый список статусов задач отличается от остальных журналов:
        # в нём нет on_approval и rework, зато есть new и in_progress
        # (проверено вызовом z_agr_statuses('TD') и схемой колонки Status).
        status_titles={
            "new": "Новая",
            "in_work": "В работе",
            "in_progress": "Выполняется",
            "on_review": "На проверке",
            "done": "Выполнена",
            "rejected": "Отклонена",
            "cancelled": "Аннулирована",
            "archived": "В архиве",
            "draft": "Черновик",
            "approved": "Принята",
        },
        # Переходы задач описаны по фактическим состояниям журнала:
        # задача не проходит общий маршрут согласования, её принимает постановщик.
        transitions={
            "new": ["update", "cancel", "copy"],
            "draft": ["submit", "update", "delete", "copy", "cancel"],
            "in_work": ["submit", "update", "cancel"],
            "in_progress": ["submit", "update", "cancel"],
            "on_review": ["approve", "reject", "withdraw"],
            "done": ["archive", "copy"],
            "approved": ["archive", "copy", "cancel"],
            "rejected": ["submit", "update", "cancel", "copy"],
            "cancelled": ["copy"],
            "archived": ["copy"],
        },
    ),
]


def main() -> int:
    written = 0
    for cfg in CONFIGS:
        text = make_config(**cfg)
        # Путь — относительно корпуса, поэтому файл кладётся в staging.
        path = OUT / f"{cfg['fname']}.php"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"записан {path.name}: {len(text.splitlines())} строк")
        written += 1
    print(f"всего конфигураций: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
