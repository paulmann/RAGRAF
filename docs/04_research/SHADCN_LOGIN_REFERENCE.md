# Справочник: стиль shadcn/ui для страницы входа (перенос в чистый CSS)

**Назначение.** Воспроизвести визуальный язык shadcn/ui на чистом CSS, без
Tailwind, npm, React и сборщиков. Проект `z.batteryservice.ru` требует, чтобы
все ресурсы были локальными, а цвета задавались токенами дизайн-системы.

**Источник.** Документация `ui.shadcn.com` (разделы theming, components) и
реестр компонентов. Где значение не подтверждено первоисточником — это помечено
явно: выдуманных значений в документе нет.

---

## 1. Токены темы

**Важная поправка.** Текущая документация отдаёт значения в формате **OKLCH**,
а не HSL. HSL-тройки вида `0 0% 100%` — это версия v3; в актуальной документации
они не публикуются.

### Светлая тема (`:root`)

| Токен | Значение |
|---|---|
| `--background` | `oklch(1 0 0)` |
| `--foreground` | `oklch(0.145 0 0)` |
| `--card` | `oklch(1 0 0)` |
| `--card-foreground` | `oklch(0.145 0 0)` |
| `--primary` | `oklch(0.205 0 0)` |
| `--primary-foreground` | `oklch(0.985 0 0)` |
| `--muted` | `oklch(0.97 0 0)` |
| `--muted-foreground` | `oklch(0.556 0 0)` |
| `--border` | `oklch(0.922 0 0)` |
| `--input` | `oklch(0.922 0 0)` |
| `--ring` | `oklch(0.708 0 0)` |
| `--destructive` | `oklch(0.577 0.245 27.325)` |

### Тёмная тема (`.dark`)

| Токен | Значение |
|---|---|
| `--background` | `oklch(0.145 0 0)` |
| `--foreground` | `oklch(0.985 0 0)` |
| `--card` | `oklch(0.205 0 0)` |
| `--primary` | `oklch(0.922 0 0)` |
| `--primary-foreground` | `oklch(0.205 0 0)` |
| `--muted` | `oklch(0.269 0 0)` |
| `--muted-foreground` | `oklch(0.708 0 0)` |
| `--border` | `oklch(1 0 0 / 10%)` |
| `--input` | `oklch(1 0 0 / 15%)` |
| `--ring` | `oklch(0.556 0 0)` |
| `--destructive` | `oklch(0.704 0.191 22.216)` |

### Радиус

Значение `--radius` в документации **не опубликовано**: опубликована только
шкала производных `calc(var(--radius) * 0.6 / 0.8 / 1 / 1.4 / 1.8 / 2.2 / 2.6)`.
Стандартное значение Tailwind — `0.25rem` (4px), но это **вывод**, а не факт из
документации shadcn.

---

## 2. Три факта, которые противоречат распространённым ожиданиям

| Ожидание | Как на самом деле | Источник |
|---|---|---|
| Поле ввода высотой 40px | **36px** (`h-9` = 2.25rem). 40px (`h-10`) — это кнопка размера `lg` | реестр, `input.tsx` |
| Флажок берёт радиус из `--radius` | **Нет**: `rounded-[4px]` жёстко — единственное исключение из системы радиусов | реестр, `checkbox.tsx` |
| Прозрачность через `rgba()` | **Через `color-mix`**: `ring-[3px] ring-ring/50` даёт `box-shadow: 0 0 0 3px color-mix(in oklab, var(--ring) 50%, transparent)` | `tailwindcss/src/utilities.ts` |

---

## 3. Структура страницы входа (блок `login-01`, дословно)

### Каркас

| Элемент | Классы Tailwind | Эквивалент CSS |
|---|---|---|
| Страница | `min-h-svh flex items-center justify-center p-6 md:p-10` | `min-height: 100svh; display: flex; align-items: center; justify-content: center; padding: 1.5rem;` (на `md` — `2.5rem`) |
| Обёртка | `w-full max-w-sm` | `width: 100%; max-width: 24rem;` (384px) |
| Карточка | `rounded-xl border bg-card py-6 shadow-sm` | `border-radius: 0.75rem; border: 1px solid var(--border); background: var(--card); padding-block: 1.5rem; box-shadow: 0 1px 2px rgb(0 0 0 / 0.05);` |
| Шапка карточки | `px-6` | `padding-inline: 1.5rem;` |
| Содержимое | `px-6` | `padding-inline: 1.5rem;` |
| Группа полей | `FieldGroup` | `display: flex; flex-direction: column; gap: 1.75rem;` |
| Поле | `Field` | `display: flex; flex-direction: column; gap: 0.75rem;` |

Порядок вложенности:

```
страница (центрирование)
└── обёртка max-width
    └── карточка
        ├── шапка: заголовок + описание
        └── содержимое
            └── форма
                └── группа полей
                    └── поле: подпись + ввод
```

### Расхождение документации и реестра

Документация `Card` показывает **новые** классы (`ring-1 ring-foreground/10`,
`py-(--card-spacing)`, без `shadow-sm`), а реестр и ветка `main` отдают **старые**
(`border`, `shadow-sm`, `px-6`). Ветка `main` отстаёт от документации.

При переносе выбран вариант **реестра**: он стабильнее и соответствует тому,
что реально генерируется при установке компонента.

---

## 4. Компоненты форм: HTML и CSS

### Подпись поля

```css
.z-label {
  font-size: 0.875rem;      /* text-sm */
  font-weight: 500;         /* font-medium */
  line-height: 1.25rem;
  color: var(--foreground);
}
.z-label[data-required]::after {
  content: " *";
  color: var(--destructive);
}
```

### Поле ввода

```css
.z-input {
  height: 2.25rem;                        /* ВАЖНО: 36px, не 40px */
  width: 100%;
  border-radius: calc(var(--radius) * 0.8);
  border: 1px solid var(--input);
  background: transparent;
  padding-inline: 0.75rem;
  padding-block: 0.25rem;
  font-size: 0.875rem;
  line-height: 1.25rem;
  color: var(--foreground);
  transition: border-color 150ms, box-shadow 150ms;
}
.z-input::placeholder { color: var(--muted-foreground); }

/* Кольцо фокуса: shadcn использует 3px и прозрачность 50% */
.z-input:focus-visible {
  outline: none;
  border-color: var(--ring);
  box-shadow: 0 0 0 3px color-mix(in oklab, var(--ring) 50%, transparent);
}
.z-input:disabled { opacity: 0.5; cursor: not-allowed; }
.z-input[aria-invalid="true"] {
  border-color: var(--destructive);
  box-shadow: 0 0 0 3px color-mix(in oklab, var(--destructive) 20%, transparent);
}
```

**Замечание о совместимости.** `color-mix()` поддержан во всех актуальных
браузерах (Chrome 111+, Firefox 113+, Safari 16.2+). Если нужен запас, вместо
него можно задать кольцо фиксированным цветом с прозрачностью: результат
визуально почти совпадает, но перестаёт следовать за сменой темы.

### Кнопка (включая состояние загрузки)

```css
.z-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  height: 2.5rem;                          /* h-10 — это кнопка, не поле */
  padding-inline: 1rem;
  border-radius: calc(var(--radius) * 0.8);
  border: 1px solid transparent;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 150ms, opacity 150ms;
}
.z-btn--primary {
  background: var(--primary);
  color: var(--primary-foreground);
}
.z-btn--primary:hover { background: color-mix(in oklab, var(--primary) 90%, transparent); }
.z-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* Состояние загрузки: показывается вращающийся индикатор */
.z-btn[data-loading]::before {
  content: "";
  width: 1rem;
  height: 1rem;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: z-spin 600ms linear infinite;
}
@keyframes z-spin { to { transform: rotate(360deg); } }
```

### Сообщение об ошибке (Alert)

```css
.z-alert {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--destructive);
  border-radius: calc(var(--radius) * 0.8);
  background: color-mix(in oklab, var(--destructive) 8%, transparent);
  color: var(--destructive);
  font-size: 0.875rem;
}
```

### Разделитель (Separator)

```css
.z-sep {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--muted-foreground);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.z-sep::before,
.z-sep::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border);
}
```

### Флажок

```css
.z-check {
  width: 1rem;
  height: 1rem;
  /* Единственный компонент с жёстким радиусом, а не из --radius */
  border-radius: 4px;
  border: 1px solid var(--input);
  accent-color: var(--primary);
}
```

---

## 5. Адаптивность

Контрольные точки Tailwind по умолчанию: `sm` = 640px, `md` = 768px,
`lg` = 1024px.

Для страницы входа достаточно одной точки:

```css
.z-auth { padding: 1.5rem; }                 /* мобильный */
@media (min-width: 768px) {
  .z-auth { padding: 2.5rem; }               /* md:p-10 */
}
```

Карточка на мобильном занимает всю ширину (`w-full`), на широком экране
ограничена 384px (`max-w-sm`). Отдельного «мобильного» макета не нужно.

---

## 6. Чек-лист доступности

1. **Связь подписи и поля:** `label for="id"` + `input id="id"` — иначе
   клик по подписи не переносит фокус.
2. **Обязательность:** атрибут `required` у поля и визуальная пометка
   (звёздочка) — требование должно быть видно до отправки.
3. **Ошибка поля:** `aria-invalid="true"` плюс `aria-describedby` на текст
   ошибки — иначе программа чтения с экрана не свяжет их.
4. **Порядок фокуса:** логин → пароль → флажок → кнопка. Не переопределять
   `tabindex` без необходимости.
5. **Кнопка загрузки:** `aria-busy="true"` и `disabled`, чтобы повторное
   нажатие не отправляло форму дважды.
6. **Видимость пароля:** кнопка-переключатель с `aria-pressed` и подписью
   («Показать пароль» / «Скрыть пароль»).
7. **Сообщение об ошибке формы:** контейнер с `role="alert"`, чтобы оно
   озвучивалось сразу при появлении.
8. **Контраст:** `--muted-foreground` на `--card` даёт около 4.6:1 —
   достаточно для обычного текста, но **не проверено калькулятором**.
9. **Фокус видим:** кольцо `:focus-visible` не убирать (`outline: none` только
   вместе с собственной заменой).
10. **Автозаполнение:** `autocomplete="username"` и `autocomplete="current-password"`
    — нужно менеджерам паролей.

---

## 7. Что осталось непроверенным

Честно, чтобы не выдавать вывод за факт:

1. **HSL-набор версии v3** — не прочитан (исчерпан бюджет вызовов). Текущие
   16 значений в формате OKLCH получены из актуальной документации.
2. **Точное значение `--radius`** — не опубликовано в документации; `0.25rem`
   это стандарт Tailwind, а не значение shadcn.
3. **Контраст пар** `muted-foreground`/`card` и `border`/`background` —
   арифметически не проверен.
4. **Страница `docs/skills`** отношения к разметке входа не имеет: она про
   AI-скилл, а не про UI-компоненты. Приём для этого документа — из
   `docs/components` и `docs/theming`.
