<#
.SYNOPSIS
    Pre-execution cleanup for DDL Backup Automation.

.DESCRIPTION
    Deletes all files and folders inside the DDL exports directory,
    except for the .git folder and .gitignore file.

    This workflow ensures deleted or deprecated database objects are properly
    reflected as file deletions within the Git repository during the next
    extraction and synchronization cycle.

    The cleanup process prevents stale DDL artifacts from remaining in source
    control after objects are removed from production databases.

    Run this step BEFORE ddl_extractor.py as part of the SQL Agent job.

.PARAMETER BaseDir
    Root directory of DDL exports. Defaults to C:\DDL_Automation\ddl_exports

.EXAMPLE
    .\cleanup_ddl_exports.ps1
    .\cleanup_ddl_exports.ps1 -BaseDir "D:\CustomPath\ddl_exports"

@author       Madan U
@role         Cloud Database Administrator
@project      Database DDL Versioning Platform
@technology   PowerShell | Git | SQL Server | Operational Automation
#>

[CmdletBinding()]
param(
    [string]$BaseDir = "C:\DDL_Automation\ddl_exports"
)

# Items to always preserve (Git metadata)
$Excluded = @(".git", ".gitignore")

$Deleted = 0
$Failed  = 0

if (-not (Test-Path $BaseDir)) {
    Write-Error "Base directory not found: $BaseDir"
    exit 1
}

Write-Output "Cleanup started: $BaseDir"
Write-Output "Preserving    : $($Excluded -join ', ')"
Write-Output ("-" * 50)

Get-ChildItem -Path $BaseDir -Force |
    Where-Object { $_.Name -notin $Excluded } |
    ForEach-Object {
        $itemPath = $_.FullName
        $itemName = $_.Name

        try {
            if ($_.PSIsContainer) {
                # Use cmd /c rmdir for robustness with locked or read-only files
                $result = cmd /c "rmdir /s /q `"$itemPath`"" 2>&1
                if (Test-Path $itemPath) {
                    Write-Output "[FAILED]  $itemName — still exists after rmdir. $result"
                    $Failed++
                } else {
                    Write-Output "[DELETED] $itemName  (folder)"
                    $Deleted++
                }
            } else {
                Remove-Item -Path $itemPath -Force -ErrorAction Stop
                Write-Output "[DELETED] $itemName  (file)"
                $Deleted++
            }
        } catch {
            Write-Output "[FAILED]  $itemName — $($_.Exception.Message)"
            $Failed++
        }
    }

Write-Output ("-" * 50)
Write-Output "Deleted : $Deleted"
Write-Output "Failed  : $Failed"
Write-Output "Time    : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

if ($Failed -gt 0) {
    Write-Warning "$Failed item(s) could not be deleted. Review output above."
    exit 1
}

exit 0
