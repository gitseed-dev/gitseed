# Generate or update .env from .env.example with strong cryptographic secrets on Windows PowerShell.
# Preserves any existing custom secrets in .env while replacing known weak/default placeholders.
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvExample = if ($env:GITSEED_ENV_EXAMPLE) { $env:GITSEED_ENV_EXAMPLE } else { Join-Path $RepoRoot ".env.example" }
$EnvFile = if ($env:GITSEED_ENV_FILE) { $env:GITSEED_ENV_FILE } else { Join-Path $RepoRoot ".env" }
$EnvDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($EnvFile))
$WorkspacesDir = if ($env:GITSEED_WORKSPACES_DIR) { $env:GITSEED_WORKSPACES_DIR } else { Join-Path $EnvDir "data/workspaces" }

New-Item -ItemType Directory -Force -Path $WorkspacesDir | Out-Null

function New-RandomHex([int]$ByteCount = 32) {
    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
    } else {
        New-Item -ItemType File -Path $EnvFile -Force | Out-Null
    }
}

$Lines = [System.Collections.Generic.List[string]]::new()
if (Test-Path $EnvFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        $Lines.Add($line)
    }
}

function Get-EnvVal([string]$Key) {
    $val = ""
    foreach ($line in $Lines) {
        if ($line -match "^$([regex]::Escape($Key))=(.*)$") {
            $val = $Matches[1].Trim()
        }
    }
    return $val
}

function Set-EnvVal([string]$Key, [string]$Val) {
    $found = $false
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match "^$([regex]::Escape($Key))=") {
            $Lines[$i] = "$Key=$Val"
            $found = $true
        }
    }
    if (-not $found) {
        $Lines.Add("$Key=$Val")
    }
}

$Placeholders = @(
    "",
    "changeme",
    "secret",
    "password",
    "admin",
    "gitseed-dev-secret-key-change-in-production",
    "sk-gitseed-litellm-master-key",
    "sk-gitseed-litellm-salt-key",
    "gitseed_password",
    "gitseed_valkey_password",
    "gitseed_admin"
)

if ($Placeholders -contains (Get-EnvVal "GITSEED_SECRET_KEY")) {
    Set-EnvVal "GITSEED_SECRET_KEY" (New-RandomHex 32)
}
if ($Placeholders -contains (Get-EnvVal "LITELLM_MASTER_KEY")) {
    Set-EnvVal "LITELLM_MASTER_KEY" ("sk-gitseed-" + (New-RandomHex 24))
}
if ($Placeholders -contains (Get-EnvVal "LITELLM_SALT_KEY")) {
    Set-EnvVal "LITELLM_SALT_KEY" ("sk-gitseed-salt-" + (New-RandomHex 24))
}
$CurPg = Get-EnvVal "POSTGRES_PASSWORD"
if ($Placeholders -contains $CurPg) {
    $NewPg = New-RandomHex 24
    Set-EnvVal "POSTGRES_PASSWORD" $NewPg
    $PgUser = Get-EnvVal "POSTGRES_USER"
    if (-not $PgUser) { $PgUser = "gitseed" }
    $PgDb = Get-EnvVal "POSTGRES_DB"
    if (-not $PgDb) { $PgDb = "gitseed" }
    try {
        $running = docker inspect -f '{{.State.Running}}' gitseed-postgresql 2>$null
        if ($running -eq "true") {
            docker exec gitseed-postgresql psql -U $PgUser -d $PgDb -c "ALTER USER `"$PgUser`" WITH PASSWORD '$NewPg';" *> $null
        }
    } catch {}
}
$CurValkey = Get-EnvVal "VALKEY_PASSWORD"
if ($Placeholders -contains $CurValkey) {
    $NewValkey = New-RandomHex 24
    Set-EnvVal "VALKEY_PASSWORD" $NewValkey
    $CurRedisUrl = Get-EnvVal "REDIS_URL"
    if ($CurRedisUrl -in @("", "redis://valkey:6379/0", "redis://:gitseed_valkey_password@valkey:6379/0")) {
        Set-EnvVal "REDIS_URL" "redis://:$NewValkey@valkey:6379/0"
    }
    try {
        $runningValkey = docker inspect -f '{{.State.Running}}' gitseed-valkey 2>$null
        if ($runningValkey -eq "true") {
            docker exec gitseed-valkey valkey-cli CONFIG SET requirepass $NewValkey *> $null
        }
    } catch {}
}
if ($Placeholders -contains (Get-EnvVal "GITSEED_ADMIN_PASSWORD")) {
    Set-EnvVal "GITSEED_ADMIN_PASSWORD" (New-RandomHex 16)
}

[System.IO.File]::WriteAllLines($EnvFile, $Lines, (New-Object System.Text.UTF8Encoding($false)))

$AdminEmail = Get-EnvVal "GITSEED_ADMIN_EMAIL"
if (-not $AdminEmail) { $AdminEmail = "admin@gitseed.local" }
$AdminPass = Get-EnvVal "GITSEED_ADMIN_PASSWORD"

Write-Host "Initialized $EnvFile with strong secrets."
Write-Host "Initial Admin Login: $AdminEmail / $AdminPass"
