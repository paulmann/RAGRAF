---
description: Показать состояние индекса RAGRAF для текущего проекта
allowed-tools: mcp__plugin_ragraf_ragraf__ragraf_context, mcp__plugin_ragraf_ragraf__ragraf_index_status, mcp__plugin_ragraf_ragraf__ragraf_catalog
---

Покажи состояние индекса RAGRAF — коротко и числами, без догадок.

1. Вызови `mcp__plugin_ragraf_ragraf__ragraf_context`: какой корпус выбран и на
   каком основании.
2. Вызови `mcp__plugin_ragraf_ragraf__ragraf_index_status`: ревизия, число
   файлов/узлов/рёбер, свежесть, `capability`, есть ли отставшие факты.
3. Выведи результат таблицей:

| Что | Значение |
|---|---|
| корпус | … |
| ревизия | … |
| файлы / узлы / рёбра | … |
| свежесть | актуален / отстал (назвать причину) |
| режим | full / degraded (почему) |

Если индекс отстал или `capability=degraded` — скажи прямо, что фактам нельзя
верить до `mcp__plugin_ragraf_ragraf__ragraf_ensure`, и предложи выполнить его
сейчас. Если корпус не выбран — покажи
`mcp__plugin_ragraf_ragraf__ragraf_catalog` и предложи
`mcp__plugin_ragraf_ragraf__ragraf_use_project` с именем разработки.

Ничего не переиндексируй без ответа пользователя: `ensure` может идти минутами.
