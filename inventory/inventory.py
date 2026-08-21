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


APIKIT_NAME = re.compile(r"^(get|post|put|delete):((?:\\[^:]+)+)")


def flow_path(flow: ET.Element) -> tuple[str, str]:
    listener = next((x for x in flow.iter() if local(x.tag) == "listener"), None)
    if listener is not None:
        method = listener.attrib.get("allowedMethods", "GET").split(",")[0].strip().upper()
        return method, listener.attrib.get("path", "")
    # APIKit-routed flows carry their route in the flow name, e.g.
    # get:\employee\(employeeId)\goals:employee-services-api-config
    match = APIKIT_NAME.match(flow.attrib.get("name", ""))
    if match:
        path = match.group(2).replace("\\", "/").replace("(", "{").replace(")", "}")
        return match.group(1).upper(), f"/api{path}"
    return "", ""


def classify(method: str, path: str, name: str) -> tuple[str, str]:
    if (method, path) in DEMO_ROUTES:
        return "maps cleanly", "direct Boomi process mapping (in-scope wave 1 route)"
    lower = name.lower()
    if "validate-token" in lower:
        return "maps cleanly", "shared 'Common - Validate Token' subprocess"
    if "main" in lower or "console" in lower:
        return "absorbed", "APIKit router/console absorbed by Boomi Web Services Server configuration"
    reasons = [
        ("salesforce", "Salesforce callback integration needs redesign against Boomi's Salesforce connector"),
        ("refresh", "refresh-token lifecycle differs from Boomi API Management token handling"),
        ("regist", "user registration/password flow is out of migration scope"),
        ("login", "interactive login flow is out of migration scope"),
        ("disconnect", "connected-app teardown flow needs a design decision"),
        ("cors", "CORS preflight is edge configuration in Boomi, not a process"),
        ("web", "static web content does not map to a Boomi process"),
    ]
    for marker, reason in reasons:
        if marker in lower:
            return "flag for redesign", reason
    return "flag for redesign", "no clean Boomi construct mapping identified"


def main() -> int:
    root = ET.parse(SOURCE).getroot()
    rows = []
    for flow in root:
        if local(flow.tag) not in {"flow", "sub-flow"}:
            continue
        name = flow.attrib.get("name", "")
        method, path = flow_path(flow)
        mapping, reason = classify(method, path, name)
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
        complexity = "S" if shapes + transforms + handlers <= 15 else "M" if shapes + transforms + handlers <= 40 else "L"
        rows.append({
            "flow": name,
            "trigger": {"method": method, "path": path},
            "connectors": sorted(connectors),
            "sql_statements": sql,
            "dataweave_transform_count": transforms,
            "error_handler_count": handlers,
            "mapping": mapping,
            "reason": reason,
            "complexity": complexity,
        })
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "estate.json").write_text(json.dumps(rows, indent=2) + "\n")
    lines = ["# MuleSoft estate inventory", "", "| Flow | Trigger | Connectors | SQL | DataWeave | Errors | Mapping | Complexity | Why |",
             "|---|---|---|---:|---:|---:|---|---|---|"]
    for row in rows:
        trigger = f"{row['trigger']['method']} {row['trigger']['path']}".strip()
        lines.append(f"| {row['flow']} | `{trigger or 'n/a'}` | {', '.join(row['connectors']) or '—'} | "
                     f"{len(row['sql_statements'])} | {row['dataweave_transform_count']} | "
                     f"{row['error_handler_count']} | {row['mapping']} | {row['complexity']} | {row['reason']} |")
    (REPORT / "estate.md").write_text("\n".join(lines) + "\n")
    print(f"Inventory written to {REPORT} ({len(rows)} flows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
