# Wave 2 design note — auth lifecycle (register / login / refresh / disconnect)

The estate assessment flags the auth lifecycle flows in
`contracts/source/employee-services-api.xml` as *flag for redesign*: they do
not map 1:1 onto Boomi process shapes. This note records the redesign before
implementation. The recorded MuleSoft behavior remains the source of truth for
everything we do not deliberately change below.

## Flow → component mapping

| Mule flow | Boomi component | Route |
|---|---|---|
| `user-registration-flow` | `AUTH - Register User` (process) | `POST /auth/register` |
| `user-login-flow` | `AUTH - Login` (process) | `POST /auth/login` |
| `refresh-token-flow` | `AUTH - Refresh Token` (process) | `POST /auth/refresh-token` |
| `disconnect-api-flow` | `AUTH - Disconnect` (process) | `POST /api/disconnect` |

No new subprocesses. None of these four flows calls
`validate-token-subflow` in the source — **including `POST /api/disconnect`,
which is unauthenticated in MuleSoft** (a source quirk we reproduce and flag,
not fix). The existing `Common - Validate Token` subprocess is unchanged and
remains wired into every wave-1 protected route.

Out of scope (deliberately not migrated): `cors-preflight-flow` (OPTIONS
handling and CORS headers move to the Boomi edge/gateway) and
`web-content-flow` (static HTML serving is not an integration concern).

## Token / session state on the existing Postgres schema

MuleSoft keeps all token state in `api_clients`; there is no object store in
these flows, so no object-store→DB replacement was needed — but the vendored
`db/init/01-schema.sql` (consolidated for wave 1) lacks the columns the auth
flows read and write. We extend `api_clients` (audited manifest approval):

- `salesforce_user_id VARCHAR(255)` — the external (Salesforce) user identity
  the login/disconnect flows key on.
- `token_expires_at TIMESTAMP` — written redundantly alongside `expires_at`
  by login/refresh, exactly as the Mule `UPDATE` does (quirk preserved).
- `refresh_token VARCHAR(255)` + `refresh_token_expires_at TIMESTAMP` —
  refresh-token state (1h access / 30d refresh, as in the source).
- `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` — touched by
  login/refresh/disconnect updates.

Seed additions (audited): two users with known salted-Base64 password hashes
(`jdoe` with no API client, `jsmith` with the pre-linked client `crm-portal`
carrying a non-expired seeded refresh token and `salesforce_user_id
'SF-USER-42'`) so login-new-client, login-existing-client, refresh and
disconnect fixtures replay deterministically from the seed alone. No existing
seed row or wave-1 fixture is touched.

## Deliberate changes vs. preserved behavior

Preserved (bit-for-bit where the parity gate can see it):
- Status codes and error bodies for every branch: 400 `missing_fields` /
  `invalid_request`, 401 `invalid_credentials` / `invalid_grant`,
  409 `user_exists`, 201 register success, 200/404 disconnect bodies.
- Password scheme: Base64(password + "salt") — same stored format; computed
  in SQL (`encode(convert_to(:password || 'salt','UTF8'),'base64')`) instead
  of an app-layer Java call, since Boomi map shapes have no scripting step in
  the local subset. Same bytes on disk either way.
- Login quirks: the payload `clientId` is **ignored** when the user already
  has an API client (the existing client's id is returned); `userId` /
  `salesforce_user_id` fallback in the request body; `user_id: null` in the
  response when no userId was sent; new-client `client_secret` is generated
  server-side and never returned.
- Refresh rotates BOTH tokens and updates `expires_at`, `token_expires_at`,
  `refresh_token_expires_at`, `updated_at` in one UPDATE keyed by client_id.
- Disconnect: unauthenticated (source quirk), soft-delete via
  `is_active = false`, 404 `not_found` body when no active row matched.

Deliberately changed (flagged divergences):
- **Salesforce callback stubbed at the boundary.** The Mule login/token flows
  can fire an async, error-swallowed HTTP callback to `callback_url`. The
  callback is fire-and-forget with `on-error-continue` (no observable effect
  on response or DB in the source), so it is stubbed out of the local
  components; a real deployment re-adds it as a Boomi HTTP Client operation
  behind the same "ignore failures" try/catch.
- **Token format.** Mule tokens are `uuid() ++ "-" ++ now()` and refresh
  tokens carry a `refresh_` prefix; the local runner's `uuid` function emits a
  bare hex uuid. Tokens are opaque bearer values (fixtures mask them; the DB
  probes verify persistence and rotation, not format).
- **Timestamp format in disconnect responses.** Source formats
  `yyyy-MM-dd'T'HH:mm:ss'Z'`; the runner emits ISO-8601 with offset. Masked as
  volatile; structure (field presence) is still asserted.
- **Hashing location** (see above): SQL expression instead of app layer.
- **CORS headers** on the /auth/* listeners move to the Boomi edge (the local
  transport wrapper is intentionally unauthenticated/loopback-only).

## Harness extension

- `boomi/routes.json` gains the four routes. `POST /api/disconnect` carries
  `"auth": false` because the source flow performs no token validation;
  `boomi/validate.py` now reads that flag (defaulting to the old
  "/api/* requires the token subprocess" rule) instead of hard-coding the
  path prefix — the gate is extended, not weakened.
- 13 new golden fixture cases (16–28) derived from the Mule XML cover
  register (success / missing fields / conflict), login (new client /
  existing client / bad credentials / missing fields), refresh (success /
  invalid / missing) and disconnect (success / not-found / missing fields),
  with SQL probes for every side effect (user row + hash value, client
  creation, token rotation, soft-delete).
- Schema/seed/routes/fixture changes go through
  `make fixtures-approve REASON=...` and are logged in
  `harness/fixtures/APPROVALS.log`.
