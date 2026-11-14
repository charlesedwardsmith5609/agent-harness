# Cross-model review: send the current diff to a different AI provider for an independent review.
# Usage: pwsh -NoProfile -File scripts/cross-review.ps1 [-Provider openai|anthropic] [-Model NAME] [-Base REF]
#        [-Include PATH,PATH]
# Only tracked changes are sent. An untracked file is sent only when you name it with -Include.
# Paths matching the deny list below (.env*, keys, credentials) are never sent, even if named.
# The request is scanned with gitleaks first. If gitleaks is missing or fails, nothing is sent.
# scripts/cross-review.sh must keep the same deny list and behavior.
# Env:   OPENAI_API_KEY / ANTHROPIC_API_KEY, optional REVIEW_PROVIDER, REVIEW_MODEL,
#        OPENAI_BASE_URL, ANTHROPIC_BASE_URL
param(
    [ValidateSet('openai', 'anthropic')]
    [string]$Provider = $(if ($env:REVIEW_PROVIDER) { $env:REVIEW_PROVIDER } else { 'openai' }),
    [string]$Model = $env:REVIEW_MODEL,
    [string]$Base = 'HEAD',
    [string[]]$Include = @()
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if ($Provider -eq 'openai') {
    if (-not $Model) { $Model = 'gpt-6.1-sol' }
    $key = $env:OPENAI_API_KEY
    $root = if ($env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL } else { 'https://api.openai.com' }
    $url = "$root/v1/chat/completions"
} else {
    if (-not $Model) { $Model = 'claude-sonnet-5-5' }
    $key = $env:ANTHROPIC_API_KEY
    $root = if ($env:ANTHROPIC_BASE_URL) { $env:ANTHROPIC_BASE_URL } else { 'https://api.anthropic.com' }
    $url = "$root/v1/messages"
}
if (-not $key) { Write-Error "Missing API key for $Provider"; exit 2 }

function Fail([string]$Message, [int]$Code) { [Console]::Error.WriteLine($Message); exit $Code }

# Deny list (keep identical to scripts/cross-review.sh). Base names, then directories.
$denyNames = @('.env*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', '*.kdbx', 'id_rsa*', 'id_dsa*',
    'id_ecdsa*', 'id_ed25519*', '*credential*', '.netrc', '.npmrc', '.pypirc', '.git-credentials', '*.tfstate', '*.tfvars')
$denyDirs = @('.aws', '.ssh', '.gnupg')
function Test-Denied([string]$Path) {
    $p = '/' + ($Path -replace '\\', '/')
    foreach ($pat in $denyNames) { if ($p -like "*/$pat") { return $true } }
    foreach ($pat in $denyDirs) { if ($p -like "*/$pat/*") { return $true } }
    return $false
}
$excludes = @(':(exclude)go.sum', ':(exclude)**/go.sum', ':(exclude)package-lock.json', ':(exclude)**/package-lock.json')
foreach ($pat in $denyNames) { $excludes += ":(exclude,glob)**/$pat" }
foreach ($pat in $denyDirs) { $excludes += ":(exclude,glob)**/$pat/**" }

# Named untracked files, validated before anything is read.
$root = (Get-Location).ProviderPath
$includeFiles = @()
foreach ($f in $Include) {
    if (Test-Denied $f) { Fail "Refusing -Include $f: it matches the secret-file deny list. Nothing was sent." 2 }
    $item = Get-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
    if (-not $item -or $item.PSIsContainer -or $item.LinkType) { Fail "-Include ${f}: not a regular file (symlinks are refused)." 2 }
    $full = $item.FullName
    if (-not $full.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) { Fail "-Include ${f}: outside the repository." 2 }
    $rel = $full.Substring($root.Length + 1) -replace '\\', '/'
    if (Test-Denied $rel) { Fail "Refusing -Include $f: it matches the secret-file deny list. Nothing was sent." 2 }
    & git ls-files --error-unmatch -- $rel 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { continue }   # tracked: already in the diff
    $includeFiles += $rel
}

& git rev-parse -q --verify "$Base^{commit}" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "unknown base: $Base" 2 }
# Tracked changes only. This does not touch the index, so untracked files stay untracked.
function Get-ReviewDiff([string]$Ref) {
    (& git diff --no-color $Ref -- . @excludes | Out-String)
}
$diff = Get-ReviewDiff $Base
# Nothing uncommitted and no -Base given: review the last commit instead.
if (-not $diff.Trim() -and -not $PSBoundParameters.ContainsKey('Base') -and $includeFiles.Count -eq 0) {
    & git rev-parse -q --verify HEAD~1 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $Base = 'HEAD~1'
        Write-Host "No uncommitted changes; reviewing the last commit: $(& git log -1 --format='%h %s')" -ForegroundColor Cyan
        Write-Host "(If the milestone spans several commits, rerun with -Base REF.)" -ForegroundColor DarkGray
        $diff = Get-ReviewDiff $Base
    }
}
foreach ($rel in $includeFiles) {
    $diff += (& git diff --no-color --no-index -- /dev/null $rel | Out-String)
}
if (-not $diff.Trim()) { Write-Host "No changes against $Base. Nothing to review."; exit 0 }
$max = 60000
if ($diff.Length -gt $max) {
    Write-Warning "Diff is $($diff.Length) chars; truncating to $max."
    $diff = $diff.Substring(0, $max)
}

$system = Get-Content -Raw -Encoding UTF8 'skills/review-checklist.md'
$user = "Review this diff. Apply the security-review pass: SSRF, information exposure, and secret handling.`n`n``````diff`n$diff`n``````"

# Secret scan before anything leaves this machine. Fails closed: a finding, a missing gitleaks,
# or a scanner failure all refuse the review and nothing is sent.
if (-not (Get-Command gitleaks -ErrorAction SilentlyContinue)) {
    Write-Error "gitleaks is not installed, so the secret scan cannot run. Nothing was sent."
    exit 1
} else {
    $ErrorActionPreference = 'Continue'   # native stderr must not throw while we capture it
    # `gitleaks stdin` is the current form; older 8.x releases only have `detect --pipe`.
    $useStdin = (& gitleaks stdin --help 2>&1 | Out-String) -match 'gitleaks stdin'
    $gl = if ($useStdin) { @('stdin') } else { @('detect', '--pipe') }
    $scan = $user | & gitleaks @gl --no-banner --no-color --redact --verbose --exit-code 7 2>&1 | Out-String
    $scanExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($scanExit -ne 0) {
        Write-Host (($scan -split "`r?`n" | Select-Object -Last 25) -join "`n")
        if ($scanExit -eq 7) {
            Write-Error "Secret scan found possible secrets (redacted above). Nothing was sent. Remove them from the change and rerun."
        } else {
            Write-Error "Secret scan failed (gitleaks exit $scanExit). Nothing was sent."
        }
        exit 1
    }
}

if ($Provider -eq 'openai') {
    $req = @{ model = $Model; messages = @(@{ role = 'system'; content = $system }, @{ role = 'user'; content = $user }) }
    $headers = @{ Authorization = "Bearer $key" }
} else {
    $req = @{ model = $Model; max_tokens = 2000; system = $system; messages = @(@{ role = 'user'; content = $user }) }
    $headers = @{ 'x-api-key' = $key; 'anthropic-version' = '2023-06-01' }
}
$json = $req | ConvertTo-Json -Depth 6
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)

Write-Host "Requesting review from $Provider ($Model)..." -ForegroundColor Cyan
try {
    $resp = Invoke-RestMethod -Method Post -Uri $url -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $bytes
} catch {
    Write-Error "API call failed: $($_.Exception.Message) $($_.ErrorDetails.Message)"
    exit 1
}

if ($Provider -eq 'openai') {
    $review = $resp.choices[0].message.content
} else {
    $review = ($resp.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join ''
}

New-Item -ItemType Directory -Force -Path '.reviews' | Out-Null
$out = ".reviews/review-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$Provider.md"
"# Cross-model review ($Provider, $Model, base $Base)`n`n$review" | Set-Content -Encoding UTF8 $out
Write-Host $review
Write-Host "`nSaved to $out" -ForegroundColor DarkGray
