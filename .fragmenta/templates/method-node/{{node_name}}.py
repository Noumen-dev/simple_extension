from typing import Any, Dict, List

from cli_runtime import register_node_class, run_registered_node_from_cli


@register_node_class
class {{NodeName}}:
    META = {
        "name": "{{NodeName}}",
        "display_name": "{{displayName}}",
        "description": "{{description}}",
        "icon": "box",
        "package": "caia_core",
        "version": "1.0.0",
        "deprecated": False,
        "tags": ["{{tag}}"],
        "category": "{{category}}",
    }

    NODE_METHODS = ["process"]

    def __init__(self, method: str | None = None, timeout: float = 10.0):
        self.method = method
        self.timeout = timeout

    def process(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"result": {"count": len(items), "items": items}}

    def exec(self, task: dict) -> dict:
        method = task.get("method") or self.method or "process"
        if method not in self.NODE_METHODS:
            raise ValueError(f"Метод {method} не поддерживается")
        return getattr(self, method)(**{k: v for k, v in task.items() if k != "method"})


if __name__ == "__main__":
    run_registered_node_from_cli()
