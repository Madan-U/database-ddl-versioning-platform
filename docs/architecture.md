# Architecture & Design Decisions

## Why This Architecture?

### Central Execution Model

All extraction runs from a single management SQL Server that has linked server connectivity to every target instance. This avoids:
- Deploying agents on 250+ servers
- Managing credentials distributed across the fleet
- Network ACL complexity (one server needs outbound access, not all)

### Cleanup-Before-Extract Pattern

The most common alternative — differential/incremental export — has a critical flaw: it can't detect *deletions*. If a table or stored procedure is dropped, an incremental approach leaves the old `.sql` file in the repo indefinitely.

The cleanup pattern solves this cleanly:
1. Delete all exported files (preserve `.git` and `.gitignore`)
2. Regenerate everything from live databases
3. Git diff naturally shows additions, modifications, AND deletions

### One SQL Query Per Database

Instead of calling `sp_helptext` per object or using SMO/PowerShell:

- **Catalog-driven** — reads directly from `sys.tables`, `sys.columns`, `sys.indexes`, etc.
- **Single pass** — one query extracts all object types per database using CTEs
- **Reconstructs TABLE DDL** — SQL Server doesn't store CREATE TABLE text; it must be built from system views
- **Handles edge cases** — `NVARCHAR(MAX)`, identity seeds/increments, multi-column constraints, included index columns

### File Per Object (Not Per Database)

Each object gets its own `.sql` file. This makes Git diffs surgical — a change to one stored procedure doesn't touch any other file. DBAs can review changes at object granularity.

### Ephemeral GitHub Tokens

Classic PATs (Personal Access Tokens) have two problems at org scale:
- They're long-lived — a leaked token gives persistent access
- Org-level PAT policies often restrict or block classic PATs

GitHub App installation tokens solve both:
- 1-hour lifespan (we use them for a single push and discard)
- App-level permissions scoped to exactly one repo
- No PAT policy friction

Vault AppRole adds another layer: the Role ID and Secret ID are injected at runtime, not stored in code or config files.

## Sequence Diagram

```
Daily / 12 Hour
   │
   ▼
SQL Agent Job
   │
   ├──[Step 1]──► export-directory-cleanup.ps1
   │               rm -rf ddl_exports/* (keep .git, .gitignore)
   │
   ├──[Step 2]──► sqlserver-ddl-export-engine.py
   │               for each server in servers.txt:
   │                 connect → list databases → run SQL_PER_DB
   │                 write ddl_exports/<App>_<IP>/<DB>/<object>.sql
   │                 log result → DBA_Admin.dbo.DDL_Git_Sync_Logs
   │
   ├──[Step 3]──► secure-git-sync-engine.py
   │               Vault AppRole login → get client token
   │               pull GitHub App ID + RSA key from Vault KV
   │               sign JWT (10 min window)
   │               fetch installation token (scoped, ~1 hr)
   │               git add . → git commit → git push origin master
   │               update GitStatus in DDL_Git_Sync_Logs
   │
   └──[Step 4]──► Alert step
                   SELECT failures from DDL_Git_Sync_Logs (today)
                   IF failures > 0: send HTML email to DBA team
```

## Log Table Schema

```sql
DDL_Git_Sync_Logs
├── LogID         INT IDENTITY PK
├── ServerIP      NVARCHAR(100)    -- "10.x.x.x,1433"
├── BackupStatus  NVARCHAR(50)     -- 'Success' | 'Failed'
├── GitStatus     NVARCHAR(50)     -- 'Success' | 'Failed' | 'No Changes'
├── Remarks       NVARCHAR(MAX)    -- human-readable summary
└── ExecutionTime DATETIME         -- indexed for date-range queries
```

## Concurrency Model

`DEFAULT_WORKERS = 1` by default. The extractor supports parallel server processing via `ThreadPoolExecutor` — increasing `DEFAULT_WORKERS` processes multiple servers simultaneously.

At `WORKERS = 1`, the bottleneck is database query time, not connection overhead. For 250 servers this completes within the nightly maintenance window at the current configuration.

## Error Isolation

Each server and each database is wrapped in independent try/except blocks. A timeout or query failure on one database does not stop extraction for other databases on the same server, or other servers entirely. Failures are:
1. Logged to `DDL_Git_Sync_Logs` with the error message in Remarks
2. Written as a `__FAILED__<timestamp>.sql` file in the affected DB folder for manual review
3. Captured in the per-server `README.txt`
4. Surfaced in the nightly email alert
