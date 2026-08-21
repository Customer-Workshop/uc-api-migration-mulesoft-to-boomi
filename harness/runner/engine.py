"""Deterministic interpreter for the local Boomi component subset."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema

from .xml_loader import load_component


_PARAMETER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z]+):([^}]+)\}\}")


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    return value


class ProcessEngine:
    def __init__(self, components_dir: Path, conn):
        self.components_dir = Path(components_dir)
        self.conn = conn
        schema_path = self.components_dir.parent / "schema" / "component.schema.json"
        schema = json.loads(schema_path.read_text())
        self.components: dict[str, dict[str, Any]] = {}
        self.halt_shapes: dict[str, set[str]] = {}
        for path in sorted(self.components_dir.glob("*.xml")):
            component, halt = load_component(path)
            jsonschema.validate(component, schema)
            if component["name"] in self.components:
                raise ValueError(f"duplicate component name: {component['name']}")
            self.components[component["name"]] = component
            self.halt_shapes[component["name"]] = set(halt)
        self.routes = [
            (component["route"]["method"], component["route"]["path"], component)
            for component in self.components.values()
            if "route" in component
        ]

    def handle(self, method: str, path: str, headers: dict, body) -> dict:
        parsed = urllib.parse.urlsplit(path)
        method = method.upper()
        for route_method, route_path, component in self.routes:
            params = self._match(route_method, route_path, method, parsed.path)
            if params is not None:
                context = {
                    "pathParams": params,
                    "queryParams": {
                        key: values[-1] if len(values) == 1 else values
                        for key, values in urllib.parse.parse_qs(parsed.query).items()
                    },
                    "headers": {str(k).lower(): v for k, v in (headers or {}).items()},
                    "body": body,
                    "payload": body,
                    "properties": {},
                    "response_status": None,
                }
                return self._run(component, context, [0])
        return {"status": 501, "body": {"error": "not_implemented", "route": f"{method} {parsed.path}"}}

    @staticmethod
    def _match(route_method: str, route_path: str, method: str, path: str) -> dict[str, str] | None:
        if route_method != method:
            return None
        route_parts = route_path.strip("/").split("/") if route_path != "/" else []
        path_parts = path.strip("/").split("/") if path != "/" else []
        if len(route_parts) != len(path_parts):
            return None
        params: dict[str, str] = {}
        for expected, actual in zip(route_parts, path_parts):
            if expected.startswith("{") and expected.endswith("}"):
                params[expected[1:-1]] = urllib.parse.unquote(actual)
            elif expected != actual:
                return None
        return params

    def _run(self, component: dict[str, Any], context: dict[str, Any], steps: list[int]) -> dict:
        shapes = {shape["name"]: shape for shape in component["shapes"]}
        start = [shape for shape in component["shapes"] if shape["shapetype"] == "start"]
        if len(start) != 1:
            raise ValueError(f"{component['name']}: expected exactly one start shape")
        current = start[0]
        visited = 0
        while True:
            steps[0] += 1
            visited += 1
            if steps[0] > 100:
                raise RuntimeError(f"shape graph exceeded 100 steps in {component['name']}")
            shape_type = current["shapetype"]
            if shape_type == "connectoraction":
                self._connector(current, context)
                next_name = current.get("next")
            elif shape_type == "decision":
                next_name = self._decision_next(current, context)
            elif shape_type == "map":
                context["payload"] = self._map(current, context)
                next_name = current.get("next")
            elif shape_type == "message":
                context["payload"] = self._message(current, context)
                if "status" in current:
                    context["response_status"] = current["status"]
                next_name = current.get("next")
            elif shape_type == "properties":
                for name, source in current.get("properties", {}).items():
                    context["properties"][name] = self._resolve(source, context)
                next_name = current.get("next")
            elif shape_type == "processcall":
                subprocess = self.components.get(current.get("process", ""))
                if subprocess is None:
                    raise ValueError(f"missing subprocess: {current.get('process')}")
                result = self._run(subprocess, context, steps)
                if result.pop("_halt", False):
                    return result
                next_name = current.get("next")
            elif shape_type == "returndocuments":
                status = current.get("status")
                if status is None:
                    status = context["properties"].get("httpStatus", context["response_status"] or 200)
                try:
                    status = int(status)
                except (TypeError, ValueError):
                    status = 200
                result = {"status": status, "body": _json_value(context["payload"])}
                if self.halt_shapes.get(component["name"], set()).__contains__(current["name"]):
                    result["_halt"] = True
                return result
            else:
                next_name = current.get("next")
            if not next_name:
                raise ValueError(f"{component['name']}: shape {current['name']} has no next")
            if next_name not in shapes:
                raise ValueError(f"{component['name']}: unknown next shape {next_name}")
            current = shapes[next_name]

    def _resolve(self, source: dict[str, Any], context: dict[str, Any]) -> Any:
        kind = source["kind"]
        name = source.get("name")
        if kind == "pathParam":
            return context["pathParams"].get(name)
        if kind == "queryParam":
            return context["queryParams"].get(name)
        if kind == "header":
            value = context["headers"].get(str(name).lower())
            path = source.get("path")
            if value is not None and path:
                if path == "bearer":
                    if not str(value).lower().startswith("bearer "):
                        return None
                    return str(value)[7:].strip()
                if path.startswith("basic:"):
                    try:
                        decoded = base64.b64decode(str(value).split(" ", 1)[1]).decode()
                        user, _, password = decoded.partition(":")
                        return user if path == "basic:username" else password
                    except (ValueError, IndexError, UnicodeDecodeError):
                        return None
            return value
        if kind in {"body", "payload"}:
            value = context["body"] if kind == "body" else context["payload"]
            return self._json_path(value, source.get("path")) if source.get("path") else value
        if kind == "property":
            return context["properties"].get(name)
        if kind == "literal":
            return source.get("value")
        if kind == "rowcount":
            return len(context["payload"]) if isinstance(context["payload"], list) else 0
        if kind == "function":
            function = source.get("function")
            now = datetime.now(timezone.utc)
            if function == "uuid":
                return uuid.uuid4().hex
            if function == "now_iso":
                return now.isoformat()
            if function == "now_plus_seconds":
                return (now + timedelta(seconds=float(source.get("args", [0])[0]))).isoformat()
        raise ValueError(f"unsupported source: {source}")

    @staticmethod
    def _json_path(value: Any, path: str) -> Any:
        if path == "$":
            return value
        if not path or not path.startswith("$"):
            raise ValueError(f"unsupported JSON path: {path}")
        current = value
        tokens = re.findall(r"(?:^|\.)((?!$)[^.\[]+)|\[(\d+)\]", path[1:])
        for field, index in tokens:
            current = current[int(index)] if index else current[field]
        return current

    def _connector(self, shape: dict[str, Any], context: dict[str, Any]) -> None:
        if shape.get("connector") != "database":
            raise ValueError("only database connectoraction is supported")
        params = {
            name: self._resolve(source, context)
            for name, source in shape.get("parameters", {}).items()
        }
        sql = _PARAMETER.sub(lambda match: f"%({match.group(1)})s", shape.get("sql", ""))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            if shape.get("operation") == "select":
                columns = [column.name for column in cursor.description]
                context["payload"] = [
                    {column: _json_value(value) for column, value in zip(columns, row)}
                    for row in cursor.fetchall()
                ]
            elif shape.get("operation") == "execute":
                context["payload"] = {"affectedRows": cursor.rowcount}
            else:
                raise ValueError(f"unsupported database operation: {shape.get('operation')}")

    def _decision_next(self, shape: dict[str, Any], context: dict[str, Any]) -> str:
        condition = shape["condition"]
        try:
            left = self._resolve(condition["left"], context)
        except Exception:
            if condition["operator"] in {"exists", "notexists"}:
                left = None
            else:
                raise
        operator = condition["operator"]
        if operator in {"exists", "notexists"}:
            result = left is not None
            if operator == "notexists":
                result = not result
        elif operator in {"empty", "notempty"}:
            result = left is None or left == "" or len(left) == 0 if hasattr(left, "__len__") else left is None or left == ""
            if operator == "notempty":
                result = not result
        else:
            right = self._resolve(condition["right"], context)
            result = str(left) == str(right)
            if operator == "notequals":
                result = not result
        return shape["whenTrue"] if result else shape["whenFalse"]

    def _map(self, shape: dict[str, Any], context: dict[str, Any]) -> Any:
        output: dict[str, Any] = {}
        for mapping in shape.get("mappings", []):
            foreach = mapping.get("foreach")
            if foreach:
                rows = context["payload"] if isinstance(context["payload"], list) else []
                if "field" in foreach:
                    value = [row[foreach["field"]] for row in rows]
                else:
                    value = [
                        {field: row.get(field) for field in foreach.get("fields", [])}
                        for row in rows
                    ]
                if mapping["to"] == "$":
                    return value
                output[mapping["to"]] = value
            else:
                value = self._resolve(mapping["from"], context)
                if mapping["to"] == "$":
                    return value
                output[mapping["to"]] = value
        return output

    def _message(self, shape: dict[str, Any], context: dict[str, Any]) -> Any:
        def substitute(value: Any) -> Any:
            if isinstance(value, str):
                return _PLACEHOLDER.sub(
                    lambda match: str(
                        self._resolve(
                            {"kind": match.group(1), "name": match.group(2)}, context
                        )
                    ),
                    value,
                )
            if isinstance(value, list):
                return [substitute(item) for item in value]
            if isinstance(value, dict):
                return {key: substitute(item) for key, item in value.items()}
            return value

        return substitute(shape.get("template"))
