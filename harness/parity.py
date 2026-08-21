"""Golden-fixture parity gate.

Replays every recorded case in harness/fixtures/cases/ through the local Boomi
process runner (harness/runner) and diffs the response field-by-field against
the golden transcript captured from the source MuleSoft application
(ts-java-mulesoft-employee-api). Database side effects are checked with SQL
probes. This gate is the evidence that "it behaves the same".

Rules this gate enforces (do not weaken them):
- The database is reset to db/init/ seed before EVERY case, so each case is
  reproducible from the seed alone — never from another case's side effects.
- Comparison is exact and ORDER-SENSITIVE for arrays (response ordering is a
  business rule; neither side is canonicalized).
- Only JSON paths listed in a case's "mask" are excluded (volatile values:
  issued tokens, timestamps). Masks never hide structure — a masked path must
  still be present.
- Fixtures are integrity-checked against MANIFEST.sha256. Editing a fixture
  requires an audited approval:  make fixtures-approve REASON="..."
- A diagnostic report is written on BOTH success and failure to
  harness/reports/parity-report.json (git-ignored; paste the summary into the
  PR body).

Exit codes: 0 all green, 1 parity failures, 2 fixture/manifest integrity error.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
CASES_DIR = HARNESS_DIR / "fixtures" / "cases"
MANIFEST = HARNESS_DIR / "fixtures" / "MANIFEST.sha256"
REPORTS_DIR = HARNESS_DIR / "reports"
SEED_FILES = [REPO_ROOT / "db" / "init" / "01-schema.sql", REPO_ROOT / "db" / "init" / "02-seed.sql"]
SOURCE_XML = REPO_ROOT / "contracts" / "source" / "employee-services-api.xml"
CONTRACT_FILES = [
    REPO_ROOT / "boomi" / "schema" / "component.schema.json",
    REPO_ROOT / "boomi" / "routes.json",
]

MASK_TOKEN = "__MASKED__"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_fingerprint() -> dict:
    """Fingerprint every input that changes recorded behavior: the fixture
    cases, the db schema+seed, the vendored source Mule XML, the component
    JSON Schema, and the route manifest."""
    files = sorted(CASES_DIR.glob("*.json")) + [p for p in SEED_FILES if p.exists()]
    if SOURCE_XML.exists():
        files.append(SOURCE_XML)
    files.extend(p for p in CONTRACT_FILES if p.exists())
    return {str(p.relative_to(REPO_ROOT)): _sha256(p) for p in files}


def check_manifest() -> list[str]:
    if not MANIFEST.exists():
        return ["fixtures/MANIFEST.sha256 is missing — run `make fixtures-approve REASON=...`"]
    recorded = {}
    for line in MANIFEST.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            digest, _, name = line.partition("  ")
            recorded[name.strip()] = digest.strip()
    problems = []
    actual = fixture_fingerprint()
    for name, digest in actual.items():
        if name not in recorded:
            problems.append(f"untracked behavior input: {name} (approve with an audited reason)")
        elif recorded[name] != digest:
            problems.append(f"modified without approval: {name}")
    for name in recorded:
        if name not in actual:
            problems.append(f"recorded input disappeared: {name}")
    return problems


def write_manifest(reason: str) -> None:
    if not reason or len(reason.strip()) < 10:
        sys.exit("Refusing: fixtures-approve requires a substantive REASON (>=10 chars).")
    lines = [f"{digest}  {name}" for name, digest in sorted(fixture_fingerprint().items())]
    MANIFEST.write_text("\n".join(lines) + "\n")
    log = HARNESS_DIR / "fixtures" / "APPROVALS.log"
    with log.open("a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}  {reason.strip()}\n")
    print(f"Manifest updated ({len(lines)} inputs). Reason logged to fixtures/APPROVALS.log")


def db_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://employee_user:employee_pass@localhost:5432/employee_db",
    )


def reset_db(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in SEED_FILES:
            cur.execute(f.read_text())
    conn.commit()


def apply_masks(body, masks: list[str]):
    """Mask supports only simple `$.field` paths (top-level). A masked path must
    exist — masking never hides a missing field."""
    problems = []
    if not masks:
        return body, problems
    masked = copy.deepcopy(body)
    for m in masks:
        if not m.startswith("$."):
            problems.append(f"unsupported mask path: {m}")
            continue
        key = m[2:]
        if isinstance(masked, dict) and key in masked:
            masked[key] = MASK_TOKEN
        else:
            problems.append(f"masked path absent from response: {m}")
    return masked, problems


def diff_json(expected, actual, path="$") -> list[str]:
    """Exact, order-sensitive structural diff. Returns human-readable field diffs."""
    diffs = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in expected:
            if k not in actual:
                diffs.append(f"{path}.{k}: missing (expected {json.dumps(expected[k])})")
            else:
                diffs.extend(diff_json(expected[k], actual[k], f"{path}.{k}"))
        for k in actual:
            if k not in expected:
                diffs.append(f"{path}.{k}: unexpected field (got {json.dumps(actual[k])})")
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            diffs.append(f"{path}: length {len(actual)} != expected {len(expected)}")
        for i, (e, a) in enumerate(zip(expected, actual)):
            diffs.extend(diff_json(e, a, f"{path}[{i}]"))
    else:
        if type(expected) is not type(actual) and not (
            isinstance(expected, (int, float)) and isinstance(actual, (int, float))
            and not isinstance(expected, bool) and not isinstance(actual, bool)
        ):
            diffs.append(
                f"{path}: type {type(actual).__name__} != expected {type(expected).__name__}"
                f" (got {json.dumps(actual)}, expected {json.dumps(expected)})"
            )
        elif expected != actual:
            diffs.append(f"{path}: {json.dumps(actual)} != expected {json.dumps(expected)}")
    return diffs


def json_path_get(body, path: str):
    if not path.startswith("$."):
        raise ValueError(f"unsupported response_path: {path}")
    cur = body
    for key in path[2:].split("."):
        cur = cur[key]
    return cur


def run_probes(conn: psycopg.Connection, probes: list[dict], response_body) -> list[str]:
    failures = []
    for p in probes:
        with conn.cursor() as cur:
            cur.execute(p["sql"])
            rows = [list(r) for r in cur.fetchall()]
        if p["kind"] == "rows":
            if rows != p["expect"]:
                failures.append(f"probe '{p['description']}': got {rows}, expected {p['expect']}")
        elif p["kind"] == "response_db_match":
            db_val = rows[0][0] if rows and rows[0] else None
            try:
                resp_val = json_path_get(response_body, p["response_path"])
            except (KeyError, TypeError):
                failures.append(
                    f"probe '{p['description']}': response has no {p['response_path']} (body: {json.dumps(response_body)})"
                )
                continue
            if db_val != resp_val or db_val in (None, ""):
                failures.append(
                    f"probe '{p['description']}': DB value {db_val!r} != response {p['response_path']} {resp_val!r}"
                )
        else:
            failures.append(f"probe '{p['description']}': unknown probe kind {p['kind']!r}")
    return failures


def run_case(case: dict, conn: psycopg.Connection) -> dict:
    from runner.engine import ProcessEngine  # local Boomi process runner

    reset_db(conn)
    engine = ProcessEngine(components_dir=REPO_ROOT / "boomi" / "components", conn=conn)
    req = case["request"]
    result = engine.handle(
        method=req["method"],
        path=req["path"],
        headers=req.get("headers", {}),
        body=req.get("body"),
    )
    conn.commit()

    failures = []
    expected = case["expected"]
    if result["status"] != expected["status"]:
        failures.append(f"status: {result['status']} != expected {expected['status']}")
    masked_actual, mask_problems = apply_masks(result["body"], case.get("mask", []))
    failures.extend(mask_problems)
    failures.extend(diff_json(expected["body"], masked_actual))
    failures.extend(run_probes(conn, case.get("probes", []), result["body"]))
    return {
        "case": case["case"],
        "description": case.get("description", ""),
        "passed": not failures,
        "failures": failures,
        "actual": {"status": result["status"], "body": masked_actual},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MuleSoft→Boomi golden-fixture parity gate")
    parser.add_argument("--approve-fixtures", metavar="REASON", help="re-fingerprint fixtures with an audited reason")
    parser.add_argument("--case", help="run a single case by name")
    args = parser.parse_args()

    if args.approve_fixtures is not None:
        write_manifest(args.approve_fixtures)
        return 0

    integrity = check_manifest()
    if integrity:
        print("FIXTURE INTEGRITY FAILURE — the gate refuses to grade against unapproved evidence:")
        for p in integrity:
            print(f"  - {p}")
        return 2

    sys.path.insert(0, str(HARNESS_DIR))
    cases = []
    for f in sorted(CASES_DIR.glob("*.json")):
        c = json.loads(f.read_text())
        if not args.case or c["case"] == args.case:
            cases.append(c)

    results = []
    with psycopg.connect(db_dsn()) as conn:
        for case in cases:
            try:
                results.append(run_case(case, conn))
            except Exception as e:  # component missing, engine error, SQL error
                conn.rollback()
                results.append({
                    "case": case["case"],
                    "description": case.get("description", ""),
                    "passed": False,
                    "failures": [f"engine error: {type(e).__name__}: {e}"],
                    "actual": None,
                })
        reset_db(conn)  # leave the DB in its seeded state

    passed = sum(1 for r in results if r["passed"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fixture_fingerprint(),
        "summary": f"{passed}/{len(results)} parity cases passed",
        "results": results,
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "parity-report.json").write_text(json.dumps(report, indent=2) + "\n")

    for r in results:
        marker = "PASS" if r["passed"] else "FAIL"
        print(f"[{marker}] {r['case']} — {r['description']}")
        for f_ in r["failures"]:
            print(f"        {f_}")
    print(f"\nPARITY: {report['summary']}  (report: harness/reports/parity-report.json)")
    return 0 if passed == len(results) and results else 1


if __name__ == "__main__":
    sys.exit(main())
