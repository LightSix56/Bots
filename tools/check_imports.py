"""Разовый статический контроль: неиспользуемые импорты и мёртвый код.

Запускается вручную, в CI не входит.
"""

import ast
import pathlib
import sys

TARGETS = ["bot.py", "news.py", "weather_bot.py", "tg.py", "config.py", "storage.py", "console.py"]

# __future__ — директива компилятора, а не имя в коде.
IGNORED = {"annotations"}


def imported_names(tree):
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[(alias.asname or alias.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names[alias.asname or alias.name] = node.lineno
    return names


def used_names(tree):
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                used.add(node.value.id)
    return used


def is_intentional(source, line_number):
    """Импорт ради побочного эффекта помечается комментарием noqa."""
    lines = source.splitlines()
    if 0 < line_number <= len(lines):
        return "noqa" in lines[line_number - 1]
    return False


def main():
    problems = 0
    for target in TARGETS:
        path = pathlib.Path(target)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        used = used_names(tree)
        for name, line in sorted(imported_names(tree).items(), key=lambda item: item[1]):
            if name in used or name in IGNORED:
                continue
            if f'"{name}"' in source or f"'{name}'" in source:
                continue
            if is_intentional(source, line):
                continue
            print(f"{target}:{line}: неиспользуемый импорт: {name}")
            problems += 1
    print(f"Проверено файлов: {len(TARGETS)}, замечаний: {problems}")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
