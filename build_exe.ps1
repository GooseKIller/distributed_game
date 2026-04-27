$ErrorActionPreference = "Stop"

try {
    $python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "Virtual environment Python not found at $python"
    }

    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "DistributedPlatformer" `
        --add-data "assets;assets" `
        run_game.py

    $builtExe = Join-Path $PSScriptRoot "dist\DistributedPlatformer.exe"
    $rootExe = Join-Path $PSScriptRoot "DistributedPlatformer.exe"
    if (-not (Test-Path $builtExe)) {
        throw "Built executable not found at $builtExe"
    }

    Copy-Item -LiteralPath $builtExe -Destination $rootExe -Force
    Write-Output "Executable copied to $rootExe"
}
catch {
    $message = $_.Exception.Message
    if ($message -match "DistributedPlatformer\.exe" -or $message -match "used by another process" -or $message -match "Access is denied") {
        Write-Error "Could not rebuild the EXE because DistributedPlatformer.exe is open. Close the game window, then run build_exe.ps1 again."
        exit 1
    }
    throw
}
