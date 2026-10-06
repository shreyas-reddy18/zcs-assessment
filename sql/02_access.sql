-- Authorization lives here, in the warehouse.
-- Every read the MCP server makes about an employee goes through
-- visible_employees(caller_email). The caller email comes from the verified
-- Google sign-in, never from the model. An unmapped account gets zero rows.
--
--   self          : the caller's own record
--   direct_report : employees whose manager_id is the caller (direct only)
--   people_ops    : everyone, if identity_map.access_tier = 'people_ops'
CREATE OR REPLACE TABLE FUNCTION ${DS}.visible_employees(caller_email STRING) AS (
  WITH caller AS (
    SELECT employee_id, access_tier
    FROM ${DS}.identity_map
    WHERE LOWER(google_email) = LOWER(caller_email)
  )
  SELECT
    e.employee_id,
    CASE
      WHEN e.employee_id = c.employee_id THEN 'self'
      WHEN e.manager_id = c.employee_id THEN 'direct_report'
      ELSE 'people_ops'
    END AS access_basis
  FROM ${DS}.employees AS e
  CROSS JOIN caller AS c
  WHERE e.employee_id = c.employee_id
     OR e.manager_id = c.employee_id
     OR c.access_tier = 'people_ops'
);

-- PTO position per employee: HRIS balance, minus days already requested and
-- awaiting a decision. Not authorization-scoped by itself; only ever read
-- joined to visible_employees().
CREATE OR REPLACE VIEW ${DS}.employee_pto_position AS
SELECT
  e.employee_id,
  e.full_name,
  e.department,
  e.job_title,
  e.manager_id,
  e.hire_date,
  e.pto_balance_days,
  e.pto_used_ytd,
  COALESCE(p.pending_days, 0) AS pending_days,
  e.pto_balance_days - COALESCE(p.pending_days, 0) AS available_after_pending
FROM ${DS}.employees AS e
LEFT JOIN (
  SELECT employee_id, SUM(days_requested) AS pending_days
  FROM ${DS}.pto_requests
  WHERE status = 'pending'
  GROUP BY employee_id
) AS p USING (employee_id);
