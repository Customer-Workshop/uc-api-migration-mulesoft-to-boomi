-- Deterministic seed. The golden parity fixtures in harness/fixtures/cases/ are
-- derived from EXACTLY this data — every fixture case is reproducible from this
-- seed alone (no per-case setup). Changing this file invalidates the fixture
-- manifest (harness/fixtures/MANIFEST.sha256) and the parity gate will refuse
-- to run until the change is approved with an audited reason.

INSERT INTO users (username, email, first_name, last_name, password_hash) VALUES
    ('portal-svc', 'portal-svc@example.test', 'Portal', 'Service', 'x-not-a-real-hash-x');

-- OAuth clients. 'demo-portal' carries a pre-seeded, non-expired bearer token so
-- authenticated fixture cases replay without a prior token call; 'stale-portal'
-- carries an expired token for the invalid_token case.
INSERT INTO api_clients (client_id, client_secret, user_id, access_token, expires_at) VALUES
    ('demo-portal',  'demo-portal-secret',  1, 'seeded-valid-token-0001',   '2099-01-01 00:00:00'),
    ('stale-portal', 'stale-portal-secret', 1, 'seeded-expired-token-0001', '2020-01-01 00:00:00');

-- Employee 101: full records. Employee 74: PTO but NO goals (the source Mule
-- flow returns HTTP 200 with a message body for this case — a real quirk).
INSERT INTO employee_goals (employee_id, goal) VALUES
    ('101', 'Ship the Q3 integration platform migration'),
    ('101', 'Mentor two junior engineers through certification'),
    ('101', 'Reduce integration incident count by 25%');

INSERT INTO employee_learning (employee_id, course, status) VALUES
    ('101', 'Integration Architecture Fundamentals', 'completed'),
    ('101', 'Advanced Data Mapping',                 'in_progress'),
    ('74',  'Security & Compliance Basics',          'completed');

INSERT INTO employee_pto (employee_id, pto_balance, next_pay_date) VALUES
    ('101', 12.5, '2026-09-15'),
    ('74',  3.0,  '2026-09-15');
