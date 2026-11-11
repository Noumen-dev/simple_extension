"""Общий CLI runtime для Python-нод.

Модуль решает две задачи:
1) Единый запуск ноды через `--json` (формат для class-node и function-node).
2) Совместимость с прежними примерами, где использовались `NodeFactory` и `NodeFile`.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Type


_REGISTERED_NODE_CLASS: Type[Any] | None = None
_REGISTERED_NODE_FUNCTION: Callable[[Any], Any] | None = None


@dataclass(slots=True)
class NodeFile:
    """Описание файла, передаваемого между нодами.

    Attributes:
        filename: Имя файла.
        content_type: MIME-тип файла.
        tmp_path: Временный путь к файлу на диске.
    """

    filename: str
    content_type: str
    tmp_path: str


def register_node_class(node_class: Type[Any]) -> Type[Any]:
    """Регистрирует class-node для запуска через CLI."""
    global _REGISTERED_NODE_CLASS
    _REGISTERED_NODE_CLASS = node_class
    return node_class


def register_node_function(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Регистрирует function-node для запуска через CLI."""
    global _REGISTERED_NODE_FUNCTION
    _REGISTERED_NODE_FUNCTION = func
    return func


def _call_callable(maybe_callable: Callable[..., Any], payload_input: Any) -> Any:
    """Вызывает функцию/метод с поддержкой sync/async и разных сигнатур."""
    sig = inspect.signature(maybe_callable)
    params = list(sig.parameters.values())
    has_var_kw = any(p.kind == p.VAR_KEYWORD for p in params)

    def invoke_sync() -> Any:
        if len(params) == 0:
            return maybe_callable()

        if len(params) == 1 and not has_var_kw:
            param_name = params[0].name
            if isinstance(payload_input, dict) and len(payload_input) == 1 and param_name in payload_input:
                return maybe_callable(payload_input[param_name])
            return maybe_callable(payload_input)

        if not isinstance(payload_input, dict):
            raise ValueError("Для вызова с несколькими аргументами `input` должен быть JSON-объектом.")
        return maybe_callable(**payload_input)

    if asyncio.iscoroutinefunction(maybe_callable):
        async def _invoke_async() -> Any:
            if len(params) == 0:
                return await maybe_callable()

            if len(params) == 1 and not has_var_kw:
                param_name = params[0].name
                if isinstance(payload_input, dict) and len(payload_input) == 1 and param_name in payload_input:
                    return await maybe_callable(payload_input[param_name])
                return await maybe_callable(payload_input)

            if not isinstance(payload_input, dict):
                raise ValueError("Для async-вызова с несколькими аргументами `input` должен быть JSON-объектом.")
            return await maybe_callable(**payload_input)

        return asyncio.run(_invoke_async())

    result = invoke_sync()
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _normalize_class_payload(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"params": {}, "input": raw}

    if "params" in raw or "input" in raw:
        return {"params": raw.get("params", {}), "input": raw.get("input", {})}

    return {"params": {}, "input": raw}


def _normalize_function_input(raw: Any) -> Any:
    if isinstance(raw, dict) and "input" in raw:
        return raw["input"]
    return raw


def _load_json_argument(raw_value: str) -> Any:
    """Читает JSON из строки или из файла, если передан префикс `@`.

    Примеры:
        --json '{"input": {...}}'
        --json @payload.json

    Args:
        raw_value: Значение аргумента `--json`.

    Returns:
        Распарсенный JSON-объект.
    """
    if raw_value.startswith("@"):
        file_path = Path(raw_value[1:]).expanduser()
        # `utf-8-sig` корректно читает файлы и с BOM, и без BOM.
        content = file_path.read_text(encoding="utf-8-sig")
        return json.loads(content)
    return json.loads(raw_value)


def run_registered_node_from_cli() -> None:
    """Запускает зарегистрированную class-node через `--json`."""
    try:
        _run_registered_node_from_cli_impl()
    except Exception as e:
        _write_error(str(e))


def _run_registered_node_from_cli_impl() -> None:
    if _REGISTERED_NODE_CLASS is None:
        raise RuntimeError("Класс ноды не зарегистрирован. Используйте декоратор @register_node_class.")

    parser = argparse.ArgumentParser(description="Универсальный запуск Python class-node")
    parser.add_argument(
        "--json",
        required=True,
        help='JSON payload или @путь_к_файлу. Для класса: {"params": {...}, "input": {...}}',
    )
    args = parser.parse_args()

    payload = _normalize_class_payload(_load_json_argument(args.json))
    node_cls = _REGISTERED_NODE_CLASS
    params = payload["params"]
    if isinstance(params, dict):
        try:
            instance = node_cls(**params)
        except TypeError:
            instance = node_cls()
    else:
        instance = node_cls(params)

    if not hasattr(instance, "exec"):
        raise AttributeError(f"Класс {node_cls.__name__} не содержит метод exec().")

    result = _call_callable(instance.exec, payload["input"])
    _write_success(result)


def _write_success(result: Any) -> None:
    """Результат «как есть» в stdout (одна строка JSON); обёртка — в экзекьюторе."""
    if result is None:
        result = {"status": "completed"}
    # ASCII JSON is encoding-independent on Windows consoles and in UTF-8 runners.
    print(json.dumps(result, ensure_ascii=True))


def _write_error(message: str) -> None:
    """При ошибке: stderr — только текст сообщения (без обёртки); экзекьютор оборачивает ответ в формат со статусами."""
    sys.stderr.write(message + "\n")
    sys.exit(1)


def run_registered_function_from_cli() -> None:
    """Запускает зарегистрированную function-node через `--json`."""
    try:
        _run_registered_function_from_cli_impl()
    except Exception as e:
        _write_error(str(e))


def _run_registered_function_from_cli_impl() -> None:
    if _REGISTERED_NODE_FUNCTION is None:
        raise RuntimeError("Функция ноды не зарегистрирована. Используйте @register_node_function.")

    parser = argparse.ArgumentParser(description="Универсальный запуск Python function-node")
    parser.add_argument(
        "--json",
        required=True,
        help='JSON payload или @путь_к_файлу. Для функции: {"params": {...}, "input": {...}}',
    )
    args = parser.parse_args()

    payload_input = _normalize_function_input(_load_json_argument(args.json))
    result = _call_callable(_REGISTERED_NODE_FUNCTION, payload_input)
    _write_success(result)


class NodeFactory:
    """Совместимый helper для локальных примеров запуска нод."""

    @staticmethod
    def exec(node_class: Type[Any], payload: Dict[str, Any] | None = None) -> Any:
        """Выполняет class-node без CLI.

        Args:
            node_class: Класс ноды.
            payload: Словарь формата {"params": {...}, "input": {...}} или короткий формат.
        """
        normalized = _normalize_class_payload(payload or {})
        params = normalized.get("params", {})
        if isinstance(params, dict):
            try:
                instance = node_class(**params)
            except TypeError:
                instance = node_class()
        else:
            instance = node_class(params)

        if not hasattr(instance, "exec"):
            raise AttributeError(f"Класс {node_class.__name__} не содержит метод exec().")

        return _call_callable(instance.exec, normalized.get("input", {}))
