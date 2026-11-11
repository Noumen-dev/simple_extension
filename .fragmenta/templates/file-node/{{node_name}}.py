import os
import shutil
import tempfile

from cli_runtime import register_node_class, run_registered_node_from_cli


@register_node_class
class {{NodeName}}:
    META = {
        "name": "{{NodeName}}",
        "display_name": "{{displayName}}",
        "description": "{{description}}",
        "icon": "file",
        "package": "caia_core",
        "version": "1.0.0",
        "deprecated": False,
        "tags": ["File"],
        "category": "File",
    }

    def __init__(self, **kwargs):
        self._config = kwargs

    def exec(self, file=None) -> dict:
        if file is None:
            raise ValueError("No input file provided")

        tmp_path = file.get("tmp_path")
        if not tmp_path or not os.path.isfile(tmp_path):
            raise ValueError(f"Invalid file tmp_path: {tmp_path}")

        suffix = os.path.splitext(file.get("filename", ""))[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_out:
            out_path = tmp_out.name
            shutil.copy2(tmp_path, out_path)

        return {
            "file": {
                "tmp_path": out_path,
                "filename": file.get("filename", "output.bin"),
                "content_type": file.get("content_type", "application/octet-stream"),
            }
        }


if __name__ == "__main__":
    run_registered_node_from_cli()
