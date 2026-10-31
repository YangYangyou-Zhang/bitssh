$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

# A running app keeps the output tree's exe locked, which makes PyInstaller's
# clean step fail. Stop both the shell and its backend (same image name).
Write-Host "== stop running bitssh =="
Get-Process bitssh -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

Write-Host "== frontend build =="
Push-Location (Join-Path $root "frontend")
try {
    node "node_modules\typescript\bin\tsc" --noEmit -p tsconfig.json
    if (-not $?) { throw "tsc failed" }
    node "node_modules\vite\bin\vite.js" build
    if (-not $?) { throw "vite build failed" }
} finally {
    Pop-Location
}

$distRoot = $env:BITSSH_DIST_ROOT
if (-not $distRoot) {
    $distRoot = Join-Path (Split-Path -Parent $root) "bitssh-dist"
}
$dist = Join-Path $distRoot "win"
if (Test-Path -LiteralPath $dist) {
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        try {
            Remove-Item -LiteralPath $dist -Recurse -Force
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (Test-Path -LiteralPath $dist) {
        throw "cannot remove $dist; close anything using it and retry"
    }
}
New-Item -ItemType Directory -Force -Path $dist | Out-Null

Write-Host "== pyinstaller -> $dist =="
& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $root "build") (Join-Path $PSScriptRoot "bitssh.spec")
if (-not $?) { throw "pyinstaller failed" }

$exe = Join-Path $dist "bitssh\bitssh.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "missing output: $exe" }

Write-Host "== distribution zip =="
$zip = Join-Path $dist "bitssh-win-x64.zip"
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
& tar.exe -a -c -f $zip -C $dist bitssh
if (-not $?) { throw "failed to create $zip" }
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -LiteralPath (Join-Path $dist "sha256sums.txt") -Value "$zipHash  bitssh-win-x64.zip" -Encoding ascii

$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "bitssh.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($shortcut)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = Split-Path -Parent $exe
$lnk.IconLocation = "$exe,0"
$lnk.Save()

Write-Host "== done =="
Write-Host "exe      : $exe"
Write-Host "shortcut : $shortcut"
Write-Host ""
Write-Host "Build output is kept outside the source tree (bitssh-dist\win)."
