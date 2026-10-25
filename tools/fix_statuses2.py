"""Правка генератора: привести статусы SH и TD точно к белому списку журналов.

Данные получены из ядра (z_agr_statuses) и из схемы стенда:
  SH: draft, on_approval, approved, rejected, cancelled, archived, assigned,
      acknowledged, rework      → лишний objection надо убрать.
  TD: draft, new, in_work, in_progress, on_review, done, approved, rejected,
      cancelled, archived       → добавить подписи new/in_progress,
                                  убрать on_approval/rework (их в TD нет)
      и переписать карту переходов под эти состояния.
"""

from __future__ import annotations

import pathlib

p = pathlib.Path("tools/gen_journal_configs.py")
t = p.read_text(encoding="utf-8")

# 1. SH: убрать objection (в белом списке его нет).
old1 = '''        status_titles=dict(STATUS_TITLES, assigned="Назначено",
                           acknowledged="Ознакомлен",
                           objection="Возражение"),'''
new1 = '''        status_titles=dict(STATUS_TITLES, assigned="Назначено",
                           acknowledged="Ознакомлен"),'''
if old1 in t:
    t = t.replace(old1, new1)
    print("SH: objection убран")

# 2. TD: подписи строго по белому списку журнала.
old2 = '''        status_titles=dict(STATUS_TITLES, in_work="В работе", done="Выполнена",
                           on_review="На проверке"),'''
new2 = '''        # Белый список статусов задач отличается от остальных журналов:
        # в нём нет on_approval и rework, зато есть new и in_progress
        # (проверено вызовом z_agr_statuses('TD') и схемой колонки Status).
        status_titles=[
            'new' => 'Новая',
            'in_work' => 'В работе',
            'in_progress' => 'Выполняется',
            'on_review' => 'На проверке',
            'done' => 'Выполнена',
            'rejected' => 'Отклонена',
            'cancelled' => 'Аннулирована',
            'archived' => 'В архиве',
            'draft' => 'Черновик',
            'approved' => 'Принята',
        ],'''
if old2 in t:
    t = t.replace(old2, new2)
    print("TD: подписи приведены к белому списку")

# 3. TD: карта переходов под фактические состояния.
old3 = '''        transitions=dict(
            BASE_TRANSITIONS,
            in_work=["submit", "update", "cancel"],
            on_review=["approve", "reject", "withdraw"],
            approved=["cancel", "copy", "archive"],
        ),'''
new3 = '''        # Переходы задач описаны по фактическим состояниям журнала:
        # задача не проходит маршрут согласования, её принимает постановщик.
        transitions=[
            'new' => ['update', 'cancel', 'copy'],
            'draft' => ['submit', 'update', 'delete', 'copy', 'cancel'],
            'in_work' => ['submit', 'update', 'cancel'],
            'in_progress' => ['submit', 'update', 'cancel'],
            'on_review' => ['approve', 'reject', 'withdraw'],
            'done' => ['archive', 'copy'],
            'approved' => ['archive', 'copy', 'cancel'],
            'rejected' => ['submit', 'update', 'cancel', 'copy'],
            'cancelled' => ['copy'],
            'archived' => ['copy'],
        ],'''
if old3 in t:
    t = t.replace(old3, new3)
    print("TD: карта переходов приведена к фактическим состояниям")

p.write_text(t, encoding="utf-8", newline="\n")
print("готово")
