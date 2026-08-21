from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from runner.engine import ProcessEngine


SCHEMA = Path(__file__).resolve().parents[2] / "boomi" / "schema" / "component.schema.json"
DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://employee_user:employee_pass@localhost:5432/employee_db",
)


def component(tmp_path: Path, *xmls: str) -> Path:
    components = tmp_path / "components"
    schema = tmp_path / "schema"
    components.mkdir(parents=True, exist_ok=True)
    schema.mkdir(parents=True, exist_ok=True)
    (schema / "component.schema.json").write_text(SCHEMA.read_text())
    for index, xml in enumerate(xmls):
        (components / f"{index}.xml").write_text(xml)
    return components


def xml(name: str, shapes: str, route: str = ' method="GET" path="/test"') -> str:
    return f"""<bns:Component xmlns:bns="http://api.platform.boomi.com/" type="process" name="{name}">
  <bns:object><process><route{route}/><shapes>{shapes}</shapes></process></bns:object>
</bns:Component>"""


def run(tmp_path: Path, xmls: tuple[str, ...], method="GET", path="/test", headers=None, body=None):
    with psycopg.connect(DSN) as conn:
        return ProcessEngine(component(tmp_path, *xmls), conn).handle(method, path, headers or {}, body)


def test_routing_and_501(tmp_path):
    shapes = '<shape name="start" shapetype="start" next="msg"/><shape name="msg" shapetype="message" next="done"><template>{"ok": true}</template></shape><shape name="done" shapetype="returndocuments" status="200"/>'
    engine_dir = component(tmp_path, xml("route", shapes))
    with psycopg.connect(DSN) as conn:
        engine = ProcessEngine(engine_dir, conn)
        assert engine.handle("GET", "/test", {}, {}) == {"status": 200, "body": {"ok": True}}
        assert engine.handle("GET", "/missing", {}, {}) == {"status": 501, "body": {"error": "not_implemented", "route": "GET /missing"}}


@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    [
        ("equals", 'kind="literal" value="a"', 'kind="literal" value="a"', "yes"),
        ("notequals", 'kind="literal" value="a"', 'kind="literal" value="b"', "yes"),
        ("empty", 'kind="literal" value=""', None, "yes"),
        ("notempty", 'kind="literal" value="a"', None, "yes"),
        ("exists", 'kind="literal" value="a"', None, "yes"),
        ("notexists", 'kind="property" name="missing"', None, "yes"),
    ],
)
def test_decision_operators(tmp_path, operator, left, right, expected):
    right_xml = f"<right {right}/>" if right else ""
    condition = f'<condition operator="{operator}"><left {left}/>{right_xml}</condition>'
    shapes = f'<shape name="start" shapetype="start" next="decision"/><shape name="decision" shapetype="decision" whenTrue="yes" whenFalse="no">{condition}</shape><shape name="yes" shapetype="message" next="done"><template>{{"result": "yes"}}</template></shape><shape name="no" shapetype="message" next="done"><template>{{"result": "no"}}</template></shape><shape name="done" shapetype="returndocuments"/>'
    assert run(tmp_path, (xml("decision", shapes),))["body"]["result"] == expected


def test_map_foreach_and_message_placeholders(tmp_path):
    shapes = '<shape name="start" shapetype="start" next="map"/><shape name="map" shapetype="map" next="message"><mapping to="items" kind="payload"><foreach fields="a,b"/></mapping></shape><shape name="message" shapetype="message" next="done"><template>{"employee": "{{pathParam:id}}", "token": "{{header:x}}"}</template></shape><shape name="done" shapetype="returndocuments"/>'
    # The map is exercised directly with a request payload; message then replaces
    # it, proving both operations share one context.
    result = run(tmp_path, (xml("map", shapes, ' method="GET" path="/test/{id}"'),), path="/test/007", headers={"X": "abc"}, body=[{"a": 1, "b": "x"}])
    assert result == {"status": 200, "body": {"employee": "007", "token": "abc"}}

    bare = '<shape name="start" shapetype="start" next="map"/><shape name="map" shapetype="map" next="done"><mapping to="$" kind="payload"><foreach field="a"/></mapping></shape><shape name="done" shapetype="returndocuments"/>'
    assert run(tmp_path, (xml("bare", bare),), body=[{"a": 2}, {"a": 3}])["body"] == [2, 3]


def test_processcall_halt_short_circuits(tmp_path):
    common = """<bns:Component xmlns:bns="http://api.platform.boomi.com/" type="process" name="Common - Validate Token">
      <bns:object><process><shapes>
        <shape name="start" shapetype="start" next="halt"/>
        <shape name="halt" shapetype="returndocuments" status="401" halt="true"><!-- no-op --></shape>
      </shapes></process></bns:object>
    </bns:Component>"""
    parent = xml("parent", '<shape name="start" shapetype="start" next="auth"/><shape name="auth" shapetype="processcall" process="Common - Validate Token" next="ok"/><shape name="ok" shapetype="message" next="done"><template>{"ok": true}</template></shape><shape name="done" shapetype="returndocuments"/>')
    assert run(tmp_path, (parent, common)) == {"status": 401, "body": None}


def test_connector_select_order_and_serialization(tmp_path):
    shapes = '<shape name="start" shapetype="start" next="select"/><shape name="select" shapetype="connectoraction" connector="database" operation="select" next="done"><sql>SELECT goal FROM employee_goals WHERE employee_id = :id ORDER BY id</sql><parameter name="id" kind="pathParam" ref="id"/></shape><shape name="done" shapetype="returndocuments"/>'
    result = run(tmp_path, (xml("select", shapes, ' method="GET" path="/test/{id}"'),), path="/test/101")
    assert [row["goal"] for row in result["body"]] == [
        "Ship the Q3 integration platform migration",
        "Mentor two junior engineers through certification",
        "Reduce integration incident count by 25%",
    ]

    date_shapes = '<shape name="start" shapetype="start" next="select"/><shape name="select" shapetype="connectoraction" connector="database" operation="select" next="done"><sql>SELECT next_pay_date, pto_balance FROM employee_pto WHERE employee_id = :id</sql><parameter name="id" kind="pathParam" ref="id"/></shape><shape name="done" shapetype="returndocuments"/>'
    row = run(tmp_path, (xml("dates", date_shapes, ' method="GET" path="/test/{id}"'),), path="/test/101")["body"][0]
    assert row == {"next_pay_date": "2026-09-15", "pto_balance": 12.5}


def test_execute_properties_and_http_status(tmp_path):
    shapes = '<shape name="start" shapetype="start" next="props"/><shape name="props" shapetype="properties" next="exec"><property name="httpStatus" kind="literal" value="207"/><property name="echo" kind="pathParam" ref="id"/></shape><shape name="exec" shapetype="connectoraction" connector="database" operation="execute" next="message"><sql>UPDATE employee_pto SET next_pay_date = next_pay_date WHERE employee_id = :id</sql><parameter name="id" kind="pathParam" ref="id"/></shape><shape name="message" shapetype="message" next="done"><template>{"id": "{{property:echo}}", "affected": 1}</template></shape><shape name="done" shapetype="returndocuments"/>'
    result = run(tmp_path, (xml("execute", shapes, ' method="POST" path="/test/{id}"'),), method="POST", path="/test/74")
    assert result == {"status": 207, "body": {"id": "74", "affected": 1}}


def test_cycle_guard(tmp_path):
    shapes = '<shape name="start" shapetype="start" next="loop"/><shape name="loop" shapetype="message" next="loop"><template>{"loop": true}</template></shape>'
    with pytest.raises(RuntimeError, match="100 steps"):
        run(tmp_path, (xml("cycle", shapes),))
