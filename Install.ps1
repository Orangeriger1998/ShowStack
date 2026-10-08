# Puts a ShowStack shortcut on the desktop. -Startup also opens ShowStack at sign-in.
#   powershell -NoProfile -ExecutionPolicy Bypass -File Install.ps1 [-Startup]
param([switch]$Startup)

$ErrorActionPreference = 'Stop'
# Files from a downloaded ZIP carry Windows' "came from the internet" mark, which makes the launcher prompt.
Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File | Unblock-File
$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath('Desktop'))
if ($Startup) { $targets += [Environment]::GetFolderPath('Startup') }

foreach ($folder in $targets) {
    $shortcut = $shell.CreateShortcut((Join-Path $folder 'ShowStack.lnk'))
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'ShowStack.vbs') + '"'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = (Join-Path $PSScriptRoot 'assets\showstack.ico') + ',0'
    $shortcut.Description = 'Open ShowStack'
    $shortcut.Save()
    Write-Host "Shortcut created: $($shortcut.FullName)" -ForegroundColor Green
}
Write-Host 'Double-click "ShowStack" to open it. Settings: showstack.json (created on first run).'
