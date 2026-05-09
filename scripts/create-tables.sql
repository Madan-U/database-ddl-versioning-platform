-- =============================================================================
-- DDL Versioning Platform — Core Metadata Tables
-- =============================================================================
-- Database  : DBA_Tools
-- Purpose   : Centralized inventory and execution tracking for
--              automated SQL Server DDL extraction and Git versioning.
--
-- Recommended Usage:
--   • Run once on the central management SQL Server
--   • Used by:
--         - sqlserver-ddl-export-engine.py
--         - secure-git-sync-engine.py
--         - export-directory-cleanup.ps1
--
-- Author    : Madan U
-- Role      : Cloud Database Administrator
-- =============================================================================

USE DBA_Tools;
GO

-- =============================================================================
-- TABLE 1: dbo.SQLServer_Instance_Inventory
-- =============================================================================
-- Central inventory of managed SQL Server instances.
--
-- This table acts as the authoritative source for all servers included
-- in automated DDL extraction workflows.
--
-- The extraction engine dynamically reads this inventory to determine:
--   • Which servers to connect to
--   • Environment classification
--   • High Availability topology
--   • Backup participation rules
--
-- Benefits:
--   • Eliminates hardcoded server lists
--   • Simplifies onboarding/offboarding
--   • Enables scalable centralized operations
-- =============================================================================

IF NOT EXISTS (
    SELECT 1
    FROM sys.tables
    WHERE name = 'SQLServer_Instance_Inventory'
      AND SCHEMA_NAME(schema_id) = 'dbo'
)
BEGIN

    CREATE TABLE dbo.SQLServer_Instance_Inventory
    (
        -- ─────────────────────────────────────────────────────────────
        -- Identity
        -- ─────────────────────────────────────────────────────────────
        ServerID                INT             NOT NULL IDENTITY(1,1),

        -- ─────────────────────────────────────────────────────────────
        -- Connection Information
        -- ─────────────────────────────────────────────────────────────
        ServerAddress           NVARCHAR(100)   NOT NULL,
        Port                    INT             NOT NULL DEFAULT 1433,
        ListenerName            NVARCHAR(150)   NULL,

        -- ─────────────────────────────────────────────────────────────
        -- Server Classification
        -- ─────────────────────────────────────────────────────────────
        ServerName              NVARCHAR(100)   NULL,
        ApplicationName         NVARCHAR(100)   NOT NULL,

        EnvironmentType         NVARCHAR(20)    NOT NULL
            CHECK (EnvironmentType IN
                ('PROD','UAT','DEV','QA','STAGING','DR')),

        DataCenter              NVARCHAR(50)    NULL,

        -- ─────────────────────────────────────────────────────────────
        -- High Availability Metadata
        -- ─────────────────────────────────────────────────────────────
        HAConfiguration         NVARCHAR(30)    NULL
            CHECK (HAConfiguration IN
                ('AlwaysOn','LogShipping','Standalone','FCI', NULL)),

        AvailabilityGroupName   NVARCHAR(100)   NULL,

        ReplicaRole             NVARCHAR(20)    NULL
            CHECK (ReplicaRole IN
                ('PRIMARY','SECONDARY', NULL)),

        -- ─────────────────────────────────────────────────────────────
        -- SQL Server Details
        -- ─────────────────────────────────────────────────────────────
        SQLVersion              NVARCHAR(20)    NULL,
        SQLEdition              NVARCHAR(50)    NULL,

        -- ─────────────────────────────────────────────────────────────
        -- Hosting Metadata
        -- ─────────────────────────────────────────────────────────────
        HostingPlatform         NVARCHAR(20)    NULL
            CHECK (HostingPlatform IN
                ('OnPrem','AWS','Azure','GCP', NULL)),

        OperatingSystem         NVARCHAR(20)    NULL DEFAULT 'Windows',

        -- ─────────────────────────────────────────────────────────────
        -- Operational Controls
        -- ─────────────────────────────────────────────────────────────
        IsActive                BIT             NOT NULL DEFAULT 1,
        IncludeInDDLExtraction  BIT             NOT NULL DEFAULT 1,
        ProcessingPriority      INT             NOT NULL DEFAULT 100,

        -- ─────────────────────────────────────────────────────────────
        -- Notes / Documentation
        -- ─────────────────────────────────────────────────────────────
        OperationalNotes        NVARCHAR(MAX)   NULL,

        -- ─────────────────────────────────────────────────────────────
        -- Audit Columns
        -- ─────────────────────────────────────────────────────────────
        CreatedAt               DATETIME        NOT NULL DEFAULT GETDATE(),
        UpdatedAt               DATETIME        NOT NULL DEFAULT GETDATE(),

        -- ─────────────────────────────────────────────────────────────
        -- Constraints
        -- ─────────────────────────────────────────────────────────────
        CONSTRAINT PK_SQLServer_Instance_Inventory
            PRIMARY KEY (ServerID),

        CONSTRAINT UQ_SQLServer_Instance_Inventory
            UNIQUE (ServerAddress, Port)
    );

    -- =====================================================================
    -- Operational Indexes
    -- =====================================================================

    CREATE NONCLUSTERED INDEX IX_SQLServer_Inventory_Active
        ON dbo.SQLServer_Instance_Inventory
        (
            IsActive,
            IncludeInDDLExtraction,
            ProcessingPriority
        )
        INCLUDE
        (
            ServerAddress,
            Port,
            ApplicationName,
            EnvironmentType
        );

    CREATE NONCLUSTERED INDEX IX_SQLServer_Inventory_App_Env
        ON dbo.SQLServer_Instance_Inventory
        (
            ApplicationName,
            EnvironmentType
        );

    PRINT 'Created: dbo.SQLServer_Instance_Inventory';

END
ELSE
BEGIN
    PRINT 'Already exists: dbo.SQLServer_Instance_Inventory';
END
GO


-- =============================================================================
-- TABLE 2: dbo.DDL_Extraction_RunLog
-- =============================================================================
-- Centralized execution tracking table.
--
-- One row is written per execution cycle and server.
--
-- Used for:
--   • Operational monitoring
--   • Failure tracking
--   • Git synchronization visibility
--   • Alerting workflows
--   • Audit history
--
-- Updated by:
--   • sqlserver-ddl-export-engine.py
--   • secure-git-sync-engine.py
-- =============================================================================

IF NOT EXISTS (
    SELECT 1
    FROM sys.tables
    WHERE name = 'DDL_Extraction_RunLog'
      AND SCHEMA_NAME(schema_id) = 'dbo'
)
BEGIN

    CREATE TABLE dbo.DDL_Extraction_RunLog
    (
        -- ─────────────────────────────────────────────────────────────
        -- Identity
        -- ─────────────────────────────────────────────────────────────
        LogID                   INT             NOT NULL IDENTITY(1,1),

        -- ─────────────────────────────────────────────────────────────
        -- Server Context
        -- ─────────────────────────────────────────────────────────────
        ServerAddress           NVARCHAR(100)   NOT NULL,
        ApplicationName         NVARCHAR(100)   NULL,

        -- ─────────────────────────────────────────────────────────────
        -- Extraction Results
        -- ─────────────────────────────────────────────────────────────
        ExtractionStatus        NVARCHAR(20)    NULL
            CHECK (ExtractionStatus IN
                ('Success','Failed', NULL)),

        DatabasesProcessed      INT             NULL DEFAULT 0,
        ObjectsExtracted        INT             NULL DEFAULT 0,
        ExtractionErrors        INT             NULL DEFAULT 0,

        -- ─────────────────────────────────────────────────────────────
        -- Git Synchronization Results
        -- ─────────────────────────────────────────────────────────────
        GitSyncStatus           NVARCHAR(20)    NULL
            CHECK (GitSyncStatus IN
                ('Success','Failed','No Changes', NULL)),

        FilesChanged            INT             NULL DEFAULT 0,

        -- ─────────────────────────────────────────────────────────────
        -- Execution Notes
        -- ─────────────────────────────────────────────────────────────
        ExecutionRemarks        NVARCHAR(MAX)   NULL,

        -- ─────────────────────────────────────────────────────────────
        -- Execution Timing
        -- ─────────────────────────────────────────────────────────────
        RunDate                 DATE            NOT NULL
            DEFAULT CAST(GETDATE() AS DATE),

        ExecutionTime           DATETIME        NOT NULL
            DEFAULT GETDATE(),

        DurationSeconds         INT             NULL,

        -- ─────────────────────────────────────────────────────────────
        -- Constraints
        -- ─────────────────────────────────────────────────────────────
        CONSTRAINT PK_DDL_Extraction_RunLog
            PRIMARY KEY (LogID)
    );

    -- =====================================================================
    -- Monitoring & Alerting Index
    -- =====================================================================

    CREATE NONCLUSTERED INDEX IX_DDL_Extraction_RunLog_RunDate
        ON dbo.DDL_Extraction_RunLog
        (
            RunDate DESC,
            ExtractionStatus,
            GitSyncStatus
        )
        INCLUDE
        (
            ServerAddress,
            ApplicationName,
            ExecutionRemarks
        );

    PRINT 'Created: dbo.DDL_Extraction_RunLog';

END
ELSE
BEGIN
    PRINT 'Already exists: dbo.DDL_Extraction_RunLog';
END
GO


-- =============================================================================
-- SAMPLE DATA (Generic / Sanitized)
-- =============================================================================
-- Example inventory entries for demonstration purposes only.
-- Replace with actual infrastructure details in production.
-- =============================================================================
/*
INSERT INTO dbo.SQLServer_Instance_Inventory
(
    ServerAddress,
    Port,
    ApplicationName,
    EnvironmentType,
    DataCenter,
    HAConfiguration,
    ReplicaRole,
    SQLVersion,
    HostingPlatform,
    IsActive,
    IncludeInDDLExtraction,
    OperationalNotes
)
VALUES
(
    '192.168.10.10',
    1433,
    'OrderManagement',
    'PROD',
    'DC1',
    'AlwaysOn',
    'PRIMARY',
    'SQL Server 2019',
    'OnPrem',
    1,
    1,
    'Primary replica for DDL extraction'
),
(
    '192.168.10.20',
    1433,
    'RiskAnalytics',
    'PROD',
    'DC2',
    'Standalone',
    NULL,
    'SQL Server 2022',
    'AWS',
    1,
    1,
    'Standalone analytics workload'
),
(
    '192.168.10.30',
    1433,
    'ReportingWarehouse',
    'UAT',
    'DC1',
    'LogShipping',
    'PRIMARY',
    'SQL Server 2019',
    'Azure',
    1,
    1,
    'UAT reporting environment'
);
*/
GO
