#!/usr/bin/env python3
"""
@file        sqlserver-ddl-export-engine.py
@description Enterprise Multi-Server Database DDL Extraction & Schema Versioning Engine

Workflow:
  1. Load centralized SQL Server inventory from configuration source
  2. Authenticate to target SQL Server instances using secure environment credentials
  3. Enumerate accessible user databases across all target servers
  4. Extract structured DDL definitions for database objects
  5. Generate version-controlled SQL export files organized by:
        Server → Database → Object Type
  6. Capture operational execution statistics and extraction metadata
  7. Log extraction status to centralized operational logging table
  8. Prepare structured output for downstream Git synchronization workflows

Extracted Object Types:
  - Tables
  - Primary Keys
  - Unique Constraints
  - Foreign Keys
  - Check Constraints
  - Indexes
  - Views
  - Stored Procedures
  - Functions
  - Triggers

Operational Features:
  - Multi-server parallel extraction support
  - Structured DDL export hierarchy
  - Environment-based credential management
  - Centralized execution logging
  - Permission-aware database handling
  - Batch-based extraction processing
  - Production-safe error handling
  - Operational reporting support

Output Structure:
  ddl_exports/<ApplicationName_IP>/<Database>/<TYPE__schema__object.sql>

Execution Type :
  Scheduled Enterprise DDL Extraction Workflow

Target Purpose :
  Centralized database schema version tracking, operational auditability,
  infrastructure governance, and Git-based DDL lifecycle management.

Security Practices:
  - No hardcoded credentials
  - Environment-variable authentication
  - Centralized secret management compatible
  - Least-privilege operational design

@author       Madan U
@role         Cloud Database Administrator
@project      Database DDL Versioning Platform
@technology   Python | SQL Server | pyodbc | GitOps | Operational Automation
@scope        Enterprise Database Governance & Schema Change Tracking
"""

#!/usr/bin/env python3
"""
ddl_extractor.py

Multi-server DDL Extraction Engine
====================================
Extracts DDL (Data Definition Language) objects from multiple SQL Server instances
and writes them as structured .sql files organized by server → database → object.

Supports:
  - Tables (with columns, data types, identity, defaults, computed columns)
  - Primary Keys, Unique Constraints, Foreign Keys, Check Constraints
  - Indexes (clustered, nonclustered, unique, with INCLUDE columns)
  - Views, Stored Procedures, Functions (scalar/table/inline), Triggers

Output layout:
  ddl_exports/<AppName_IP>/<DatabaseName>/<TYPE__schema__object.sql>

Credentials are loaded from environment variables — never hardcoded.
See .env.example for required variables.

Author : Madan U — Associate Cloud DBA
Context: Centralized schema versioning across 250+ SQL Server instances
"""

import pyodbc
import os
import logging
import time
from datetime import datetime
import concurrent.futures
import sys
from collections import defaultdict

# ─────────────────────────────────────────────
# CREDENTIALS — loaded from environment variables
# Set these via your scheduler, .env file, or secrets manager
# ─────────────────────────────────────────────
SQL_USERNAME = os.environ.get("DDL_SQL_USERNAME", "")
SQL_PASSWORD = os.environ.get("DDL_SQL_PASSWORD", "")

if not SQL_USERNAME or not SQL_PASSWORD:
    raise EnvironmentError(
        "SQL credentials not set. Export DDL_SQL_USERNAME and DDL_SQL_PASSWORD "
        "before running this script."
    )

# ─────────────────────────────────────────────
# CONFIGURATION — override via environment or edit below
# ─────────────────────────────────────────────
SERVERS_FILE_PATH  = os.environ.get("DDL_SERVERS_FILE", r"C:\DDL_Automation\servers.txt")
BASE_OUTPUT_DIR    = os.environ.get("DDL_OUTPUT_DIR",   r"C:\DDL_Automation\ddl_exports")
CENTRAL_LOG_DIR    = os.environ.get("DDL_LOG_DIR",      r"C:\DDL_Automation\central_logs")
CENTRAL_MGMT_SERVER = os.environ.get("DDL_MGMT_SERVER", "")   # your central SQL Server
LOGGING_DB          = "DBA_Admin"
DEFAULT_DRIVER      = "ODBC Driver 17 for SQL Server"
DEFAULT_WORKERS     = 1      # set >1 to process servers in parallel
FETCH_BATCH         = 200    # rows per fetchmany()
LOG_LEVEL           = logging.INFO

# ─────────────────────────────────────────────
# SERVER → APPLICATION NAME MAPPING
# Maps "IP,port" → human-readable application label.
# Populate this with your actual server inventory.
# Example entries shown with placeholder IPs.
# ─────────────────────────────────────────────
APP_NAME_MAPPING: dict[str, str] = {
    # "10.x.x.x,1433": "APP_NAME",
    # Add entries matching your servers.txt
}

# ─────────────────────────────────────────────
# SQL — database enumeration
# ─────────────────────────────────────────────
DB_LIST_QUERY = """
SELECT name FROM sys.databases
WHERE database_id > 4   -- exclude system DBs
  AND state = 0         -- online only
ORDER BY name;
"""

# ─────────────────────────────────────────────
# SQL — full DDL extraction per database
# One query covers all object types in a single pass.
# Uses STRING_AGG (SQL Server 2017+).
# ─────────────────────────────────────────────
SQL_PER_DB = r"""
SET NOCOUNT ON;

;WITH ColInfo AS (
    SELECT
        s.name              AS SchemaName,
        t.name              AS TableName,
        c.name              AS ColumnName,
        tp.name             AS TypeName,
        c.max_length,
        c.precision,
        c.scale,
        c.is_nullable,
        c.is_identity,
        c.column_id,
        ic.seed_value,
        ic.increment_value,
        dc.definition       AS DefaultDefinition,
        c.is_computed,
        t.object_id
    FROM sys.tables t
    JOIN sys.schemas s          ON t.schema_id   = s.schema_id
    JOIN sys.columns c          ON t.object_id   = c.object_id
    JOIN sys.types tp           ON c.system_type_id = tp.user_type_id AND tp.is_user_defined = 0
    LEFT JOIN sys.identity_columns ic ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id
    WHERE t.is_ms_shipped = 0
)
, TableCreate AS (
    SELECT SchemaName, TableName,
        'CREATE TABLE ' + QUOTENAME(SchemaName) + '.' + QUOTENAME(TableName)
            + CHAR(13)+CHAR(10) + '(' + CHAR(13)+CHAR(10)
            + STRING_AGG(
                '    ' + QUOTENAME(ColumnName) + ' '
                + CASE
                    WHEN UPPER(TypeName) IN ('NVARCHAR','NCHAR')
                        THEN UPPER(TypeName) + '(' + CASE WHEN max_length = -1 THEN 'MAX'
                             ELSE CAST(max_length/2 AS VARCHAR(10)) END + ')'
                    WHEN UPPER(TypeName) IN ('VARCHAR','CHAR','VARBINARY','BINARY')
                        THEN UPPER(TypeName) + '(' + CASE WHEN max_length = -1 THEN 'MAX'
                             ELSE CAST(max_length AS VARCHAR(10)) END + ')'
                    WHEN UPPER(TypeName) IN ('DECIMAL','NUMERIC')
                        THEN UPPER(TypeName) + '(' + CAST(precision AS VARCHAR(10))
                             + ', ' + CAST(scale AS VARCHAR(10)) + ')'
                    WHEN UPPER(TypeName) IN ('ROWVERSION','TIMESTAMP') THEN 'ROWVERSION'
                    ELSE UPPER(TypeName)
                  END
                + CASE WHEN is_identity = 1
                       THEN ' IDENTITY(' + CAST(ISNULL(seed_value,0) AS VARCHAR(20))
                            + ',' + CAST(ISNULL(increment_value,1) AS VARCHAR(20)) + ')'
                       ELSE '' END
                + CASE WHEN is_nullable = 0 THEN ' NOT NULL' ELSE ' NULL' END
                + CASE WHEN DefaultDefinition IS NOT NULL
                       THEN ' DEFAULT ' +
                            CASE WHEN LEFT(LTRIM(DefaultDefinition),1) = '('
                                      AND RIGHT(RTRIM(DefaultDefinition),1) = ')'
                                      AND CHARINDEX('(', DefaultDefinition, 2) = 0
                                 THEN SUBSTRING(DefaultDefinition, 2, LEN(DefaultDefinition)-2)
                                 ELSE DefaultDefinition END
                       ELSE '' END
              , ',' + CHAR(13)+CHAR(10)) WITHIN GROUP (ORDER BY column_id)
            + CHAR(13)+CHAR(10) + ');'  AS DDL
    FROM ColInfo
    WHERE is_computed = 0
    GROUP BY SchemaName, TableName
)
, KeyDDL AS (
    SELECT kc.SchemaName, kc.TableName, kc.ConstraintName, kc.ConstraintType,
        'ALTER TABLE ' + QUOTENAME(kc.SchemaName) + '.' + QUOTENAME(kc.TableName)
            + ' ADD CONSTRAINT ' + QUOTENAME(kc.ConstraintName) + ' '
            + CASE WHEN kc.ConstraintType = 'PRIMARY_KEY_CONSTRAINT'
                   THEN 'PRIMARY KEY' ELSE 'UNIQUE' END
            + ' (' + STRING_AGG(QUOTENAME(kc2.ColumnName), ', ')
                     WITHIN GROUP (ORDER BY kc2.key_ordinal) + ')'  AS DDL
    FROM (
        SELECT sch.name AS SchemaName, t.name AS TableName,
               kc.name AS ConstraintName, kc.type_desc AS ConstraintType,
               kc.parent_object_id, kc.unique_index_id
        FROM sys.key_constraints kc
        JOIN sys.tables t   ON kc.parent_object_id = t.object_id
        JOIN sys.schemas sch ON t.schema_id = sch.schema_id
        WHERE t.is_ms_shipped = 0
    ) kc
    JOIN (
        SELECT kc.name AS ConstraintName, sch.name AS SchemaName, t.name AS TableName,
               ic.key_ordinal, COL_NAME(ic.object_id, ic.column_id) AS ColumnName
        FROM sys.key_constraints kc
        JOIN sys.tables t   ON kc.parent_object_id = t.object_id
        JOIN sys.schemas sch ON t.schema_id = sch.schema_id
        JOIN sys.index_columns ic
            ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
    ) kc2 ON kc2.ConstraintName = kc.ConstraintName
         AND kc2.SchemaName = kc.SchemaName
         AND kc2.TableName  = kc.TableName
    GROUP BY kc.SchemaName, kc.TableName, kc.ConstraintName, kc.ConstraintType
)
, FKDDL AS (
    SELECT SchemaName, TableName, FKName,
        'ALTER TABLE ' + QUOTENAME(SchemaName) + '.' + QUOTENAME(TableName)
            + ' ADD CONSTRAINT ' + QUOTENAME(FKName)
            + ' FOREIGN KEY (' + STRING_AGG(QUOTENAME(ParentCol), ', ')
                                  WITHIN GROUP (ORDER BY constraint_column_id) + ')'
            + ' REFERENCES ' + QUOTENAME(RefSchema) + '.' + QUOTENAME(RefTable)
            + ' (' + STRING_AGG(QUOTENAME(RefCol), ', ')
                     WITHIN GROUP (ORDER BY constraint_column_id) + ')' AS DDL
    FROM (
        SELECT fk.object_id AS FK_ObjectId,
               sch.name AS SchemaName, parent.name AS TableName, fk.name AS FKName,
               ref_sch.name AS RefSchema, ref_tbl.name AS RefTable,
               pcol.name AS ParentCol, rcol.name AS RefCol,
               fkc.constraint_column_id
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables parent  ON fkc.parent_object_id   = parent.object_id
        JOIN sys.schemas sch    ON parent.schema_id        = sch.schema_id
        JOIN sys.tables ref_tbl ON fkc.referenced_object_id = ref_tbl.object_id
        JOIN sys.schemas ref_sch ON ref_tbl.schema_id      = ref_sch.schema_id
        JOIN sys.columns pcol   ON fkc.parent_object_id    = pcol.object_id
                                AND fkc.parent_column_id   = pcol.column_id
        JOIN sys.columns rcol   ON fkc.referenced_object_id = rcol.object_id
                                AND fkc.referenced_column_id = rcol.column_id
        WHERE parent.is_ms_shipped = 0
    ) x
    GROUP BY SchemaName, TableName, FKName, RefSchema, RefTable
)
, CheckDDL AS (
    SELECT sch.name AS SchemaName, t.name AS TableName, cc.name AS ConstraintName,
        'ALTER TABLE ' + QUOTENAME(sch.name) + '.' + QUOTENAME(t.name)
            + ' ADD CONSTRAINT ' + QUOTENAME(cc.name) + ' CHECK ' + cc.definition AS DDL
    FROM sys.check_constraints cc
    JOIN sys.tables t   ON cc.parent_object_id = t.object_id
    JOIN sys.schemas sch ON t.schema_id = sch.schema_id
    WHERE t.is_ms_shipped = 0
)
, IndexDDL AS (
    SELECT k.SchemaName, k.TableName, k.IndexName,
        'CREATE '
            + CASE WHEN k.is_unique = 1 THEN 'UNIQUE ' ELSE '' END
            + CASE WHEN k.type_desc LIKE '%CLUSTERED%' THEN 'CLUSTERED ' ELSE 'NONCLUSTERED ' END
            + 'INDEX ' + QUOTENAME(k.IndexName)
            + ' ON ' + QUOTENAME(k.SchemaName) + '.' + QUOTENAME(k.TableName)
            + ' (' + k.KeyColumns + ')'
            + CASE WHEN ic.IncludedColumns IS NOT NULL
                   THEN ' INCLUDE (' + ic.IncludedColumns + ')' ELSE '' END AS DDL
    FROM (
        SELECT s.name AS SchemaName, t.name AS TableName, i.name AS IndexName,
               i.index_id, i.is_unique, i.type_desc,
               STRING_AGG(QUOTENAME(COL_NAME(ic.object_id, ic.column_id)), ', ')
                   WITHIN GROUP (ORDER BY ic.key_ordinal, ic.index_column_id) AS KeyColumns
        FROM sys.indexes i
        JOIN sys.tables t   ON i.object_id = t.object_id
        JOIN sys.schemas s  ON t.schema_id = s.schema_id
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        WHERE i.is_hypothetical = 0
          AND t.is_ms_shipped   = 0
          AND i.is_primary_key  = 0
          AND ic.is_included_column = 0
        GROUP BY s.name, t.name, i.name, i.index_id, i.is_unique, i.type_desc
    ) k
    LEFT JOIN (
        SELECT s.name AS SchemaName, t.name AS TableName, i.name AS IndexName,
               STRING_AGG(QUOTENAME(COL_NAME(ic.object_id, ic.column_id)), ', ')
                   WITHIN GROUP (ORDER BY ic.index_column_id) AS IncludedColumns
        FROM sys.indexes i
        JOIN sys.tables t   ON i.object_id = t.object_id
        JOIN sys.schemas s  ON t.schema_id = s.schema_id
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        WHERE ic.is_included_column = 1
          AND t.is_ms_shipped = 0
        GROUP BY s.name, t.name, i.name
    ) ic ON ic.SchemaName = k.SchemaName AND ic.TableName = k.TableName
         AND ic.IndexName  = k.IndexName
)
, ModuleDDL AS (
    SELECT
        CASE o.type
            WHEN 'V'  THEN 'VIEW'
            WHEN 'P'  THEN 'PROCEDURE'
            WHEN 'FN' THEN 'FUNCTION'
            WHEN 'TF' THEN 'FUNCTION'
            WHEN 'IF' THEN 'FUNCTION'
            WHEN 'TR' THEN 'TRIGGER'
            ELSE 'MODULE'
        END AS type_desc,
        sch.name AS SchemaName,
        o.name   AS ObjectName,
        ISNULL(m.definition, '/* encrypted or not available */') AS DDL
    FROM sys.objects o
    JOIN sys.schemas sch ON o.schema_id = sch.schema_id
    LEFT JOIN sys.sql_modules m ON o.object_id = m.object_id
    WHERE o.type IN ('V','P','FN','TF','IF','TR')
      AND o.is_ms_shipped = 0
)

SELECT DB_NAME() AS DatabaseName, ObjectType, SchemaName, ObjectName, DDL
FROM (
    SELECT 'TABLE'       AS ObjectType, SchemaName, TableName AS ObjectName, DDL FROM TableCreate
    UNION ALL SELECT 'PK_UNIQUE',   SchemaName, TableName, DDL FROM KeyDDL
    UNION ALL SELECT 'FOREIGN_KEY', SchemaName, TableName, DDL FROM FKDDL
    UNION ALL SELECT 'CHECK',       SchemaName, TableName, DDL FROM CheckDDL
    UNION ALL SELECT 'INDEX',       SchemaName, TableName, DDL FROM IndexDDL
    UNION ALL SELECT type_desc,     SchemaName, ObjectName, DDL FROM ModuleDDL
) A
ORDER BY DatabaseName, SchemaName, ObjectType, ObjectName;
"""

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
OBJTYPE_PREFIX = {
    "TABLE": "TABLE", "PK_UNIQUE": "PK", "FOREIGN_KEY": "FK",
    "CHECK": "CHK",   "INDEX": "IDX",    "VIEW": "VW",
    "PROCEDURE": "SP","FUNCTION": "FN",  "TRIGGER": "TR", "MODULE": "MOD",
}

def sanitize(name: str) -> str:
    if not name:
        return "unknown_object"
    for ch in ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '\n', '\r', '\t']:
        name = name.replace(ch, '_')
    return name.replace(" ", "_").strip()

def object_filename(objtype: str, schema: str, objname: str) -> str:
    prefix = OBJTYPE_PREFIX.get(objtype, objtype)
    raw = f"{prefix}__{sanitize(schema)}__{sanitize(objname)}.sql"
    return raw[:196] + ".sql" if len(raw) > 200 else raw

def get_conn_str(server: str, driver: str, dbname: str = None) -> str:
    base = (
        f"DRIVER={{{driver}}};SERVER={server};"
        f"UID={SQL_USERNAME};PWD={SQL_PASSWORD};"
    )
    return base if not dbname else base + f"DATABASE={dbname};"

# ─────────────────────────────────────────────
# CENTRAL DB LOGGING
# Logs extraction result to a management table.
# Uses Windows Auth on localhost (avoids transmitting credentials).
# ─────────────────────────────────────────────
def log_to_central_db(server_ip: str, backup_status: str, remarks: str,
                       driver: str, logger: logging.Logger) -> None:
    sql = """
        INSERT INTO DDL_Git_Sync_Logs (ServerIP, BackupStatus, GitStatus, Remarks, ExecutionTime)
        VALUES (?, ?, NULL, ?, GETDATE());
    """
    conn_str = (
        f"Driver={{{driver}}};"
        "Server=localhost;"
        f"Database={LOGGING_DB};"
        "Trusted_Connection=yes;"
    )
    try:
        with pyodbc.connect(conn_str, timeout=10) as conn:
            conn.autocommit = True
            conn.cursor().execute(sql, (server_ip, backup_status, remarks))
        logger.info("[DDL_Git_Sync_Logs] Logged %s → %s", server_ip, backup_status)
    except Exception as e:
        logger.error("[DDL_Git_Sync_Logs] Logging failed for %s: %s", server_ip, e)

# ─────────────────────────────────────────────
# PER-SERVER PROCESSOR
# ─────────────────────────────────────────────
def process_server(server: str, driver: str, base_output: str,
                    logger: logging.Logger) -> dict:
    server_start = time.time()

    app_name      = APP_NAME_MAPPING.get(server)
    clean_ip      = server.split(',')[0]
    combined_name = f"{app_name}_{clean_ip}" if app_name else clean_ip
    safe_server   = sanitize(combined_name)

    server_dir = os.path.join(base_output, safe_server)
    os.makedirs(server_dir, exist_ok=True)
    logger.info("=== SERVER START: %s → %s ===", server, combined_name)

    processed = 0
    failures:  list[tuple[str, str]] = []
    per_db_stats:       dict = {}
    server_type_totals: dict = defaultdict(int)
    server_total_objects = 0
    db_order: list[str] = []
    backup_status = "Success"

    try:
        with pyodbc.connect(get_conn_str(server, driver), timeout=10) as conn:
            cur = conn.cursor()
            cur.execute(DB_LIST_QUERY)
            db_list = [r[0] for r in cur.fetchall()]
        logger.info("[%s] Databases found: %s", server, db_list)

        for db in db_list:
            db_order.append(db)
            safe_db  = sanitize(db)
            db_start = time.time()
            db_dir   = os.path.join(server_dir, safe_db)
            os.makedirs(db_dir, exist_ok=True)

            objects_written = 0
            per_type_counts: dict = defaultdict(int)

            try:
                with pyodbc.connect(get_conn_str(server, driver, db),
                                    autocommit=True, timeout=30) as conn:
                    cur = conn.cursor()
                    cur.execute(SQL_PER_DB)
                    while True:
                        rows = cur.fetchmany(FETCH_BATCH)
                        if not rows:
                            break
                        for r in rows:
                            try:
                                dbname, objtype, schema, objname, ddl = r
                            except Exception:
                                logger.exception("[%s][%s] Unexpected row: %s", server, db, r)
                                continue

                            ddl = ddl or "/* <no DDL provided> */"
                            fname    = object_filename(objtype, schema, objname)
                            obj_path = os.path.join(db_dir, fname)

                            with open(obj_path, "w", encoding="utf-8", newline='\n') as f:
                                f.write(f"-- DDL Export\n")
                                f.write(f"-- Server  : {server}\n")
                                f.write(f"-- Database: {db}\n")
                                f.write(f"-- Type    : {objtype}\n")
                                f.write(f"-- Object  : {schema}.{objname}\n\n")
                                f.write(f"USE [{db}];\nGO\n\n")
                                f.write(ddl.rstrip() + "\n\nGO\n")

                            objects_written += 1
                            per_type_counts[objtype]    += 1
                            server_type_totals[objtype] += 1
                            server_total_objects        += 1

                elapsed_db = time.time() - db_start
                logger.info("[%s][%s] Done: %d objects (%.1fs)", server, db, objects_written, elapsed_db)
                processed += 1
                per_db_stats[db] = {
                    "status": "OK", "objects_written": objects_written,
                    "by_type": dict(per_type_counts),
                    "elapsed_seconds": round(elapsed_db, 2),
                }

            except Exception as e:
                logger.exception("[%s][%s] Error: %s", server, db, e)
                failures.append((db, str(e)))
                backup_status = "Failed"
                per_db_stats[db] = {
                    "status": "ERROR", "objects_written": objects_written,
                    "by_type": dict(per_type_counts),
                    "elapsed_seconds": round(time.time() - db_start, 2),
                    "error": str(e),
                }

    except Exception as e:
        logger.exception("[%s] SERVER ERROR: %s", server, e)
        failures.append(("SERVER", str(e)))
        backup_status = "Failed"

    # Write per-server README
    elapsed     = time.time() - server_start
    readme_path = os.path.join(server_dir, "README.txt")
    try:
        with open(readme_path, "w", encoding="utf-8", newline='\n') as rf:
            rf.write(f"DDL Extraction — {combined_name}\n")
            rf.write(f"{'='*50}\n")
            rf.write(f"Server        : {server}\n")
            rf.write(f"Finished      : {datetime.now().isoformat()}\n")
            rf.write(f"Elapsed (s)   : {round(elapsed,2)}\n")
            rf.write(f"Total objects : {server_total_objects}\n\n")
            rf.write("Per-database breakdown:\n")
            for db in db_order:
                info = per_db_stats.get(db, {})
                rf.write(f"\n  [{db}]  status={info.get('status')}  "
                         f"objects={info.get('objects_written',0)}\n")
                for t, c in sorted(info.get("by_type", {}).items(), key=lambda x: -x[1]):
                    rf.write(f"    {t}: {c}\n")
            rf.write("\nServer totals by type:\n")
            for t, c in sorted(server_type_totals.items(), key=lambda x: -x[1]):
                rf.write(f"  {t}: {c}\n")
    except Exception as e:
        logger.exception("[%s] README write failed: %s", server, e)

    # Log outcome to central management table
    remarks = (
        f"DBs processed: {processed}, total objects: {server_total_objects}."
        if not failures else
        f"Completed with errors. OK: {processed}, Failed: {len(failures)}, "
        f"objects: {server_total_objects}."
    )
    log_to_central_db(server, backup_status, remarks, driver, logger)
    logger.info("=== SERVER DONE: %s  status=%s  objects=%d ===",
                server, backup_status, server_total_objects)

    return {
        "server": server, "processed": processed,
        "failures": failures, "output_dir": server_dir,
        "readme": readme_path, "status": backup_status,
    }

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    if not os.path.isfile(SERVERS_FILE_PATH):
        sys.exit(f"ERROR: servers.txt not found at: {SERVERS_FILE_PATH}")

    servers = [
        line.strip()
        for line in open(SERVERS_FILE_PATH, encoding="utf-8")
        if line.strip() and not line.startswith("#")
    ]
    if not servers:
        sys.exit(f"ERROR: No servers found in {SERVERS_FILE_PATH}")

    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    os.makedirs(CENTRAL_LOG_DIR, exist_ok=True)

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    central_log = os.path.join(CENTRAL_LOG_DIR, f"multi_server_{timestamp}.log")

    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(central_log, encoding="utf-8")]
    )
    logger = logging.getLogger("ddl_extractor")
    logger.info("Starting DDL extraction — servers: %d", len(servers))

    results = []
    if DEFAULT_WORKERS == 1:
        for s in servers:
            results.append(process_server(s, DEFAULT_DRIVER, BASE_OUTPUT_DIR, logger))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as exe:
            futures = {exe.submit(process_server, s, DEFAULT_DRIVER,
                                  BASE_OUTPUT_DIR, logger): s for s in servers}
            for fut in concurrent.futures.as_completed(futures):
                s = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    logger.exception("Server task %s crashed: %s", s, e)
                    results.append({"server": s, "processed": 0,
                                    "failures": [("__executor__", str(e))],
                                    "status": "Failed"})

    total_dbs      = sum(r.get("processed", 0) for r in results)
    total_failures = sum(len(r.get("failures", [])) for r in results)
    logger.info("Extraction complete — servers: %d  dbs_ok: %d  failures: %d",
                len(servers), total_dbs, total_failures)

    print("\nPer-server summary:")
    for r in results:
        print(f"  [{r.get('status','?'):7s}] {r['server']:30s}  "
              f"dbs={r['processed']}  failures={len(r['failures'])}")

if __name__ == "__main__":
    main()
