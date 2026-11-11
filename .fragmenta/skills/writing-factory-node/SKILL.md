---
name: writing-factory-node
description: Use when adding a new Python node under golang_factory/nodes using a kebab-case folder name. Covers required schema.json fields, port connection rules, Python class layout, and registration so the node appears in the Noumen browser panel and runs in the workflow.
---

# Writing a Factory Node

## Overview

Each node lives in `golang_factory/nodes/<kebab-name>/` and consists of **two files**:

- `schema.json` — UI/runtime contract (ports, parameters, metadata). Read by the orchestrator extension to populate the browser panel.
- `<snake_name>_node.py` — Python class with `@register_node_class` and an `exec` (or method) entry point. Executed by the factory at runtime via `cli_runtime.run_registered_node_from_cli`.

Both files are required. Missing or malformed `schema.json` → node won't appear in the panel. Missing `@register_node_class` → factory raises `class XxxNode is not registered` on Run.

## The Iron Rules

1. `schema.json` **must** contain top-level keys: `type`, `kind: "class"`, `metadata`, `ports`, `ui`, `data`, `code`. Any missing → node is invisible in the panel.
2. `data.type` **must** equal the top-level `type` and match the Python class name exactly.
3. `code.path` **must** match the actual `.py` filename in the same folder.
4. The Python class **must** be decorated with `@register_node_class` and **must** define `META` with at least: `name`, `display_name`, `description`, `package`, `version`, `tags`, `category`.
5. `__main__` **must** call `run_registered_node_from_cli()` — that's how the factory invokes it.
6. Every signal and data port **must** define the standard `connectionRules` profile so it supports fan-in/fan-out and connects predictably to other nodes.

## Templates

Start from a template in `.noumen/templates/` — copy the folder, rename files, replace placeholders.

| Template | Use when |
|----------|----------|
| `simple-node/` | Single `exec(...)` method, scalar/object I/O (text → text, object → object). |
| `file-node/` | Inputs/outputs include a `file` port (`dataType: "file"`). File objects are `{tmp_path, filename, content_type}` dicts. |
| `method-node/` | Multiple methods exposed via a `method` enum parameter with `effects.parameters.method.<value>.ports` overrides per method. |

### Placeholders

| Placeholder | Example | Where it goes |
|-------------|---------|---------------|
| `{{NodeName}}` | `MyAwesomeNode` | Python class name, `type` and `data.type` in schema |
| `{{node_name}}` | `my_awesome_node` | `.py` filename and `code.path` |
| `{{displayName}}` | `My Awesome Node` | `metadata.displayName` (shown in panel) |
| `{{description}}` | `Does the awesome thing` | `metadata.description` (shown in the IDE catalog), `data.description` and `META.description` |
| `{{category}}` | `Data` / `File` / `Logic` / `Function` / `Text` | `data.category` and `META.category` — groups the node in the panel sidebar |
| `{{tag}}` | `Data` | `data.tags[0]` and `META.tags[0]` |

Folder name on disk uses kebab-case (`my-awesome-node/`), Python filename uses snake_case (`my_awesome_node.py`), class uses PascalCase (`MyAwesomeNode`). Pick one base name and stick to those three derivatives.

## Step-by-step

1. **Pick a template** matching your I/O shape.
2. **Copy** `.noumen/templates/<template>/` → `golang_factory/nodes/<kebab-name>/`. Rename `{{node_name}}.py` to `<snake_name>_node.py`.
3. **Replace all `{{...}}` placeholders** in both files (schema.json and the .py). Search for `{{` to confirm none are left.
4. **Update `code.path`** in schema.json to the actual filename.
5. **Edit ports** in schema.json (`ports.inputs` / `ports.outputs`) to match your real inputs/outputs:
   - Always keep `{type: "signal", key: "execute"}` as the first input and `{type: "signal", key: "next"}` as the first output.
   - Data ports: `{type: "data", key, displayName, dataType, required?, defaultValue?, description?}`.
   - `dataType`: `string` | `number` | `boolean` | `array` | `object` | `file` | `any`.
   - Add the standard `connectionRules` block below to every signal and data port, including ports under `effects.parameters`.
6. **Implement the logic** in the Python class:
   - For `simple-node`: edit `exec(self, ...)` — argument names must match the input port `key`s (minus `execute`). Return a dict whose keys match the output port `key`s (minus `next`).
   - For `method-node`: add methods, list them in `NODE_METHODS`, extend the `method` enum in schema.json's `parameters` and add a matching entry under `effects.parameters.method.<value>.ports`.
7. **Mirror to `golang_factory_unix/nodes/<kebab-name>/`** if a unix-build node folder exists at the same level — both trees ship the same node set.
8. **Restart the factory** (Build in the orchestrator UI). Verify the node appears in the panel under your `category`.

## Value Schemas, Forms and Expressions

The IDE draws the right panel and suggests `{{Node Name.output.path}}` expressions from `schema`. One field, `schema`, describes the value shape for data ports and parameters.

- **Short form** mirrors the value: `{ "id": "string", "tags": ["string"], "owner": { "name": "string" } }`. Leaves: `string`, `number`, `boolean`, `date`, `file`, `any`; depth ≤ 8.
- **Long form** is a JSON Schema subset: `type` (`integer` too), `properties`, `items`, `required`, `title`, `description`, `default`, `enum` or `oneOf: [{ "const", "title" }]`, `minimum`/`maximum`, `"format": "date"`, and for form fields `visibleWhen` / `disabledWhen` / `requiredWhen` (`mode == 'password'`) and `dependsOn`. Any other key silently breaks long-form detection.
- **Object/array inputs and parameters** with a fixed set of keys: add a long-form `schema` — the right panel becomes a form with a link button per field. A parameter with `schema` needs no `control`.
- **Do not use nested `parameters`** — they are deprecated. Describe the object with `schema` and put field defaults into `default`.
- **Outputs**: add a short-form `schema` so suggestions go deep. An object schema is closed — every key the code returns must be listed; use `"any"` for variable parts. Free-form values (rows, arbitrary JSON, header maps) get no schema; never `{}`.
- **Descriptions** (`metadata.description`, port/parameter/field `description`) are short, user-facing Russian phrases. Implementation notes go to the node README.

Example — credentials as a form:

```json
"credentials": {
  "displayName": "Credentials",
  "description": "SMTP-сервер и ящик отправителя",
  "required": true,
  "schema": {
    "type": "object",
    "required": ["host", "sender", "password"],
    "properties": {
      "host": { "type": "string", "title": "Host" },
      "port": { "type": "integer", "title": "Port", "minimum": 1, "maximum": 65535, "default": 465 },
      "sender": { "type": "string", "title": "Sender" },
      "password": { "type": "string", "title": "Password" }
    }
  }
}
```

The value the node receives does not change: the form writes the same object; once any field is linked, the orchestrator turns the JSON template into an object with `{{nodeId.path}}` strings and the runtime resolves them.

## Port Connection Rules

Use this profile on every input and output port:

```json
"connectionRules": {
  "requireSameType": true,
  "requireSameDataType": false,
  "allowMultipleOutputs": true,
  "allowMultipleInputs": true
}
```

- Require the same port type: signal connects to signal and data connects to data.
- Do not require the same `dataType`; runtime conversion or validation may handle it.
- Allow fan-in and fan-out by setting both multiplicity flags to `true`.
- Set all four fields explicitly instead of relying on direction-dependent defaults.
- Repeat the complete block inside every `effects.parameters.<parameter>.<value>.ports` variant because an effect replaces the active ports.

## Categories & Icons

`category` groups the node in the browser panel sidebar. Use an existing one if it fits — `Data`, `File`, `Logic`, `Function`, `Text`, `HTTP`, `Flow`. New categories are allowed but create their own sidebar group.

`metadata.icon` accepts a lucide-style PascalCase name (`Box`, `File`, `Filter`, `Globe`, `Timer`). `META.icon` in Python is informational only (lowercase, free-form).

## Verification Checklist

Before declaring done:

- [ ] `grep -r '{{' golang_factory/nodes/<kebab-name>/` returns nothing.
- [ ] `data.type` === top-level `type` === Python class name.
- [ ] `code.path` === actual `.py` filename.
- [ ] Python file imports `register_node_class, run_registered_node_from_cli` from `cli_runtime` and uses both.
- [ ] `META` dict present with all six required keys.
- [ ] `metadata.description` is set; object/array ports and parameters with fixed keys have `schema`; no nested `parameters`.
- [ ] Every top-level and effect-generated port has the standard four-field `connectionRules` block.
- [ ] One output can connect to multiple downstream inputs, and one input accepts multiple upstream outputs.
- [ ] Restart factory → node visible in panel under expected category.
- [ ] Drag onto canvas, connect signals, run a workflow → no `not registered` error.

## Common Failure Modes

| Symptom | Cause |
|---------|-------|
| Node missing from panel | `schema.json` missing one of `kind`/`metadata`/`ui`/`data` — orchestrator skips it silently. |
| `class XxxNode is not registered` on Run | Python file not decorated with `@register_node_class`, or factory image was built before the node was added (rebuild). |
| Node appears but inputs/outputs aren't wired | Port `key`s in schema don't match argument names in `exec` / method, or return-dict keys don't match output port `key`s. |
| Method picker doesn't switch ports | Missing `effects.parameters.method.<value>.ports` entry for that method value. |
| File input errors with "Invalid tmp_path" | Caller passed something other than a `{tmp_path, filename, content_type}` dict — verify upstream node emits the right shape. |

## Reference Nodes

Working examples in `golang_factory/nodes/`:

- `echo/` — simplest possible class node (`simple-node` shape).
- `file-echo/` — file in → file out (`file-node` shape).
- `filter/` — methods + parameters + effects (`method-node` shape).
- `http-request/` — large enum with per-method port overrides.

When in doubt, copy from the closest reference and diff against the template.
