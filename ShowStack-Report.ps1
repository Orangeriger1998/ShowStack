# Prints what ShowStack shows, after a fresh scan, so a change to showstack.json, tool-catalog.json or a project
# can be checked without the window.  Usage: powershell -NoProfile -ExecutionPolicy Bypass -File ShowStack-Report.ps1
Import-Module (Join-Path $PSScriptRoot 'ShowStack.psm1') -Force
$status = Get-DevStatus -Inventory (Update-DevInventory)
'TOOLS'
$status.Tools | Format-Table Name, Category, State, Version, Detail -AutoSize | Out-String -Width 200
'SERVICES AND SERVERS'
$status.Rows | Format-Table Section, Group, Name, State, Action, Detail -AutoSize | Out-String -Width 240
if ($status.Hidden) { "$($status.Hidden) server(s) hidden" }
