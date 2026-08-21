# Playbook: Convert MuleSoft API flows to verified Boomi processes

> **Facilitator / presenter:** this file is the source for a **Devin Playbook**.
> Copy its contents into your Devin organization (Settings → Playbooks → *Create
> a new Playbook*) so sessions can invoke it as `!convert-mulesoft-to-boomi`.
> See [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks).
> Repo-specific commands (make targets, component format, namespaces) live in
> the companion Skill at `.agents/skills/mulesoft-to-boomi-migration/SKILL.md`,
> which Devin auto-loads when working in this repo.

## Overview

Convert **one or more** MuleSoft Mule 4 API flows into Boomi process
components, verified by a golden-fixture parity harness. The outcome is a PR
containing schema-valid Boomi process definitions plus parity evidence proving
the migrated processes behave **identically** to the source MuleSoft flows —
same status codes, same response shapes, same field types, same ordering, same
database side effects.

## The one principle: the recorded MuleSoft behavior is the source of truth

A migration reproduces behavior faithfully — it does not redesign it. If the
source has a quirk (a 200-with-message where you'd expect a 404, a POST that
writes to a column you'd never choose), reproduce it and **flag it** in a
migration note — never silently "improve" it. Cleaning up the API is a
separate, deliberate decision made with the team. That is why every migration
is gated by field-by-field replay of golden fixtures, not by "looks right"
review.

## Required from user

- **MuleSoft source** — the Mule XML flow file(s) and RAML/OAS spec to migrate.
- **Scope** — which routes/flows to convert (or "all in-scope routes" per the
  inventory's *maps cleanly* classification).
- **Namespace** — an isolated branch name so concurrent runs never collide
  (e.g. `migration/<wave-or-route>`). All outputs land on this branch; never
  push to the before-state branch or `main`.

## Procedure

1. Run the estate inventory tool over the Mule XML and read its report:
   per-flow endpoints, connectors, SQL, transforms, error handlers, and the
   *maps cleanly* vs *flag for redesign* classification. Confirm your assigned
   scope is in the *maps cleanly* set; anything flagged for redesign is out of
   scope for conversion.
2. Read the source of each in-scope flow closely: the trigger path/method, the
   auth gate, every `<db:select>`/`<db:update>` and its exact SQL, every
   DataWeave transform (these define the response field names and types), each
   `<choice>` branch, and each `<on-error-propagate>` handler with its exact
   status code and error body.
3. Map each Mule construct to its Boomi equivalent:
   - HTTP Listener / APIKit route → Web Services Server operation (process `route`)
   - token-validation subflow → shared subprocess called before any data access
   - `<db:select>` / `<db:update>` → Database connector action
   - `<choice>`/`<when>` → decision shape
   - DataWeave transform → map shape (field mappings; foreach for result sets)
   - static payloads / error bodies → message shape
   - `<try>`/`<on-error-propagate>` → try/catch with the source's exact status + body
4. Author one Boomi process component per route, in the repo's component
   format, and run the **component gate** (structural validation: schema-valid,
   route covered, auth subprocess wired before data access). Fix until green.
5. Run the **parity gate**: it resets the database to seed, replays every
   golden fixture case through the process, and diffs status + body
   field-by-field (order-sensitive, type-sensitive) plus SQL side-effect
   probes. Read the diagnostic report on failures.
6. Close the loop: when a case fails, investigate **against the source Mule
   flow** — never against your own intuition of what the API "should" do, and
   never by editing the fixture. Correct the component and re-run until green.
7. Deliver a PR from the namespace branch containing the components, the green
   component + parity gate output pasted into the PR body (the report file
   itself is a git-ignored artifact), and a migration note flagging every
   source quirk you reproduced.

## Specifications (postconditions)

- Component gate green: every in-scope route has a schema-valid process
  component; protected routes call the shared token-validation subprocess
  before any database access.
- Parity gate green: **all** fixture cases pass — status, field-by-field body
  (array order preserved, numeric vs string types preserved), and DB probes.
- Fixture integrity intact: the fixture manifest is untouched; no golden case,
  seed, or schema was edited to make a red case pass.
- Source quirks reproduced are explicitly flagged in the PR description.
- The PR targets the namespace branch's base, never the before-state branch.

## Worked example: a real bug the parity gate catches

Converting `GET /api/employee/{employeeId}/goals`, the natural Boomi mapping
returns a 404 with an error body when the query comes back empty — that is
what every other endpoint in the estate does. The parity gate fails the
`goals-none` case: the source Mule flow returns **HTTP 200** with
`{"message": "No goals found for employee 74"}` — a real quirk consumers may
already depend on. The fix is a decision shape routing the empty result to a
200 message shape reproducing that body, plus a migration-note flag. The same
gate also catches `ptoBalance` coming back as the string `"12.5"` instead of
the number `12.5`, and learning records re-sorted out of their source order —
the class of silent behavior drift that eyeball review misses.

## Advice and pointers

- Start with the auth subprocess — it gates every protected route and is the
  most common source of parity divergence (401 bodies differ between
  missing-header and invalid-token).
- The DataWeave expressions are the response contract: field names, nesting,
  and types map directly to map-shape output. Watch string-vs-number and
  date serialization.
- Empty-result handling differs per route in this estate (200-with-message vs
  404-with-error-body) — check each flow, don't generalize.
- POST side effects are verified by SQL probes, not just the response body —
  match the source's exact SQL semantics (including upserts).
- A red parity case is either a component bug or a fixture defect. If you
  believe the fixture is wrong, prove it against the source Mule XML first,
  then change it only through the audited approval path with a written reason.

## Forbidden actions

- Do NOT edit golden fixtures, the seed, the schema, or the fixture manifest
  to make a red case pass.
- Do NOT weaken the gates (masking extra fields, sorting arrays, relaxing type
  checks).
- Do NOT push to `main` or the before-state branch — all work stays on the
  namespace branch.
- Do NOT redesign the API (change paths, rename fields, "fix" quirky status
  codes) during migration.
- Do NOT convert flows classified *flag for redesign* — they need a human
  design decision first.
