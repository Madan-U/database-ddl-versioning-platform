-- =============================================================================
-- usp_DDL_Versioning_Orchestrator
-- =============================================================================
-- Purpose :
--     Master orchestration procedure for the SQL Server
--     DDL Versioning Platform.
--
-- Workflow:
--     Phase 1 — Export directory cleanup
--     Phase 2 — SQL Server DDL extraction
--     Phase 3 — Secure Git synchronization
--     Phase 4 — Failure alerting
--
-- Scripts:
--     export-directory-cleanup.ps1
--     sqlserver-ddl-export-engine.py
--     secure-git-sync-engine.py
--
-- Author:
--     Madan U
--     Role      : Cloud Database Administrator
-- =============================================================================

USE DBA_Tools;
GO

CREATE OR ALTER PROCEDURE dbo.usp_DDL_Versioning_Orchestrator
(
      @DebugMode             BIT             = 0
    , @NotifyEmail           NVARCHAR(200)   = NULL
    , @MailProfileName       NVARCHAR(100)   = 'DBAAlerts'

    -- Runtime Paths
    , @PythonExe             NVARCHAR(300)   =
        'C:\DDL_Automation\venv\Scripts\python.exe'

    , @ExtractorScript       NVARCHAR(300)   =
        'C:\DDL_Automation\scripts\sqlserver-ddl-export-engine.py'

    , @GitSyncScript         NVARCHAR(300)   =
        'C:\DDL_Automation\scripts\secure-git-sync-engine.py'

    , @CleanupScript         NVARCHAR(300)   =
        'C:\DDL_Automation\scripts\export-directory-cleanup.ps1'

    -- Directories
    , @OutputDirectory       NVARCHAR(300)   =
        'C:\DDL_Automation\ddl_exports'

    , @LogDirectory          NVARCHAR(300)   =
        'C:\DDL_Automation\central_logs'

    -- Alerting
    , @DefaultAlertEmail     NVARCHAR(200)   =
        'dba-team@domain.com'
)
AS
BEGIN

    SET NOCOUNT ON;

    DECLARE
          @Step              NVARCHAR(100)
        , @Cmd               NVARCHAR(2000)
        , @RunDate           DATE            = CAST(GETDATE() AS DATE)
        , @StartTime         DATETIME        = GETDATE()
        , @FailureCount      INT             = 0
        , @AlertEmail        NVARCHAR(200)
        , @Subject           NVARCHAR(300)
        , @Body              NVARCHAR(MAX);

    SET @AlertEmail = ISNULL(@NotifyEmail, @DefaultAlertEmail);

    -- =========================================================================
    -- PHASE 1 — EXPORT DIRECTORY CLEANUP
    -- =========================================================================

    SET @Step = 'Phase 1 - Cleanup';

    RAISERROR(
        '[%s] Running export directory cleanup...',
        0,
        1,
        @Step
    ) WITH NOWAIT;

    SET @Cmd =
        'powershell -ExecutionPolicy Bypass -File "'
        + @CleanupScript
        + '"';

    IF @DebugMode = 1
    BEGIN
        RAISERROR(
            '[DEBUG] %s',
            0,
            1,
            @Cmd
        ) WITH NOWAIT;
    END
    ELSE
    BEGIN
        EXEC xp_cmdshell @Cmd;
    END;

    -- =========================================================================
    -- PHASE 2 — SQL SERVER DDL EXTRACTION
    -- =========================================================================

    SET @Step = 'Phase 2 - DDL Extraction';

    RAISERROR(
        '[%s] Running SQL Server DDL export engine...',
        0,
        1,
        @Step
    ) WITH NOWAIT;

    SET @Cmd =
        '"' + @PythonExe + '" "' + @ExtractorScript + '"';

    IF @DebugMode = 1
    BEGIN
        RAISERROR(
            '[DEBUG] %s',
            0,
            1,
            @Cmd
        ) WITH NOWAIT;
    END
    ELSE
    BEGIN
        EXEC xp_cmdshell @Cmd;
    END;

    -- =========================================================================
    -- PHASE 3 — SECURE GIT SYNCHRONIZATION
    -- =========================================================================

    SET @Step = 'Phase 3 - Git Synchronization';

    RAISERROR(
        '[%s] Running secure Git sync engine...',
        0,
        1,
        @Step
    ) WITH NOWAIT;

    SET @Cmd =
        '"' + @PythonExe + '" "' + @GitSyncScript + '"';

    IF @DebugMode = 1
    BEGIN
        RAISERROR(
            '[DEBUG] %s',
            0,
            1,
            @Cmd
        ) WITH NOWAIT;
    END
    ELSE
    BEGIN
        EXEC xp_cmdshell @Cmd;
    END;

    -- =========================================================================
    -- PHASE 4 — FAILURE ALERTING
    -- =========================================================================

    SET @Step = 'Phase 4 - Alerting';

    SELECT
        @FailureCount = COUNT(*)
    FROM dbo.DDL_Backup_RunLog
    WHERE RunDate = @RunDate
      AND (
            BackupStatus = 'Failed'
         OR GitStatus    = 'Failed'
      );

    IF @FailureCount > 0
    BEGIN

        RAISERROR(
            '[%s] %d failure(s) detected.',
            0,
            1,
            @Step,
            @FailureCount
        ) WITH NOWAIT;

        SET @Subject =
              'DDL Versioning Alert - '
            + CONVERT(NVARCHAR(20), @RunDate, 23)
            + ' | Failures: '
            + CAST(@FailureCount AS NVARCHAR(10));

        SET @Body =
            N'<html><body style="font-family:Segoe UI;font-size:12px;">'
            + N'<h3>DDL Versioning Platform - Failure Summary</h3>'
            + N'<table border="1" cellpadding="5" cellspacing="0">'
            + N'<tr style="background:#2c3e50;color:white;">'
            + N'<th>Server</th>'
            + N'<th>Application</th>'
            + N'<th>Backup Status</th>'
            + N'<th>Git Status</th>'
            + N'<th>Execution Time</th>'
            + N'</tr>';

        SELECT
            @Body = @Body +
            N'<tr>'
            + N'<td>' + ISNULL(ServerIP,'-') + N'</td>'
            + N'<td>' + ISNULL(ApplicationName,'-') + N'</td>'
            + N'<td>' + ISNULL(BackupStatus,'-') + N'</td>'
            + N'<td>' + ISNULL(GitStatus,'-') + N'</td>'
            + N'<td>' + CONVERT(NVARCHAR(20), ExecutionTime, 120) + N'</td>'
            + N'</tr>'
        FROM dbo.DDL_Backup_RunLog
        WHERE RunDate = @RunDate
          AND (
                BackupStatus = 'Failed'
             OR GitStatus    = 'Failed'
          );

        SET @Body = @Body + N'</table></body></html>';

        EXEC msdb.dbo.sp_send_dbmail
              @profile_name = @MailProfileName
            , @recipients   = @AlertEmail
            , @subject      = @Subject
            , @body         = @Body
            , @body_format  = 'HTML';

    END
    ELSE
    BEGIN

        RAISERROR(
            '[Phase 4] No failures detected.',
            0,
            1
        ) WITH NOWAIT;

    END;

    -- =========================================================================
    -- FINAL SUMMARY
    -- =========================================================================

    DECLARE @DurationSeconds INT;

    SET @DurationSeconds =
        DATEDIFF(SECOND, @StartTime, GETDATE());

    RAISERROR(
        '[DONE] Pipeline completed in %d seconds.',
        0,
        1,
        @DurationSeconds
    ) WITH NOWAIT;

END;
