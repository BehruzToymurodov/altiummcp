# Build the installable Claude Desktop extension: dist\altium-mcp-unified-<version>.mcpb
#
#   powershell -ExecutionPolicy Bypass -File scripts\pack.ps1
#
# Needs Node.js (npx). The packer is pinned so a build never changes because a
# new packer release came out. Files listed in .mcpbignore are left out.
param([string]$Out = "")
$ErrorActionPreference = "Stop"
$Packer = "@anthropic-ai/mcpb@2.1.2"

$root = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content (Join-Path $root "manifest.json") -Raw | ConvertFrom-Json
if (-not $Out) { $Out = Join-Path $root ("dist\" + $manifest.name + "-" + $manifest.version + ".mcpb") }
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null

Push-Location $root
try {
    npx --yes $Packer validate manifest.json
    if ($LASTEXITCODE -ne 0) { throw "manifest.json did not validate" }
    npx --yes $Packer pack . $Out
    if ($LASTEXITCODE -ne 0) { throw "packing failed" }
} finally {
    Pop-Location
}
Write-Host "Packed $Out"
