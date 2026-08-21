# uc-api-migration-mulesoft-to-boomi

Migration use case: convert the MuleSoft **Employee Services API**
(`ts-java-mulesoft-employee-api`, vendored under `contracts/source/`) into
**Boomi process components**, with a fully local, programmatic verification
loop proving behavioral parity.

`main` is the durable **before-state**: the source estate, the assessment
tooling, the verification harness, golden fixtures, the Devin Playbook source,
and the repo Skill — but **no migrated components**. Devin produces the
migration live on a namespace branch; `boomi/components/` is empty here by
design, so `make verify` is red on `main` (that is the starting point).

## The verification loop

1. **Inventory / assessment** — `make inventory` parses the Mule XML and
   reports per-flow endpoints, connectors, SQL, transforms, error handlers,
   and a *maps cleanly* vs *flag for redesign* classification with complexity
   scores.
2. **Component gate** — `make validate` structurally validates every Boomi
   component against a closed-set schema and checks all 7 in-scope routes are
   covered with the auth subprocess wired before any data access.
3. **Parity gate** — `make parity` resets PostgreSQL to the deterministic
   seed, replays 15 golden fixture cases (recorded from the source MuleSoft
   behavior) through a local interpreter of the Boomi components, and diffs
   status + body **field-by-field** (order- and type-sensitive), plus SQL
   probes for database side effects. Fixtures are integrity-locked
   (`harness/fixtures/MANIFEST.sha256`); changing them requires an audited
   reason (`make fixtures-approve REASON=...`).

The local runner stands in for a test Atom so the loop is offline and
deterministic; `harness/atomsphere_client.py` shows how the same component
definitions and fixtures replay against a real test Atom via the AtomSphere
Platform API.

## Quick start

```bash
make db-up install      # seeded PostgreSQL + python deps (uv)
make harness-test       # runner self-tests (green on main)
make inventory          # estate assessment report
make verify             # component gate + parity gate (RED on main — no components yet)
```

See `.agents/skills/mulesoft-to-boomi-migration/SKILL.md` for full mechanics
and `.workshop/playbooks/convert-mulesoft-to-boomi.devin.md` for the portable
migration procedure (`!convert-mulesoft-to-boomi`).
