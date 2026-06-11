$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Has($Name) { $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }

if (-not (Has py) -and -not (Has python)) {
    winget install -e --id Python.Python.3.13
}
if (-not (Has soffice) -and -not (Test-Path "$env:ProgramFiles\LibreOffice\program\soffice.exe")) {
    winget install -e --id TheDocumentFoundation.LibreOffice
}

if (Has py) {
    py -3 -m venv .venv
} elseif (Has python) {
    python -m venv .venv
} elseif (Test-Path "$env:LocalAppData\Programs\Python\Python313\python.exe") {
    & "$env:LocalAppData\Programs\Python\Python313\python.exe" -m venv .venv
} else {
    throw "Python was installed. Open a new PowerShell window and run this script again."
}

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host "Done. Run: .\.venv\Scripts\python.exe parasha_generator.py 2026-08-29"
