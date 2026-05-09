# Database DDL Versioning Platform

**Automated schema version control for large-scale SQL Server infrastructure**

Extracts DDL from 250+ SQL Server instances nightly, organizes output by server and database, and pushes structured `.sql` files to a central Git repository — giving DBAs a complete, auditable history of every schema change across the fleet.

Built and operated at AngelOne (a high-frequency stock trading platform) as part of a centralized DBA tooling initiative.

---

## The Problem

At scale, schema drift is silent and expensive. When a stored procedure changes, an index gets dropped, or a table definition shifts across environments, there is no automatic record of it. Manual DDL backups are inconsistent and non-comparable.

This system solves that: every schema object on every server is captured nightly and version-controlled, making changes visible as Git diffs.

---

## Architecture

```
Central SQL Server (Management Node)
│
├─ SQL Server Agent Job: "(dba) DDL Backup & Git Sync Daily / 12 Hour"
│   │
│   ├─ Step 1: Cleanup         cleanup_ddl_exports.ps1
│   │           Wipes ddl_exports/ (preserving .git) so deleted DB
│   │           objects appear as deletions in Git after regeneration
│   │
│   ├─ Step 2: DDL Extraction  ddl_extractor.py
│   │           Reads server list → connects to each SQL Server →
│   │           extracts all DDL → writes structured .sql files
│   │           Logs result to DBA_Admin.dbo.DDL_Git_Sync_Logs
│   │
│   ├─ Step 3: Git Sync        git_sync.py
│   │           Authenticates via Vault AppRole →
│   │           fetches short-lived GitHub App token →
│   │           commits + pushes all changes to master
│   │           Updates GitStatus in log table
│   │
│   └─ Step 4: Alert
│               Queries log table for failures →
│               sends HTML email to DBA team if any found
│
└─ DBA_Admin.dbo.DDL_Git_Sync_Logs
    Audit table — every run logged with status, remarks, timestamp
```

**Infrastructure scale:**
- 250+ SQL Server instances across two datacenters (Mumbai, Hyderabad)
- Multiple application clusters (Application_1, Application_2, etc)
- Centralized execution via management node with linked server connectivity

---

## Output Structure

```
ddl_exports/
└── <AppName>_<IP>/              e.g. NSE_CASH_10.x.x.x
    ├── README.txt               per-server extraction summary
    └── <DatabaseName>/
        ├── TABLE__dbo__Orders.sql
        ├── PK__dbo__Orders.sql
        ├── IDX__dbo__Orders__IX_OrderDate.sql
        ├── FK__dbo__OrderLines__Orders.sql
        ├── SP__dbo__usp_GetOrdersByClient.sql
        ├── VW__dbo__vw_ActiveOrders.sql
        └── FN__dbo__fn_CalcBrokerage.sql
```

**Object type prefixes:**

| Prefix | Type |
|--------|------|
| `TABLE` | Table definition |
| `PK` | Primary Key / Unique Constraint |
| `FK` | Foreign Key |
| `CHK` | Check Constraint |
| `IDX` | Index |
| `VW` | View |
| `SP` | Stored Procedure |
| `FN` | Function |
| `TR` | Trigger |

---

## DDL Coverage

A single T-SQL query per database extracts all object types in one pass using CTEs and `STRING_AGG` (SQL Server 2017+):

- **Tables** — full column definitions including data types, precision/scale, identity seeds, inline defaults, nullability
- **Keys** — primary keys, unique constraints with column lists and ordering
- **Foreign Keys** — multi-column FK references with correct column mapping
- **Check Constraints** — all table-level check definitions
- **Indexes** — clustered/nonclustered, unique, with INCLUDE columns
- **Modules** — views, stored procedures, scalar/table/inline functions, triggers (handles encrypted modules gracefully)

---

## Security Design

| Concern | Implementation |
|---------|----------------|
| SQL credentials | Environment variables — never hardcoded. Set via SQL Agent job step or secrets manager. |
| GitHub authentication | GitHub App (not PAT). Short-lived installation tokens fetched at runtime via JWT. |
| Vault access | AppRole (Role ID + Secret ID) — credentials injected from environment, not stored in code. |
| Vault SSL | Internal CA accepted explicitly. `InsecureRequestWarning` suppressed for internal endpoints only. |
| SQL logging | Windows Authentication (localhost) — no password transmitted over the wire. |

**Threat model:** If this repository is forked or cloned publicly, no secrets are exposed. All sensitive values are in environment variables, Vault, or `servers.txt` (excluded via `.gitignore`).

---

## SQL Extraction Logic — Technical Notes

**Why one query per database instead of sp_helptext / SMO?**

`sp_helptext` truncates long definitions and requires multiple calls. SMO requires PowerShell and is significantly slower at this scale. The custom T-SQL approach:
- Reconstructs `CREATE TABLE` DDL from system catalog (not stored as text, has to be built)
- Fetches module definitions from `sys.sql_modules` in a single pass
- Handles edge cases: `NVARCHAR(MAX)`, identity columns, nullable defaults, multi-column constraints
- Uses `STRING_AGG` for correct column ordering within indexes and FK definitions

**Why cleanup before extraction?**

Re-running extraction without cleanup would leave stale files for objects that were dropped from the database. The cleanup + full-regenerate pattern ensures Git `diff` accurately shows deletions.

---

## Monitoring & Alerting

Every extraction run logs to `DBA_Admin.dbo.DDL_Git_Sync_Logs`:

```sql
-- Today's run status
SELECT ServerIP, BackupStatus, GitStatus, Remarks, ExecutionTime
FROM   dbo.DDL_Git_Sync_Logs
WHERE  CAST(ExecutionTime AS DATE) = CAST(GETDATE() AS DATE)
ORDER  BY ExecutionTime DESC;
```

The SQL Agent job's final step queries this table and sends an HTML email alert if any server shows `BackupStatus = 'Failed'` or `GitStatus = 'Failed'`.

Schema for the log table: [`sql/create_log_table.sql`](sql/create_log_table.sql)

---

## Configuration

**1. Set environment variables** — copy `.env.example` to `.env` and fill values (or inject directly into the job step):

```bash
DDL_SQL_USERNAME=your_sql_service_account
DDL_SQL_PASSWORD=your_sql_password
VAULT_URL=https://your-vault-server:8200
VAULT_ROLE_ID=your-approle-role-id
VAULT_SECRET_ID=your-approle-secret-id
GITHUB_ORG=your-org
GITHUB_REPO=your-ddl-repo
```

**2. Create `servers.txt`** — one `IP,port` per line (excluded from version control):

```
# Format: IP,port
10.x.x.x,1433
10.x.x.x,13442
```

**3. Populate `APP_NAME_MAPPING`** in `ddl_extractor.py` — maps server IPs to application names for readable folder names.

**4. Create the log table** — run `sql/create_log_table.sql` once on your management server.

**5. Set up the SQL Agent job** — three steps in order:

| Step | Type | Command |
|------|------|---------|
| 1 | PowerShell | `.\scripts\cleanup_ddl_exports.ps1` |
| 2 | CmdExec | `python C:\DDL_Automation\scripts\ddl_extractor.py` |
| 3 | CmdExec | `python C:\DDL_Automation\scripts\git_sync.py` |

---

## Dependencies

```
pyodbc>=4.0          # SQL Server connectivity
requests>=2.28       # Vault and GitHub API calls
PyJWT>=2.6           # JWT generation for GitHub App auth
cryptography>=41     # RSA private key loading
urllib3>=1.26        # HTTP (SSL warning suppression)
```

Install: `pip install pyodbc requests PyJWT cryptography`

ODBC Driver 17 for SQL Server required on the execution host.

---

## Change Visualization

Once committed, Git shows schema changes as standard diffs:

- **Red lines** — removed columns, dropped indexes, deleted procedures
- **Green lines** — new objects, added columns, modified definitions

This gives the DBA team a clear, timestamped audit trail of every schema change across the entire SQL Server fleet — without requiring any change management tooling beyond standard Git.

---

## Skills Demonstrated

- **Large-scale infrastructure automation** — multi-server DDL extraction across 250+ instances
- **Secure secrets management** — HashiCorp Vault AppRole + ephemeral GitHub App tokens
- **T-SQL systems programming** — catalog queries, CTEs, STRING_AGG, DDL reconstruction from sys views
- **Python engineering** — connection pooling, batched fetches, structured error handling, concurrent execution
- **Git integration** — programmatic commit/push, handling line-ending normalization, detecting real changes
- **Operational reliability** — audit logging, failure isolation, email alerting, README generation per run

---

*Author: Madan U — Associate Cloud DBA, AngelOne (via Ahana Systems)*  
*LinkedIn: [linkedin.com/in/madan-u-3bb24627b](https://linkedin.com/in/madan-u-3bb24627b)*
