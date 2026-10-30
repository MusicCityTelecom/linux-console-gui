param([string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\SerialDesk-Source'))
$ErrorActionPreference = 'Stop'
$SourceDir = $PSScriptRoot
& py -3 -c "import sys; assert sys.version_info >= (3,12)"
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12+ from python.org, including the Python launcher.' }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$Venv = Join-Path $InstallDir '.venv'
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    & py -3 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
$Python = Join-Path $Venv 'Scripts\python.exe'
& $Python -m pip install -r (Join-Path $SourceDir 'requirements-windows.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; the current source installation was preserved.' }
$Version = (& $Python -c "import runpy,sys; print(runpy.run_path(sys.argv[1])['VERSION'])" (Join-Path $SourceDir 'version.py')).Trim()
$Release = Join-Path $InstallDir ('version-' + $Version + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $Release | Out-Null
$Files = @('console_gui.py','serial_core.py','network_core.py','network_ui.py','updates.py','credential_vault.py','profile_ui.py','platform_support.py','version.py','serial-desk.svg','LICENSE','README.md')
foreach ($File in $Files) { Copy-Item -LiteralPath (Join-Path $SourceDir $File) -Destination $Release }
Set-Content -LiteralPath (Join-Path $Release 'INSTALL_MODE') -Value 'windows-source' -Encoding ASCII
$Shell = New-Object -ComObject WScript.Shell
$Programs = [Environment]::GetFolderPath('Programs')
$Shortcut = $Shell.CreateShortcut((Join-Path $Programs 'Serial Desk (Source).lnk'))
$Shortcut.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
$Shortcut.Arguments = '"' + (Join-Path $Release 'console_gui.py') + '"'
$Shortcut.WorkingDirectory = $Release
$Shortcut.Description = 'Serial and network console toolkit'
$Shortcut.Save()
Write-Output "Installed Serial Desk $Version. Open Serial Desk (Source) from the Start menu."
