# Generates MCP client config pointing at THIS repo's aigpt-mcp server.
# Run it on any machine where the repo lives - the path is derived, never
# hardcoded, so the config works after copying/moving the repo elsewhere.
#
# Prints to stdout:
#   - Claude Code command (claude mcp add ...)
#   - Claude Desktop JSON block (mcpServers)
#   - Generic command for other clients (Cursor, VS Code, Windsurf, ...)
#
# Optional:  -WriteMcpJson   also writes a .mcp.json into the CURRENT directory
#                            (run from another project's folder to get its
#                            config ready in one step).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\gen_mcp_config.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\gen_mcp_config.ps1 -WriteMcpJson
#
# No admin needed. Idempotent.

param(
    [switch]$WriteMcpJson
)

$ErrorActionPreference = 'Stop'

# ---- locate repo root (this script lives in <repo>\scripts) ----
$repoRoot = Split-Path -Parent $PSScriptRoot

# Forward slashes so the path matches what uv/claude expect on Windows.
$repoPath = ($repoRoot -replace '\\', '/')

$claudeCodeCmd  = "claude mcp add aigpt -- uv run --project $repoPath aigpt-mcp"
$genericCmd     = "uv run --project $repoPath aigpt-mcp"

$claudeDesktopJson = @{
    mcpServers = @{
        aigpt = @{
            command = 'uv'
            args    = @('run', '--project', $repoPath, 'aigpt-mcp')
        }
    }
} | ConvertTo-Json -Depth 4

Write-Host ""
Write-Host "Repo (derived, no hardcode): $repoPath"
Write-Host ""

Write-Host "== Claude Code (chay tu folder project khac) =="
Write-Host $claudeCodeCmd
Write-Host "   Kiem tra: claude mcp list"
Write-Host ""

Write-Host "== Claude Desktop (Settings -> Developer -> Edit Config) =="
Write-Host $claudeDesktopJson
Write-Host ""

Write-Host "== Client khac (Cursor, VS Code, Windsurf...) - stdin/stdio =="
Write-Host $genericCmd
Write-Host ""

if ($WriteMcpJson) {
    $outPath = Join-Path (Get-Location) '.mcp.json'
    $json = @{
        mcpServers = @{
            aigpt = @{
                command = 'uv'
                args    = @('run', '--project', $repoPath, 'aigpt-mcp')
            }
        }
    } | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($outPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Da ghi: $outPath"
}

Write-Host ""
Write-Host "Mac dinh (tu folder project khac):"
Write-Host "  claude mcp add aigpt -- uv run --project $repoPath aigpt-mcp"
exit 0
