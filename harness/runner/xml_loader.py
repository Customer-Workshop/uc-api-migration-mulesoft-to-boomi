"""Translate the workshop's XML component subset to the schema JSON shape."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _source(element: ET.Element, *, ref_attr: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {}
    for key in ("kind", "path", "value", "function", "args"):
        if key in element.attrib:
            value: Any = element.attrib[key]
            if key == "args":
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = [value]
            source[key] = value
    if ref_attr and ref_attr in element.attrib:
        source["name"] = element.attrib[ref_attr]
    elif "name" in element.attrib and "name" not in source:
        source["name"] = element.attrib["name"]
    return source


def _shape(element: ET.Element) -> tuple[dict[str, Any], bool]:
    shape: dict[str, Any] = {
        "name": element.attrib["name"],
        "shapetype": element.attrib["shapetype"],
    }
    for key in ("next", "whenTrue", "whenFalse", "connector", "operation", "sql", "status", "process"):
        if key in element.attrib:
            value: Any = element.attrib[key]
            if key in {"status"}:
                value = int(value)
            shape[key] = value

    halted = element.attrib.get("halt", "").lower() == "true"
    for child in element:
        kind = _local(child.tag)
        if kind == "condition":
            condition: dict[str, Any] = {"operator": child.attrib["operator"]}
            left = next((x for x in child if _local(x.tag) == "left"), None)
            right = next((x for x in child if _local(x.tag) == "right"), None)
            if left is None:
                raise ValueError(f"condition on {shape['name']} has no left source")
            condition["left"] = _source(left)
            if right is not None:
                condition["right"] = _source(right)
            shape["condition"] = condition
        elif kind == "parameter":
            shape.setdefault("parameters", {})[child.attrib["name"]] = _source(child, ref_attr="ref")
        elif kind == "property":
            shape.setdefault("properties", {})[child.attrib["name"]] = _source(child, ref_attr="ref")
        elif kind == "sql":
            shape["sql"] = child.text or ""
        elif kind == "mapping":
            mapping: dict[str, Any] = {"to": child.attrib["to"], "from": _source(child)}
            foreach = next((x for x in child if _local(x.tag) == "foreach"), None)
            if foreach is not None:
                item: dict[str, Any] = {}
                if "field" in foreach.attrib:
                    item["field"] = foreach.attrib["field"]
                if "fields" in foreach.attrib:
                    item["fields"] = [x for x in foreach.attrib["fields"].split(",") if x]
                mapping["foreach"] = item
            shape.setdefault("mappings", []).append(mapping)
        elif kind == "template":
            raw = child.text or ""
            try:
                shape["template"] = json.loads(raw)
            except json.JSONDecodeError:
                shape["template"] = raw
    return shape, halted


def load_component(path: Path) -> tuple[dict[str, Any], dict[str, bool]]:
    """Load one component and return its schema form plus halt metadata."""
    root = ET.parse(path).getroot()
    if _local(root.tag) != "Component":
        raise ValueError(f"{path}: root element must be bns:Component")
    process = next((x for x in root.iter() if _local(x.tag) == "process"), None)
    if process is None:
        raise ValueError(f"{path}: missing process object")
    component: dict[str, Any] = {
        "name": root.attrib.get("name") or path.stem,
        "type": root.attrib.get("type", "process"),
        "shapes": [],
    }
    route = next((x for x in process if _local(x.tag) == "route"), None)
    if route is not None:
        component["route"] = {
            "method": route.attrib["method"].upper(),
            "path": route.attrib["path"],
        }
    shapes = next((x for x in process if _local(x.tag) == "shapes"), None)
    if shapes is None:
        raise ValueError(f"{path}: missing shapes")
    halt: dict[str, bool] = {}
    for element in shapes:
        if _local(element.tag) != "shape":
            continue
        shape, is_halt = _shape(element)
        component["shapes"].append(shape)
        if is_halt:
            halt[shape["name"]] = True
    return component, halt
