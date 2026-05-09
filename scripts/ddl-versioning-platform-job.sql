-- =============================================================================
-- ddl-versioning-platform-job.sql
-- =============================================================================
-- Purpose :
--     Central orchestration procedure for the DDL Versioning Platform.
--
-- Responsibilities :
--     1. Execute export directory cleanup
--     2. Run SQL Server DDL extraction engine
--     3. Run secure Git synchronization engine
--     4. Trigger alerting procedure on failures
--
-- Author    : Madan U
-- Role      : Cloud Database Administrator
-- =============================================================================
USE [msdb]
GO

BEGIN TRANSACTION

DECLARE @ReturnCode INT
SELECT @ReturnCode = 0

-- ============================================================================
-- JOB CATEGORY
-- ============================================================================

IF NOT EXISTS (
    SELECT name
    FROM msdb.dbo.syscategories
    WHERE name = N'(dba) Automation & Versioning'
      AND category_class = 1
)
BEGIN

    EXEC @ReturnCode = msdb.dbo.sp_add_category
        @class = N'JOB',
        @type  = N'LOCAL',
        @name  = N'(dba) Automation & Versioning'

    IF (@@ERROR <> 0 OR @ReturnCode <> 0)
        GOTO QuitWithRollback

END

-- ============================================================================
-- CREATE JOB
-- ============================================================================

DECLARE @jobId BINARY(16)

EXEC @ReturnCode = msdb.dbo.sp_add_job
    @job_name               = N'(dba) - DDL Versioning Platform',
    @enabled                = 1,
    @notify_level_eventlog  = 0,
    @notify_level_email     = 0,
    @notify_level_netsend   = 0,
    @notify_level_page      = 0,
    @delete_level           = 0,
    @description            = N'Automated SQL Server DDL extraction, Git synchronization, and failure alerting platform.',
    @category_name          = N'(dba) Automation & Versioning',
    @owner_login_name       = N'sa',
    @job_id                 = @jobId OUTPUT

IF (@@ERROR <> 0 OR @ReturnCode <> 0)
    GOTO QuitWithRollback

-- ============================================================================
-- STEP 1 — EXECUTE ORCHESTRATOR
-- ============================================================================

EXEC @ReturnCode = msdb.dbo.sp_add_jobstep
    @job_id                = @jobId,
    @step_name             = N'Execute DDL Versioning Orchestrator',
    @step_id               = 1,
    @cmdexec_success_code  = 0,
    @on_success_action     = 1,
    @on_fail_action        = 2,
    @retry_attempts        = 0,
    @retry_interval        = 0,
    @os_run_priority       = 0,
    @subsystem             = N'TSQL',
    @command               = N'
        EXEC DBA_Tools.dbo.usp_DDL_Versioning_Orchestrator;
    ',
    @database_name         = N'DBA_Tools',
    @flags                 = 0

IF (@@ERROR <> 0 OR @ReturnCode <> 0)
    GOTO QuitWithRollback

-- ============================================================================
-- SET START STEP
-- ============================================================================

EXEC @ReturnCode = msdb.dbo.sp_update_job
    @job_id         = @jobId,
    @start_step_id  = 1

IF (@@ERROR <> 0 OR @ReturnCode <> 0)
    GOTO QuitWithRollback

-- ============================================================================
-- SCHEDULE 1 — DAILY 6:00 AM
-- ============================================================================

EXEC @ReturnCode = msdb.dbo.sp_add_jobschedule
    @job_id                    = @jobId,
    @name                      = N'Daily - 6 AM',
    @enabled                   = 1,
    @freq_type                 = 4,
    @freq_interval             = 1,
    @freq_subday_type          = 1,
    @freq_subday_interval      = 0,
    @freq_relative_interval    = 0,
    @freq_recurrence_factor    = 0,
    @active_start_date         = 20260509,
    @active_end_date           = 99991231,
    @active_start_time         = 060000,
    @active_end_time           = 235959

IF (@@ERROR <> 0 OR @ReturnCode <> 0)
    GOTO QuitWithRollback

-- ============================================================================
-- SCHEDULE 2 — DAILY 6:00 PM
-- ============================================================================

EXEC @ReturnCode = msdb.dbo.sp_add_jobschedule
    @job_id                    = @jobId,
    @name                      = N'Daily - 6 PM',
    @enabled                   = 1,
    @freq_type                 = 4,
    @freq_interval             = 1,
    @freq_subday_type          = 1,
    @freq_subday_interval      = 0,
    @freq_relative_interval    = 0,
    @freq_recurrence_factor    = 0,
    @active_start_date         = 20260509,
    @active_end_date           = 99991231,
    @active_start_time         = 180000,
    @active_end_time           = 235959

IF (@@ERROR <> 0 OR @ReturnCode <> 0)
    GOTO QuitWithRollback

-- ============================================================================
-- TARGET SERVER
-- ============================================================================

EXEC @ReturnCode = msdb.dbo.sp_add_jobserver
    @job_id      = @jobId,
    @server_name = N'(local)'

IF (@@ERROR <> 0 OR @ReturnCode <> 0)
    GOTO QuitWithRollback

COMMIT TRANSACTION

GOTO EndSave

QuitWithRollback:

    IF (@@TRANCOUNT > 0)
        ROLLBACK TRANSACTION

EndSave:
GO
