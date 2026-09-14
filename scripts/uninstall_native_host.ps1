# Removes the GPT Image Studio native messaging host registration for the
# current user. Idempotent; safe to run when not installed.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\uninstall_native_host.ps1

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repoRoot 'extension\native_host\com.aigpt.launcher.json'
$regKey = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.aigpt.launcher'

if (Test-Path $regKey) {
    Remove-Item -Path $regKey -Force
    Write-Host "Removed registry key: $regKey"
} else {
    Write-Host "Registry key not present (already uninstalled): $regKey"
}

if (Test-Path $manifestPath) {
    Remove-Item -Path $manifestPath -Force
    Write-Host "Removed manifest: $manifestPath"
} else {
    Write-Host "Manifest not present: $manifestPath"
}

Write-Host "Restart Chrome if it is running."
exit 0
