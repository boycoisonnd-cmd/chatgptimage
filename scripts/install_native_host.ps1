# Installs the GPT Image Studio native messaging host for the current user.
# No admin needed: everything lives under HKCU.
#
# Registers:
#   HKCU\Software\Google\Chrome\NativeMessagingHosts\com.aigpt.launcher
#         -> path to the host manifest JSON (written under extension\native_host)
#
# Idempotent. Re-run to refresh after a path or extension-id change.
# Chrome must be restarted after (re)installing the host.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install_native_host.ps1

$ErrorActionPreference = 'Stop'

# ---- locate repo root (this script lives in <repo>\scripts) ----
$repoRoot = Split-Path -Parent $PSScriptRoot
$nativeDir = Join-Path $repoRoot 'extension\native_host'
$manifestPath = Join-Path $nativeDir 'com.aigpt.launcher.json'
$extManifest = Join-Path $repoRoot 'extension\manifest.json'

# ---- resolve the stable extension id ----
$extId = $null
$idFile = Join-Path $repoRoot 'extension_id.txt'
if (Test-Path $idFile) {
    $extId = (Get-Content $idFile -Raw).Trim()
}
if (-not $extId -and (Test-Path $extManifest)) {
    # Derive from the manifest "key": Chrome id = first 16 bytes of SHA-256 of
    # the DER SPKI, each byte -> two chars over alphabet a-p.
    $manifest = Get-Content $extManifest -Raw | ConvertFrom-Json
    if ($manifest.key) {
        $bytes = [Convert]::FromBase64String($manifest.key)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $digest = $sha.ComputeHash($bytes)
        $sb = New-Object System.Text.StringBuilder
        for ($i = 0; $i -lt 16; $i++) {
            [void]$sb.Append([char]([int][char]'a' + ($digest[$i] -shr 4)))
            [void]$sb.Append([char]([int][char]'a' + ($digest[$i] -band 0x0F)))
        }
        $extId = $sb.ToString()
    }
}
if (-not $extId) {
    Write-Error "Cannot determine extension id: no extension_id.txt and no 'key' in extension\manifest.json"
    exit 1
}

# ---- write the host manifest (UTF-8, no BOM) ----
if (-not (Test-Path $nativeDir)) {
    New-Item -ItemType Directory -Path $nativeDir -Force | Out-Null
}
$hostBat = Join-Path $nativeDir 'host.bat'
$hostJson = [ordered]@{
    name             = 'com.aigpt.launcher'
    description      = 'Launches the aigpt-api local server for GPT Image Studio'
    path             = $hostBat
    type             = 'stdio'
    allowed_origins  = @("chrome-extension://$extId/")
}
$json = $hostJson | ConvertTo-Json
[System.IO.File]::WriteAllText($manifestPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $manifestPath"

# ---- register the HKCU key ----
$regKey = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.aigpt.launcher'
New-Item -Path $regKey -Force | Out-Null
Set-ItemProperty -Path $regKey -Name '(default)' -Value $manifestPath

Write-Host "Registered native messaging host: $regKey"
Write-Host "Extension id: $extId"
Write-Host "Manifest:      $manifestPath"
Write-Host ""
Write-Host "Restart Chrome for the host to take effect."
exit 0
