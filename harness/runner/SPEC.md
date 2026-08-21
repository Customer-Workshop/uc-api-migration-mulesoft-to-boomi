# Local Boomi process runner — execution semantics

The runner is a deterministic local interpreter for the Boomi process components
under `boomi/components/*.xml`. It stands in for a test Atom so the parity gate
runs fully offline; a real migration deploys the same component definitions to a
test Atom via the AtomSphere Platform API and replays the identical fixtures
against it (see `harness/atomsphere_client.py`).

## Component file format

Components are XML files modeled on the AtomSphere Platform API `bns:Component`
envelope:

```xml
<bns:Component xmlns:bns="http://api.platform.boomi.com/" type="process"
               name="EMP - Get Employee Goals">
  <bns:object>
    <process>
      <route method="GET" path="/api/employee/{employeeId}/goals"/>
      <shapes>
        <shape name="start" shapetype="start" next="auth"/>
        <shape name="auth" shapetype="processcall" process="Common - Validate Token" next="q"/>
        <shape name="q" shapetype="connectoraction" connector="database" operation="select" next="found">
          <sql>SELECT goal FROM employee_goals WHERE employee_id = :employeeId</sql>
          <parameter name="employeeId" kind="pathParam" ref="employeeId"/>
        </shape>
        <shape name="found" shapetype="decision" whenTrue="mk_array" whenFalse="mk_msg">
          <condition operator="notempty">
            <left kind="payload"/>
          </condition>
        </shape>
        ...
        <shape name="done" shapetype="returndocuments" status="200"/>
      </shapes>
    </process>
  </bns:object>
</bns:Component>
```

The XML parses 1:1 into the JSON shape validated by
`boomi/schema/component.schema.json` (`<parameter name=... kind=... ref=...>`
becomes `parameters: {name: {kind, name: ref}}`; `<condition>` children `left`/
`right` carry the same source attributes; `<template>` holds the raw JSON text;
`<property name=... kind=... .../>` children populate `properties`; mapping
children `<mapping to=... kind=... .../>` populate `mappings`, with an optional
nested `<foreach field=.../>` or `<foreach fields="a,b"/>`).

## Engine contract (`harness/runner/engine.py`)

```python
class ProcessEngine:
    def __init__(self, components_dir: Path, conn: psycopg.Connection): ...
    def handle(self, method: str, path: str, headers: dict, body) -> dict:
        """Returns {"status": int, "body": <parsed JSON>}"""
```

- On init: parse every `*.xml` in `components_dir`, validate each against the
  JSON schema (jsonschema library); a schema violation raises immediately
  (fail closed, never skip a bad component).
- `handle` routes by matching `route.method` + `route.path`, where `{param}`
  segments match one path segment and bind path params. No matching component
  → return `{"status": 501, "body": {"error": "not_implemented", "route": "<METHOD PATH>"}}`.
- Execute the shape graph starting at the (single) `start` shape, following
  `next` / `whenTrue` / `whenFalse` until a `returndocuments` shape.
  Guard against cycles (max 100 steps → raise).

### Execution context

- `pathParams`, `queryParams`, `headers` (keys lower-cased), `body` (parsed
  JSON or None) — bound at start.
- `payload` — current document. Starts as the request body; `connectoraction
  select` replaces it with a list of row dicts; `map`/`message` replace it with
  their output.
- `properties` — string/any map set by `properties` shapes and subprocesses.
- `response_status` — set by `returndocuments` `status` attr, or overridable by
  a property named `httpStatus` (integer-coerced) when the attr is absent.

### Source resolution (`kind`)

- `pathParam`/`queryParam`/`header`/`property`: lookup by `name` (headers
  case-insensitive). `header` with name `authorization` supports an optional
  `path` of `bearer` or `basic:username` / `basic:password` to extract
  credentials (Basic per RFC 7617 base64 decode).
- `body`: JSON path into the request body (`$.field` only, one level ok;
  nested `$.a.b` supported).
- `payload`: JSON path into the current payload; `$[0].col` indexes row 0 of a
  result set; bare `payload` kind with no path yields the whole payload.
- `literal`: `value` as-is.
- `rowcount`: number of rows in current payload (0 if not a list).
- `function`: `uuid` → `uuid4().hex`; `now_iso` → current UTC ISO-8601 string;
  `now_plus_seconds` with `args: [n]` → UTC now + n seconds, ISO string
  (used for token expiry; passed to SQL as a timestamp string).

### Shape semantics

- **start** — no-op, binds context (already done); follow `next`.
- **connectoraction** (connector `database` only):
  - `select`: run `sql` with named `:param` binding from `parameters`
    (translate `:name` → psycopg `%(name)s`). Payload becomes
    `[{col: value, ...}, ...]` in cursor order — NEVER re-sort rows.
    Serialize `date`/`datetime` values to ISO strings, `Decimal` to
    int-if-integral else float.
  - `execute`: same binding; payload becomes `{"affectedRows": n}`.
  - SQL errors propagate as exceptions (the gate records them as failures).
- **decision** — evaluate `condition`: resolve `left` (and `right` if the
  operator is binary). `equals`/`notequals` compare after string-coercion of
  scalars; `empty`/`notempty` mean None, "" or length 0; `exists`/`notexists`
  mean the source resolved to a non-None value (resolution errors on the left
  side of exists-checks yield None, not exceptions). Follow `whenTrue`/`whenFalse`.
- **map** — build a new payload from `mappings`. `to: "$"` with a `foreach`
  produces a bare array (e.g. goals: `foreach.field: "goal"` over the current
  result-set payload). Otherwise output is an object; `foreach.fields` maps a
  result set to an array of objects with the listed columns (preserving row
  order and column order as listed). Values resolve via sources; numeric DB
  values stay numeric; everything sourced from pathParam/header stays a string.
- **message** — payload becomes the `template` (parsed JSON) after placeholder
  substitution: `{{pathParam:x}}`, `{{property:x}}`, `{{header:x}}` inside
  string values. If a `status` attr is present, it sets `response_status`.
- **properties** — set each named property from its source; follow `next`.
- **processcall** — run the named subprocess in the SAME context (shared
  properties, payload, request bindings). If the subprocess ends in a
  `returndocuments` with a `halt="true"` attribute, the parent process stops
  and returns the subprocess's response (this is how "Common - Validate Token"
  short-circuits 401s). Otherwise execution continues at the parent's `next`.
- **returndocuments** — finish: response body = current payload,
  status = `status` attr (or `properties["httpStatus"]`, default 200).
  Optional `halt="true"` only meaningful inside subprocesses (see above).

## HTTP wrapper (`harness/runner/server.py`)

A thin FastAPI/uvicorn wrapper exposing the engine on 127.0.0.1:8090
(loopback only) so a converted process can be exercised with curl during the
walkthrough. It shares the engine code path exactly — the wrapper adds no
behavior. It is a namespaced, disposable local fixture: a real deployment adds
authentication hardening, tenant scoping, and CORS policy at the Boomi edge.

## Runner self-tests (`harness/tests/`)

pytest unit tests for the engine semantics above, using tiny inline test
components (NOT the answer key) and a temp Postgres schema. These run on `main`
before any migration work exists — they gate the harness itself, not the
migration.
