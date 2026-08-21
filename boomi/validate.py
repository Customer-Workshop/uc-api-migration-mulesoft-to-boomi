"""Validate that every demo route has a safe, schema-valid component graph."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from runner.xml_loader import load_component


ROUTES = json.loads((ROOT / "boomi" / "routes.json").read_text())
COMPONENTS = ROOT / "boomi" / "components"
SCHEMA = json.loads((ROOT / "boomi" / "schema" / "component.schema.json").read_text())


def _path_match(expected: str, actual: str) -> bool:
    a, b = expected.strip("/").split("/"), actual.strip("/").split("/")
    return len(a) == len(b) and all(x.startswith("{") or x == y for x, y in zip(a, b))


def _reaches_safe_connector(component: dict) -> bool:
    shapes = {shape["name"]: shape for shape in component["shapes"]}
    starts = [s for s in component["shapes"] if s["shapetype"] == "start"]
    if len(starts) != 1:
        return False
    pending = [(starts[0]["name"], False, set())]
    while pending:
        name, called, seen = pending.pop()
        if name in seen:
            continue
        seen = seen | {name}
        shape = shapes[name]
        called = called or (
            shape["shapetype"] == "processcall"
            and shape.get("process") == "Common - Validate Token"
        )
        if shape["shapetype"] == "connectoraction" and not called:
            return False
        if shape["shapetype"] == "decision":
            pending.extend(
                (next_name, called, seen)
                for next_name in (shape.get("whenTrue"), shape.get("whenFalse"))
                if next_name
            )
        elif shape.get("next"):
            pending.append((shape["next"], called, seen))
    return True


def main() -> int:
    loaded = []
    errors = []
    for path in sorted(COMPONENTS.glob("*.xml")):
        try:
            component, _ = load_component(path)
            jsonschema.validate(component, SCHEMA)
            loaded.append(component)
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")

    common = [c for c in loaded if c["name"] == "Common - Validate Token"]
    if len(common) != 1:
        errors.append(f"expected exactly one 'Common - Validate Token' subprocess; found {len(common)}")

    implemented = 0
    rows = []
    for route in ROUTES:
        matches = [
            c for c in loaded
            if c.get("route", {}).get("method") == route["method"]
            and _path_match(c.get("route", {}).get("path", ""), route["path"])
        ]
        route_errors = []
        if len(matches) != 1:
            route_errors.append("missing" if not matches else "duplicate")
        elif route["path"].startswith("/api/"):
            component = matches[0]
            calls = [
                s for s in component["shapes"]
                if s["shapetype"] == "processcall"
                and s.get("process") == "Common - Validate Token"
            ]
            if len(calls) != 1:
                route_errors.append("token subprocess count is not 1")
            elif not _reaches_safe_connector(component):
                route_errors.append("connectoraction reachable before token validation")
        if not route_errors:
            implemented += 1
        rows.append((route["method"], route["path"], "PASS" if not route_errors else "FAIL",
                     "; ".join(route_errors)))

    print("METHOD  PATH                                      STATUS  DETAILS")
    for method, path, status, details in rows:
        print(f"{method:<7} {path:<42} {status:<7} {details}")
    print(f"\n{implemented}/{len(ROUTES)} routes implemented")
    for error in errors:
        print(f"ERROR: {error}")
    return 0 if implemented == len(ROUTES) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
