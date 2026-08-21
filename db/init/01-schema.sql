-- Employee Services database schema (consolidated from the MuleSoft source estate:
-- ts-java-mulesoft-employee-api). This is the shared source-of-truth database that
-- both the legacy Mule flows and the migrated Boomi processes run against.

CREATE TABLE users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    first_name VARCHAR(255) NOT NULL,
    last_name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE api_clients (
    client_id VARCHAR(255) PRIMARY KEY,
    client_secret VARCHAR(255) NOT NULL,
    user_id INTEGER REFERENCES users(user_id),
    access_token VARCHAR(255),
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE employee_goals (
    id SERIAL PRIMARY KEY,
    employee_id VARCHAR(32) NOT NULL,
    goal TEXT NOT NULL
);

CREATE TABLE employee_learning (
    id SERIAL PRIMARY KEY,
    employee_id VARCHAR(32) NOT NULL,
    course VARCHAR(255) NOT NULL,
    status VARCHAR(64) NOT NULL
);

CREATE TABLE employee_pto (
    employee_id VARCHAR(32) PRIMARY KEY,
    pto_balance NUMERIC(5,1),
    next_pay_date DATE
);

CREATE INDEX idx_api_clients_token ON api_clients(access_token);
CREATE INDEX idx_goals_employee ON employee_goals(employee_id);
CREATE INDEX idx_learning_employee ON employee_learning(employee_id);
