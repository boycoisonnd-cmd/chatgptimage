# Generate current MCP client configuration for both image providers.
#
# The server runs locally over stdio. ChatGPT and Antigravity credentials are
# read from this machine's existing OAuth stores; no API key is embedded in
# the generated JSON.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\gen_mcp_config.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\gen_mcp_config.ps1 -WriteMcpJson
#   powershell -ExecutionPolicy Bypass -File scripts\gen_mcp_config.ps1 -ApiKey aigpt_...

param(
    [switch]$WriteMcpJson,
    [string]$ApiKey = ''
)

$ErrorActionPreference = 'Stop'

# This script lives in <repo>\scripts, so the path remains valid if the repo
# is moved to another drive or machine.
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoPath = ($repoRoot -replace '\\', '/')

function New-McpServerEntry([string]$provider, [string]$apiKey) {
    $env = [ordered]@{ AIGPT_MCP_PROVIDER = $provider }
    if ($apiKey) {
        $env.AIGPT_MCP_API_KEY = $apiKey
    }
    return [ordered]@{
        type    = 'stdio'
        command = 'uv'
        args    = @('run', '--project', $repoPath, 'aigpt-mcp')
        env     = $env
    }
}

$config = [ordered]@{
    mcpServers = [ordered]@{
        'aigpt-chatgpt'     = New-McpServerEntry 'chatgpt' $ApiKey
        'aigpt-antigravity' = New-McpServerEntry 'antigravity' $ApiKey
    }
}
$configJson = $config | ConvertTo-Json -Depth 8

$keyEnv = if ($ApiKey) { " --env AIGPT_MCP_API_KEY=$ApiKey" } else { '' }
$chatgptCmd = "claude mcp add --scope user aigpt-chatgpt --env AIGPT_MCP_PROVIDER=chatgpt$keyEnv -- uv run --project $repoPath aigpt-mcp"
$antigravityCmd = "claude mcp add --scope user aigpt-antigravity --env AIGPT_MCP_PROVIDER=antigravity$keyEnv -- uv run --project $repoPath aigpt-mcp"
$genericCmd = "uv run --project $repoPath aigpt-mcp"

Write-Host ""
Write-Host "Repo: $repoPath"
Write-Host ""
Write-Host "== Claude Code: ChatGPT =="
Write-Host $chatgptCmd
Write-Host ""
Write-Host "== Claude Code: Antigravity =="
Write-Host $antigravityCmd
Write-Host ""
Write-Host "== Claude Desktop / Cursor / VS Code / Windsurf =="
Write-Host "Paste this JSON into the client's MCP configuration:"
Write-Host $configJson
Write-Host ""
Write-Host "== Generic stdio command =="
Write-Host $genericCmd
Write-Host "The provider can also be selected in the generate_image tool with provider=chatgpt or provider=antigravity."
if ($ApiKey) {
    Write-Host "The supplied API key is included in env and will be validated when the MCP process starts."
} else {
    Write-Host "No API key is required for this local stdio setup; OAuth accounts stay on this machine."
}

if ($WriteMcpJson) {
    $outPath = Join-Path (Get-Location) '.mcp.json'
    [System.IO.File]::WriteAllText(
        $outPath,
        $configJson + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Host ""
    Write-Host "Da ghi: $outPath"
}

Write-Host ""
Write-Host "Kiem tra Claude Code: claude mcp list"
exit 0
