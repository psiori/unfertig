$ErrorActionPreference = 'Stop'
try {
    Write-Host "`nunfertig - first-time setup"
    $BoardUv = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
    if (-not $BoardUv -and (Test-Path "$HOME\.local\bin\uv.exe")) { $BoardUv = "$HOME\.local\bin\uv.exe" }
    if (-not $BoardUv -and (Test-Path "$HOME\.cargo\bin\uv.exe")) { $BoardUv = "$HOME\.cargo\bin\uv.exe" }
    if (-not $BoardUv) {
        Write-Host 'Installing uv for your user account from https://astral.sh/uv/install.ps1'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'uv installation failed.' }
        $BoardUv = "$HOME\.local\bin\uv.exe"
        if (-not (Test-Path $BoardUv)) { throw 'uv may be in a custom location. Open a new terminal and run this installer again.' }
    }
    & $BoardUv python install 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python setup failed. Check your internet connection and retry.' }
    & $BoardUv run --no-project --python 3.12 --script (Join-Path $PSScriptRoot 'server.py') --configure-port
    if ($LASTEXITCODE -ne 0) { throw 'Board validation failed. See the message above.' }
    Write-Host "`nReady! Double-click start.bat to open your board."
} catch {
    Write-Host "Setup failed: $_" -ForegroundColor Red
    exit 1
}
