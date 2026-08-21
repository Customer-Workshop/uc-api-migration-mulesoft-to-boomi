---
name: mulesoft-to-boomi-migration
description: Repo-specific mechanics for converting the MuleSoft Employee Services API to Boomi process components in this repo — commands, component format, gates, namespaces. Use whenever migrating flows, running the component/parity gates, or debugging a red parity case here.
---

# MuleSoft → Boomi migration: repo mechanics

## Layout

- `contracts/source/employee-services-api.xml` — the vendored MuleSoft source
  flows (read-only source of truth; original repo:
  `Cognition-Partner-Workshops/ts-java-mulesoft-employee-api`).
- `contracts/source/employee-services-api.raml` — the RAML API contract.
- `boomi/components/*.xml` — the Boomi process components you author (one per
  route, plus the shared `Common - Validate Token` subprocess). Empty on the
  before-state; format documented in `harness/runner/SPEC.md`.
- `boomi/routes.json` — the closed set of in-scope routes the component gate
  checks.
- `boomi/schema/component.schema.json` — closed-set schema every component
  must satisfy (unknown shapetypes/connectors fail, never no-op).
- `harness/fixtures/cases/*.json` — golden transcripts recorded from the
  source MuleSoft app; integrity-locked by `harness/fixtures/MANIFEST.sha256`.
- `db/init/` — schema + deterministic seed. Every fixture case is reproducible
  from this seed alone; the parity gate resets the DB to it before each case.
- `inventory/inventory.py` — estate inventory/assessment report generator.

## Commands (from the repo root)

```bash
make db-up          # start seeded PostgreSQL (docker compose; PGPORT overridable)
make install        # uv sync
make inventory      # estate report -> inventory/report/estate.{json,md} (git-ignored)
make validate       # component gate: schema + route coverage + auth wiring
make parity         # parity gate: golden-fixture replay, field-by-field diff + DB probes
make verify         # validate + parity
make harness-test   # pytest self-tests of the local runner (green on main)
make run            # serve the components locally on 127.0.0.1:8090 (loopback only)
make db-down        # stop and remove the database
```

Reports land in `harness/reports/parity-report.json` and
`inventory/report/` — both **git-ignored CI artifacts**. Paste the gate
summary lines (e.g. `PARITY: 15/15 parity cases passed`) and any failure
detail into the PR body as evidence; do not commit the report files.

## Namespaces / isolation

- Work on your own branch (e.g. `migration/<route-or-wave>`); never push to
  `main` (durable before-state).
- Concurrent local runs: `COMPOSE_PROJECT_NAME=<ns> PGPORT=<port> make db-up`
  and `DATABASE_URL=postgresql://employee_user:employee_pass@localhost:<port>/employee_db`
  for the gates. Each session's VM is isolated anyway; this matters only when
  sharing a machine.
- Revert = delete the branch and `make db-down` (the DB is disposable and
  reseeded from `db/init/` on every gate run).

## The local runner vs a real Atom

`harness/runner/` interprets the component XML locally so the parity loop runs
offline and deterministically. `harness/atomsphere_client.py` is the
AtomSphere Platform API client: with `BOOMI_API_TOKEN` unset it records the
would-be `create_component`/`deploy`/`execute` calls to
`harness/reports/atomsphere-plan.json`; a real migration points it at a test
Atom and replays the same fixtures there. The local HTTP wrapper (`make run`)
is loopback-bound and intentionally unauthenticated at the transport level —
a real deployment adds auth hardening, tenant scoping, and CORS at the Boomi
edge.

## Fixture integrity (audited changes only)

`make parity` refuses to grade if any behavior input (fixtures, schema, seed,
vendored source XML) differs from `harness/fixtures/MANIFEST.sha256`. If a
fixture is genuinely wrong, prove it against `contracts/source/…xml`, fix it,
then run:

```bash
make fixtures-approve REASON="<what was wrong, proven against which source flow>"
```

This re-fingerprints and appends the reason to
`harness/fixtures/APPROVALS.log`. Never approve to make a red component pass.

## Debugging a red parity case

1. Read the failure lines in the gate output / `parity-report.json` — they are
   field-level (`$.ptoBalance: "12.5" != expected 12.5`).
2. Run one case: `cd harness && uv run python parity.py --case <name>`.
3. Compare against the source flow in `contracts/source/employee-services-api.xml`
   (search for the flow name, read its DataWeave and error handlers).
4. Fix the component, re-run. Known source quirks fixtures encode on purpose:
   - `goals-none`: empty goals → **200** `{"message": "No goals found for employee 74"}`, not 404.
   - `pto-schedule`: POST upserts `endDate` into `employee_pto.next_pay_date`; there is no schedule table.
   - `learning-found`: `employeeId` is a **string**; rows keep seed order.
   - `pto-balance-found`: `ptoBalance` is a **number** (12.5).
   - OAuth: `expires_in` 3600, scope `"read write"`; 401 bodies differ between
     missing header (`missing_token`) and bad/expired token (`invalid_token`).
