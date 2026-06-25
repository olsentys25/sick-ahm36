# Bootstrap a Python 3.12 virtual environment for sick_ahm36 (Windows).
#
#   Right-click > Run with PowerShell, or:  .\setup.ps1
#
# Creates .venv, installs the package (editable) + dev tools, runs a quick check.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$wanted = "3.12"

function Find-Python312 {
    # Prefer the py launcher pinned to 3.12.
    try {
        $v = & py "-$wanted" -c "import sys; print(sys.version.split()[0])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -like "$wanted*") { return @("py", "-$wanted") }
    } catch {}
    # Fall back to a python3.12 on PATH.
    foreach ($name in @("python3.12", "python")) {
        try {
            $v = & $name -c "import sys; print(sys.version.split()[0])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -like "$wanted*") { return @($name) }
        } catch {}
    }
    return $null
}

$py = Find-Python312
if ($null -eq $py) {
    Write-Host "Python $wanted was not found on this machine." -ForegroundColor Yellow
    Write-Host "Install it (64-bit) from:" -ForegroundColor Yellow
    Write-Host "    https://www.python.org/downloads/release/python-3120/"
    Write-Host "During install, tick 'py launcher'. Then re-run .\setup.ps1"
    Write-Host ""
    Write-Host "Tip: avoid the Microsoft Store Python - it sandboxes file access"
    Write-Host "and can interfere with USB CAN-adapter drivers."
    exit 1
}

Write-Host "Using Python $wanted via: $($py -join ' ')" -ForegroundColor Green

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv ..."
    & $py[0] $py[1..($py.Count-1)] -m venv .venv
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "Upgrading pip and installing sick_ahm36 (editable, with dev tools) ..."
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -e ".[dev]"

Write-Host ""
Write-Host "Running tests ..." -ForegroundColor Green
& $venvPy -m pytest -q

Write-Host ""
Write-Host "Done. Activate the environment with:" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\Activate.ps1"
Write-Host "Then try the simulator:"
Write-Host "    python examples\read_speed_position.py"