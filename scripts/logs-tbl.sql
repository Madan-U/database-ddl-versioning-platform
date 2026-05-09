-- =====================================================
-- DDL_Git_Sync_Logs — Central Execution Log Table
-- =====================================================
-- Database : DBA_Admin
-- Purpose  : Tracks every DDL extraction + Git sync run
--            for auditing, alerting, and trend analysis.
--
-- Created by: Madan U — Associate Cloud DBA
-- =====================================================

USE <Your DB Name>;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'DDL_Git_Sync_Logs'
      AND SCHEMA_NAME(schema_id) = 'dbo'
)
BEGIN
    CREATE TABLE dbo.DDL_Git_Sync_Logs (
        LogID         INT           NOT NULL IDENTITY(1,1) PRIMARY KEY,
        ServerIP      NVARCHAR(100) NOT NULL,
        BackupStatus  NVARCHAR(50)  NOT NULL,   -- 'Success' | 'Failed'
        GitStatus     NVARCHAR(50)  NULL,        -- 'Success' | 'Failed' | 'No Changes' (updated by git_sync.py)
        Remarks       NVARCHAR(MAX) NULL,
        ExecutionTime DATETIME      NOT NULL DEFAULT GETDATE()
    );

    -- Index for date-range queries used by the alerting job step
    CREATE NONCLUSTERED INDEX IX_DDL_Git_Sync_Logs_ExecutionTime
        ON dbo.DDL_Git_Sync_Logs (ExecutionTime DESC);

    PRINT 'Table created: dbo.DDL_Git_Sync_Logs';
END
ELSE
BEGIN
    PRINT 'Table already exists: dbo.DDL_Git_Sync_Logs';
END
GO

-- ─────────────────────────────────────────────────────
-- Useful queries for the DBA team
-- ─────────────────────────────────────────────────────

-- Today's run summary
-- SELECT * FROM dbo.DDL_Git_Sync_Logs
-- WHERE CAST(ExecutionTime AS DATE) = CAST(GETDATE() AS DATE)
-- ORDER BY ExecutionTime DESC;

-- Failed servers in the last 7 days
-- SELECT ServerIP, BackupStatus, GitStatus, Remarks, ExecutionTime
-- FROM dbo.DDL_Git_Sync_Logs
-- WHERE ExecutionTime >= DATEADD(DAY, -7, GETDATE())
--   AND (BackupStatus = 'Failed' OR GitStatus = 'Failed')
-- ORDER BY ExecutionTime DESC;

-- Daily success rate over last 30 days
-- SELECT
--     CAST(ExecutionTime AS DATE) AS RunDate,
--     COUNT(*)                    AS TotalServers,
--     SUM(CASE WHEN BackupStatus = 'Success' THEN 1 ELSE 0 END) AS Successful,
--     SUM(CASE WHEN BackupStatus = 'Failed'  THEN 1 ELSE 0 END) AS Failed
-- FROM dbo.DDL_Git_Sync_Logs
-- WHERE ExecutionTime >= DATEADD(DAY, -30, GETDATE())
-- GROUP BY CAST(ExecutionTime AS DATE)
-- ORDER BY RunDate DESC;
