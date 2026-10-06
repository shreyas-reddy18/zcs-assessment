-- Warehouse schema for the Meridian HR assistant.
-- ${DS} is replaced with `project.dataset` by scripts/load_bigquery.py.
-- Keys are declared NOT ENFORCED: BigQuery does not enforce them, but they
-- document the model and let the optimizer use them for join elimination.

-- One row per employee. HRIS snapshot loaded from employees.csv.
-- pto_balance_days / pto_used_ytd are owned by the HRIS; the assistant never
-- edits them. Pending requests are netted off at query time (see 02_views.sql).
CREATE OR REPLACE TABLE ${DS}.employees (
  employee_id      STRING  NOT NULL,
  full_name        STRING  NOT NULL,
  email            STRING,
  department       STRING,
  job_title        STRING,
  manager_id       STRING,
  hire_date        DATE,
  pto_balance_days NUMERIC NOT NULL,
  pto_used_ytd     NUMERIC NOT NULL,
  location         STRING,
  employment_type  STRING,
  -- manager_id references employees.employee_id. BigQuery does not allow
  -- self-referencing foreign keys, so that relationship is documented here only.
  PRIMARY KEY (employee_id) NOT ENFORCED
);

-- One row per Google account allowed to use the assistant.
-- access_tier is only consulted for 'people_ops'. Manager rights are derived
-- from employees.manager_id, so they cannot drift from the org chart.
CREATE OR REPLACE TABLE ${DS}.identity_map (
  google_email STRING NOT NULL,
  employee_id  STRING NOT NULL,
  access_tier  STRING NOT NULL,
  notes        STRING,
  PRIMARY KEY (google_email) NOT ENFORCED,
  FOREIGN KEY (employee_id) REFERENCES ${DS}.employees (employee_id) NOT ENFORCED
);

-- One row per leave request. Historical rows come from pto_requests.csv;
-- rows written by the assistant carry the provenance columns so every write
-- traces back to the signed-in user and the conversation that produced it.
CREATE OR REPLACE TABLE ${DS}.pto_requests (
  request_id           STRING    NOT NULL,
  employee_id          STRING    NOT NULL,
  start_date           DATE      NOT NULL,
  end_date             DATE      NOT NULL,
  days_requested       NUMERIC   NOT NULL,
  status               STRING    NOT NULL,  -- pending | approved | denied
  submitted_date       DATE,
  approver_id          STRING,
  reason               STRING,
  source               STRING    NOT NULL,  -- hris_import | hr_assistant
  governing_doc_id     STRING,              -- policy edition the request was validated against
  idempotency_key      STRING,              -- sha256(employee_id|start|end); NULL for imported rows
  submitted_by_email   STRING,
  source_session_id    STRING,
  source_invocation_id STRING,
  created_at           TIMESTAMP,
  PRIMARY KEY (request_id) NOT ENFORCED,
  FOREIGN KEY (employee_id) REFERENCES ${DS}.employees (employee_id) NOT ENFORCED,
  FOREIGN KEY (approver_id) REFERENCES ${DS}.employees (employee_id) NOT ENFORCED
)
CLUSTER BY employee_id;

-- Append-only audit trail for the write path: every check, confirmation
-- miss, duplicate and submission, tied to the conversation that caused it.
-- Created only if missing so the trail survives a data reload.
CREATE TABLE IF NOT EXISTS ${DS}.pto_request_events (
  event_id        STRING    NOT NULL,
  event_type      STRING    NOT NULL,  -- CHECKED | CONFIRMATION_MISSING | SUBMITTED | DUPLICATE | REJECTED
  occurred_at     TIMESTAMP NOT NULL,
  actor_email     STRING    NOT NULL,
  employee_id     STRING,
  session_id      STRING,
  invocation_id   STRING,
  idempotency_key STRING,
  request_id      STRING,
  start_date      DATE,
  end_date        DATE,
  eligible        BOOL,
  details         JSON,
  PRIMARY KEY (event_id) NOT ENFORCED
)
PARTITION BY DATE(occurred_at)
CLUSTER BY session_id, idempotency_key;

-- Edition registry, mirrored from config/policy_editions.json.
CREATE OR REPLACE TABLE ${DS}.policy_editions (
  doc_id          STRING NOT NULL,
  file_name       STRING NOT NULL,
  title           STRING NOT NULL,
  policy_type     STRING NOT NULL,
  plan_year       INT64,
  edition_status  STRING NOT NULL,  -- current | superseded
  versioned       BOOL   NOT NULL,
  effective_start DATE,
  effective_end   DATE,
  governs         STRING,
  PRIMARY KEY (doc_id) NOT ENFORCED
);

-- Machine-checkable PTO rules per plan year, from config/pto_policy_rules.json.
CREATE OR REPLACE TABLE ${DS}.pto_lead_time_rules (
  plan_year         INT64  NOT NULL,
  min_business_days INT64  NOT NULL,
  max_business_days INT64,
  min_notice_hours  INT64  NOT NULL,
  note              STRING,
  source_doc_id     STRING NOT NULL,
  source_section    STRING NOT NULL,
  FOREIGN KEY (source_doc_id) REFERENCES ${DS}.policy_editions (doc_id) NOT ENFORCED
);

CREATE OR REPLACE TABLE ${DS}.pto_blackout_periods (
  plan_year        INT64  NOT NULL,
  name             STRING NOT NULL,
  start_date       DATE   NOT NULL,
  end_date         DATE   NOT NULL,
  max_days_allowed INT64  NOT NULL,
  source_doc_id    STRING NOT NULL,
  source_section   STRING NOT NULL,
  FOREIGN KEY (source_doc_id) REFERENCES ${DS}.policy_editions (doc_id) NOT ENFORCED
);

CREATE OR REPLACE TABLE ${DS}.non_working_days (
  day           DATE   NOT NULL,
  plan_year     INT64  NOT NULL,
  name          STRING NOT NULL,
  kind          STRING NOT NULL,  -- holiday | shutdown
  source_doc_id STRING NOT NULL,
  PRIMARY KEY (day) NOT ENFORCED
);
