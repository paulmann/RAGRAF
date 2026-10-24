"""Проверка: как грамматика C# называет записи (record) и структуры.

Проверка C#-корпуса показала: `public sealed record AlertRule(...)` определён
в индексе как `struct`. Это подозрительно: `record` без слова `struct` — это
ССЫЛОЧНЫЙ тип (record class), а не значимый. Если профиль кладёт все записи
в структуры, значит он не различает record class и record struct.

Проверяется на реальном коде: какие имена узлов даёт грамматика для
   record class  (ссылочный тип)
   record struct (значимый тип)
   class, struct, interface, enum
"""

from __future__ import annotations

import pathlib
import sys

from tree_sitter_language_pack import get_parser  # type: ignore

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
PROBE = REPO / ".staging" / "csharp_probe.cs"
PROBE.write_text(
    "using System;\n"
    "\n"
    "namespace Demo;\n"
    "\n"
    "// Ссылочный тип\n"
    "public sealed record AlertRule(string RuleId, int Severity);\n"
    "\n"
    "// Тоже ссылочный: с телом\n"
    "public record Person\n"
    "{\n"
    "    public string Name { get; init; } = \"\";\n"
    "}\n"
    "\n"
    "// Значимый тип\n"
    "public readonly record struct Point(int X, int Y);\n"
    "\n"
    "public class Service : IDisposable\n"
    "{\n"
    "    public void Dispose() { }\n"
    "    public int Value { get; set; }\n"
    "}\n"
    "\n"
    "public struct Vector { public int X; }\n"
    "\n"
    "public interface IThing { void Do(); }\n"
    "\n"
    "public enum Severity { Low, High }\n",
    encoding="utf-8", newline="\n",
)

parser = get_parser("csharp")
tree = parser.parse(PROBE.read_bytes())

print("=" * 78)
print("ТИПЫ УЗЛОВ ГРАММАТИКИ C# ДЛЯ ОБЪЯВЛЕНИЙ")
print("=" * 78)
print(f"\n  {'тип узла':<42} {'строка':>7}  текст")
print("  " + "-" * 74)

seen: dict[str, list[str]] = {}
data = PROBE.read_bytes()


def walk(node, depth: int = 0) -> None:
    text = data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    first_line = text.split("\n", 1)[0][:44]
    decl_types = {
        "class_declaration", "struct_declaration", "record_declaration",
        "interface_declaration", "enum_declaration", "method_declaration",
        "property_declaration", "field_declaration", "namespace_declaration",
        "file_scoped_namespace_declaration", "constructor_declaration",
    }
    if node.type in decl_types:
        seen.setdefault(node.type, []).append(first_line)
        print(f"  {node.type:<42} {node.start_point[0] + 1:>7}  {first_line}")
    for child in node.children:
        walk(child, depth + 1)


walk(tree.root_node)

print()
print("=" * 78)
print("СВОДКА: сколько раз встретился каждый тип объявления")
print("=" * 78)
for node_type, items in sorted(seen.items(), key=lambda kv: -len(kv[1])):
    print(f"  {node_type:<42} {len(items)}")

print()
print("=" * 78)
print("ЧТО ЭТО ЗНАЧИТ ДЛЯ ПРОФИЛЯ")
print("=" * 78)
print("""
  Ключевой вопрос: различает ли грамматика record class и record struct.

  Если узел для обоих называется record_declaration, то по одному имени узла
  вид не определить — нужно смотреть модификаторы внутри узла (наличие слова
  struct). Тогда в профиле запись нельзя однозначно отнести к classes или
  structs, и честнее назвать её отдельным видом.

  Проверка ниже показывает, что именно лежит внутри record_declaration.
""")

# Разбирается содержимое узлов record_declaration: есть ли слово struct.
print("  Содержимое узлов record_declaration:")
for node in [n for n in _all_nodes(tree.root_node)
             if n.type == "record_declaration"]:
    text = data[node.start_byte : node.end_byte].decode("utf-8", "replace")
    head = text.split("{", 1)[0].split("\n", 1)[0].strip()
    has_struct = " struct " in f" {head} " or head.startswith("public struct")
    print(f"    «{head[:60]}»")
    print(f"      тип: {'ЗНАЧИМЫЙ (struct)' if has_struct else 'ССЫЛОЧНЫЙ (class)'}")
