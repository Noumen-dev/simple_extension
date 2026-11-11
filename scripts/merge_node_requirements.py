#!/usr/bin/env python3
"""
Скрипт объединения зависимостей узлов (nodes) в один файл requirements.

Обходит nodes/*/requirements.txt, дедуплицирует пакеты и записывает результат в
nodes/requirements-install.txt. Если есть nodes/requirements-base.txt — его строки
подмешиваются первыми. Используется при сборке Docker и make python-venv.
"""

import re
import json
import sys
from pathlib import Path


def _normalize_package_name(line: str) -> str:
    """
    Извлекает каноническое имя пакета из строки зависимости (PEP 508).

    Примеры:
        "requests>=2.25.0" -> "requests"
        "dlt[clickhouse,postgres]>=1.18" -> "dlt"
        "PyYAML>=6.0.2" -> "pyyaml"  # pip нормализует к нижнему регистру

    Args:
        line: Строка спецификации пакета (без комментариев и пробелов по краям).

    Returns:
        Имя пакета в нижнем регистре для дедупликации.
    """
    # Убираем экстра-часть [extra1,extra2]
    name_part = line.split("[")[0].strip()
    # Убираем версию: всё после ==, >=, <=, <, >, ~=, !=
    name_part = re.split(r"\s*(?:==|>=|<=|!=|~=|[<>])\s*", name_part, maxsplit=1)[0]
    return name_part.strip().lower()


def _strip_comment(line: str) -> str:
    """Удаляет комментарий из строки (включая ведущие пробелы перед #)."""
    if "#" in line:
        return line[: line.index("#")].strip()
    return line.strip()


def _collect_from_file(path: Path, seen: set[str], result: list[tuple[str, str]]) -> None:
    """Добавляет строки из одного requirements-файла в result (с дедупликацией)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = _strip_comment(raw_line)
            if not line or line.startswith("-"):
                continue
            name = _normalize_package_name(line)
            if name in seen:
                continue
            seen.add(name)
            result.append((name, line))


def collect_requirements(nodes_dir: Path) -> list[tuple[str, str]]:
    """
    Собирает зависимости: опционально nodes/requirements-base.txt, затем
    все nodes/*/requirements.txt. Дедупликация по имени пакета.

    Args:
        nodes_dir: Путь к директории nodes.

    Returns:
        Список пар (каноническое_имя_пакета, исходная_строка).
    """
    seen: set[str] = set()
    result: list[tuple[str, str]] = []

    # Опциональный общий файл в корне nodes
    base_file = nodes_dir / "requirements-base.txt"
    if base_file.is_file():
        _collect_from_file(base_file, seen, result)

    # Рекурсивно ищем все requirements.txt в поддиректориях nodes (не в корне)
    index_path = nodes_dir.parent / "index.json"
    active = set(json.loads(index_path.read_text(encoding="utf-8"))) if index_path.exists() else None
    for req_file in sorted(nodes_dir.rglob("requirements.txt")):
        if req_file.parent == nodes_dir:
            continue  # не включаем nodes/requirements.txt, если бы был
        if active is not None and req_file.relative_to(nodes_dir).parts[0] not in active:
            continue
        # Пропускаем файлы не в непосредственных поддиректориях nodes (например nodes/extractor/scripts/ если бы был)
        try:
            req_file.resolve().relative_to(nodes_dir.resolve())
        except ValueError:
            continue
        _collect_from_file(req_file, seen, result)

    return result


def main() -> int:
    """Точка входа: объединяет requirements и записывает в выходной файл."""
    script_dir = Path(__file__).resolve().parent
    # Скрипт лежит в golang_factory/scripts/, nodes — golang_factory/nodes/
    base_dir = script_dir.parent
    nodes_dir = base_dir / "nodes"
    output_path = base_dir / "nodes" / "requirements-install.txt"

    if not nodes_dir.is_dir():
        print(f"Ошибка: директория узлов не найдена: {nodes_dir}", file=sys.stderr)
        return 1

    pairs = collect_requirements(nodes_dir)
    lines = [spec for _name, spec in pairs]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        if lines:
            f.write("\n")

    print(f"Записано {len(lines)} зависимостей в {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
