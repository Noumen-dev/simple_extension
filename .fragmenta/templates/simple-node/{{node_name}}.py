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

    def __init__(self, **kwargs):
        self._config = kwargs

    def exec(self, text: str) -> dict:
        return {"result": text}


if __name__ == "__main__":
    run_registered_node_from_cli()
