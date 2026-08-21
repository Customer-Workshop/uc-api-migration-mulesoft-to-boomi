"""Inventory the vendored MuleSoft estate without requiring Mule tooling."""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts" / "source" / "employee-services-api.xml"
REPORT = ROOT / "inventory" / "report"
DEMO_ROUTES = {
    ("POST", "/oauth/token"),
    ("GET", "/health"),
    ("GET", "/api/employee/{employeeId}/goals"),
    ("GET", "/api/employee/{employeeId}/learning-status"),
    ("GET", "/api/employee/{employeeId}/next-pay-date"),
    ("GET", "/api/employee/{employeeId}/pto/balance"),
    ("POST", "/api/employee/{employeeId}/pto/schedule"),
}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def flow_path(flow: ET.Element) -> tuple[str, str]:
    listener = next((x for x in flow.iter() if local(x.tag) == "listener"), None)
    if listener is None:
        return "unknown", ""
    method = listener.attrib.get("allowedMethods", "GET").split(",")[0].strip().upper()
    return method, listener.attrib.get("path", "")


def classify(method: str, path: str, name: str) -> str:
    if (method, path) in DEMO_ROUTES:
        return "maps cleanly"
    lower = f"{name} {path}".lower()
    flags = ("object-store", "objectstore", "salesforce", "web", "cors", "register",
             "login", "refresh", "disconnect", "/api/*", "/auth/*")
    return "flag for redesign" if any(flag in lower for flag in flags) else "flag for redesign"


def main() -> int:
    root = ET.parse(SOURCE).getroot()
    rows = []
    for flow in root:
        if local(flow.tag) not in {"flow", "sub-flow"}:
            continue
        name = flow.attrib.get("name", "")
        method, path = flow_path(flow)
        connectors = set()
        sql = []
        transforms = 0
        handlers = 0
        shapes = 0
        for item in flow.iter():
            tag = local(item.tag)
            if tag not in {"flow", "sub-flow"}:
                shapes += 1
            for key, value in item.attrib.items():
                if key in {"config-ref", "connector"}:
                    connectors.add(value.split("_")[0].lower())
            if ":" in item.tag:
                uri = item.tag.split("}", 1)[0].strip("{")
                if "http" in uri:
                    connectors.add("http")
                elif "db" in uri:
                    connectors.add("db")
                elif "objectstore" in uri or uri.endswith("/os"):
                    connectors.add("os")
                elif "salesforce" in uri:
                    connectors.add("salesforce")
            if tag == "sql" and (item.text or "").strip():
                sql.append(" ".join((item.text or "").split()))
            if tag in {"transform", "set-payload"}:
                transforms += 1
            if tag.startswith("on-error"):
                handlers += 1
        complexity = "S" if shapes + transforms + handlers <= 8 else "M" if shapes + transforms + handlers <= 20 else "L"
        rows.append({
            "flow": name,
            "trigger": {"method": method, "path": path},
            "connectors": sorted(connectors),
            "sql_statements": sql,
            "dataweave_transform_count": transforms,
            "error_handler_count": handlers,
            "mapping": classify(method, path, name),
            "complexity": complexity,
        })
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "estate.json").write_text(json.dumps(rows, indent=2) + "\n")
    lines = ["# MuleSoft estate inventory", "", "| Flow | Trigger | Connectors | SQL | DataWeave | Errors | Mapping | Complexity |",
             "|---|---|---|---:|---:|---:|---|---|"]
    for row in rows:
        trigger = f"{row['trigger']['method']} {row['trigger']['path']}"
        lines.append(f"| {row['flow']} | `{trigger}` | {', '.join(row['connectors']) or '—'} | "
                     f"{len(row['sql_statements'])} | {row['dataweave_transform_count']} | "
                     f"{row['error_handler_count']} | {row['mapping']} | {row['complexity']} |")
    (REPORT / "estate.md").write_text("\n".join(lines) + "\n")
    print(f"Inventory written to {REPORT} ({len(rows)} flows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
