# Creates the virtual environment (.venv) and installs the dependencies.
# Usage:  .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
}
if (-not $python) {
    throw "Python 3.10+ was not found on PATH. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment in .venv ..."
    & $python -m venv .venv
}
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Installing dependencies from requirements.txt ..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

& $venvPython -c "import cv2, numpy, PIL, tkinter; print('OK: OpenCV', cv2.__version__, '- NumPy', numpy.__version__, '- Pillow', PIL.__version__)"
Write-Host "Setup complete. Start the application with .\run.ps1"
