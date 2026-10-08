# ShowStack engine.
#   Inventory (slow, cached): which developer tools are installed (tool-catalog.json) and which localhost servers the
#   code in your project folders can start (Claude launch.json, package.json scripts, Python, Django, .NET, compose).
#   Status (fast, live): Docker, which of those servers are running, and any other dev server listening on a port.

$ErrorActionPreference = 'Stop'

$script:CatalogPath = Join-Path $PSScriptRoot 'tool-catalog.json'
$script:ConfigPath = Join-Path $PSScriptRoot 'showstack.json'
$script:StatePath = Join-Path $env:LOCALAPPDATA 'ShowStack'
$script:InventoryPath = Join-Path $script:StatePath 'inventory.json'
$script:StartedPath = Join-Path $script:StatePath 'started.json'
$script:HiddenPath = Join-Path $script:StatePath 'hidden.json'
$script:WingetPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
$script:DockerDesktopPaths = @(
    (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
)
$script:DockerRunPath = Join-Path $env:LOCALAPPDATA 'Docker\run'
# Processes that count as a dev server when one listens on a port no known server claims.
$script:DevRuntimes = 'python', 'pythonw', 'node', 'bun', 'deno', 'dotnet', 'java', 'ruby', 'php', 'uvicorn', 'hugo', 'caddy'
# Folders never worth descending into when looking for project code.
$script:SkipFolders = 'node_modules', 'venv', 'env', '__pycache__', 'dist', 'build', 'bin', 'obj', 'target', 'out',
    'coverage', 'site-packages', 'vendor', 'packages', 'tests', 'test', '__tests__', 'logs', 'tmp', 'temp'
$script:MaxScanFolders = 20000

# Carry state over from the app's earlier name (Dev Status) the first time.
$legacyStatePath = Join-Path $env:LOCALAPPDATA 'DevServers'
if (-not (Test-Path -LiteralPath $script:StatePath) -and (Test-Path -LiteralPath $legacyStatePath)) {
    Copy-Item -LiteralPath $legacyStatePath -Destination $script:StatePath -Recurse
}
New-Item -ItemType Directory -Force -Path $script:StatePath | Out-Null

# ---------------------------------------------------------------------------------------------------------------- config

function Get-DefaultConfig {
    [ordered]@{
        requiredTools         = @('Git', 'Node.js', 'Python')
        tools                 = @()
        projectRoots          = @('%USERPROFILE%\source\repos', '%USERPROFILE%\repos', '%USERPROFILE%\dev', '%USERPROFILE%\.dev',
            '%USERPROFILE%\code', '%USERPROFILE%\projects', '%USERPROFILE%\Documents\GitHub')
        includeClaudeProjects = $true
        scanDepth             = 5
        excludeFolders        = @('*backup*', '*archive*', '*.bak', 'old', 'TO BE DELETED')
        shellFolder           = '%USERPROFILE%'
        titles                = @{}
        ports                 = @{}
    }
}

function Get-PathKey {
    param([string]$Path)
    return $Path.Replace('/', '\').TrimEnd('\').ToLowerInvariant()
}

function Get-WidgetConfig {
    $defaults = Get-DefaultConfig
    if (-not (Test-Path -LiteralPath $script:ConfigPath -PathType Leaf)) {
        $defaults | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $script:ConfigPath -Encoding UTF8
    }
    $raw = Get-Content -Raw -LiteralPath $script:ConfigPath | ConvertFrom-Json
    $value = { param($name) if ($null -ne $raw.$name) { $raw.$name } else { $defaults[$name] } }

    $titles = @{}
    if ($raw.titles) {
        foreach ($property in $raw.titles.PSObject.Properties) {
            $titles[(Get-PathKey ([Environment]::ExpandEnvironmentVariables($property.Name)))] = [string]$property.Value
        }
    }
    $ports = @{}
    if ($raw.ports) {
        foreach ($property in $raw.ports.PSObject.Properties) { $ports[$property.Name.ToLowerInvariant()] = [int]$property.Value }
    }

    [pscustomobject]@{
        RequiredTools         = @(& $value 'requiredTools' | ForEach-Object { ([string]$_).ToLowerInvariant() })
        Tools                 = @(& $value 'tools')
        ProjectRoots          = @(& $value 'projectRoots' | ForEach-Object { [Environment]::ExpandEnvironmentVariables([string]$_).TrimEnd('\') })
        IncludeClaudeProjects = [bool](& $value 'includeClaudeProjects')
        ScanDepth             = [int](& $value 'scanDepth')
        ExcludeFolders        = @(& $value 'excludeFolders')
        ShellFolder           = [Environment]::ExpandEnvironmentVariables([string](& $value 'shellFolder'))
        Titles                = $titles
        Ports                 = $ports
    }
}

function Read-StateFile {
    param([string]$Path, $Default)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json } catch { }
    }
    return $Default
}

function Write-StateFile {
    param([string]$Path, $Value)
    ConvertTo-Json -InputObject $Value -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

# --------------------------------------------------------------------------------------------------------------- helpers

function Update-ProcessPath {
    # The widget runs for days; re-read PATH from the registry so tools installed since it opened are found.
    $fresh = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';'
        [Environment]::GetEnvironmentVariable('Path', 'User') -split ';'
    ) | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).Trim() } | Where-Object { $_ }
    $seen = @{}
    $merged = foreach ($entry in @($fresh) + @($env:Path -split ';')) {
        $key = Get-PathKey $entry
        if ($entry -and -not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $entry
        }
    }
    $env:Path = $merged -join ';'
}

function ConvertTo-ArgumentString {
    param([string[]]$Arguments)
    ($Arguments | ForEach-Object {
        if ($_ -eq '') { '""' }
        elseif ($_ -match '[\s"]') { '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
        else { $_ }
    }) -join ' '
}

function Start-CapturedProcess {
    # Starts a program with no window and captured output. Returns a handle for Wait-CapturedProcess.
    param([Parameter(Mandatory)] [string]$Path, [string[]]$Arguments = @(), [string]$WorkingDirectory)

    $psi = New-Object Diagnostics.ProcessStartInfo
    if ($Path -like '*.ps1') {
        $psi.FileName = 'powershell.exe'
        $Arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Path) + $Arguments
    }
    else {
        $psi.FileName = $Path
    }
    $psi.Arguments = ConvertTo-ArgumentString $Arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    $process = [Diagnostics.Process]::Start($psi)
    $process.StandardInput.Close()
    [pscustomobject]@{ Process = $process; Out = $process.StandardOutput.ReadToEndAsync(); Err = $process.StandardError.ReadToEndAsync() }
}

function Wait-CapturedProcess {
    param([Parameter(Mandatory)] $Handle, [int]$TimeoutMilliseconds = 10000)

    if (-not $Handle.Process.WaitForExit([Math]::Max(0, $TimeoutMilliseconds))) {
        Stop-ProcessTree -Id $Handle.Process.Id
        return [pscustomobject]@{ ExitCode = $null; Output = @(); TimedOut = $true }
    }
    $Handle.Process.WaitForExit()
    $text = "$($Handle.Out.Result)`n$($Handle.Err.Result)" -replace "`0", ''
    $lines = @($text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    [pscustomobject]@{ ExitCode = $Handle.Process.ExitCode; Output = $lines; TimedOut = $false }
}

function Stop-ProcessTree {
    # taskkill reports an already-exited process on stderr; under 'Stop' Windows PowerShell would make that fatal.
    param([int]$Id)
    $ErrorActionPreference = 'Continue'
    & taskkill.exe /PID $Id /T /F 2>&1 | Out-Null
}

function Invoke-Captured {
    param([Parameter(Mandatory)] [string]$Path, [string[]]$Arguments = @(), [string]$WorkingDirectory, [int]$TimeoutSeconds = 10)
    Wait-CapturedProcess -Handle (Start-CapturedProcess -Path $Path -Arguments $Arguments -WorkingDirectory $WorkingDirectory) -TimeoutMilliseconds ($TimeoutSeconds * 1000)
}

function Test-CloudOnly {
    # OneDrive and similar keep "files on demand"; touching their content downloads them. Never read those.
    param([Parameter(Mandatory)] [IO.FileSystemInfo]$Item)
    $attributes = [int]$Item.Attributes
    return [bool](($attributes -band 0x400000) -or ($attributes -band 0x40000) -or ($attributes -band 0x1000))
}

function Read-SmallText {
    param([string]$Path, [int]$MaxBytes = 262144)
    if (-not $Path) { return $null }
    $item = New-Object IO.FileInfo $Path
    if (-not $item.Exists -or $item.Length -gt $MaxBytes -or (Test-CloudOnly $item)) { return $null }
    try { return [IO.File]::ReadAllText($item.FullName) } catch { return $null }
}

# ----------------------------------------------------------------------------------------------------------------- tools

function Get-PathIndex {
    # Every program on PATH, by lower-case name, first one wins (the one Windows would run).
    Update-ProcessPath
    $index = @{}
    foreach ($dir in ($env:Path -split ';' | Where-Object { $_ })) {
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        $isStoreAliasDir = $dir -like '*\Microsoft\WindowsApps'
        foreach ($file in (Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue)) {
            if ($file.Extension -notin '.exe', '.cmd', '.bat', '.com') { continue }
            $name = $file.BaseName.ToLowerInvariant()
            # The Store's python.exe "aliases" open the Microsoft Store instead of running Python.
            if ($isStoreAliasDir -and $name -like 'python*') { continue }
            if (-not $index.ContainsKey($name)) { $index[$name] = $file.FullName }
        }
    }
    return $index
}

function Get-ToolCatalog {
    param([Parameter(Mandatory)] $Config)
    $merged = [ordered]@{}
    foreach ($tool in @((Get-Content -Raw -LiteralPath $script:CatalogPath | ConvertFrom-Json).tools) + @($Config.Tools)) {
        if ($tool -and $tool.name) { $merged[([string]$tool.name).ToLowerInvariant()] = $tool }   # config entries override the catalog
    }
    return @($merged.Values)
}

function Resolve-CatalogTool {
    param([Parameter(Mandatory)] $Tool, [Parameter(Mandatory)] [hashtable]$Index)

    foreach ($command in @($Tool.commands)) {
        if ($command -and $Index.ContainsKey(([string]$command).ToLowerInvariant())) { return $Index[([string]$command).ToLowerInvariant()] }
    }
    foreach ($path in @($Tool.paths)) {
        if (-not $path) { continue }
        $expanded = [Environment]::ExpandEnvironmentVariables([string]$path)
        if ($expanded -match '[*?]') {
            $hit = Resolve-Path -Path $expanded -ErrorAction SilentlyContinue | Sort-Object Path -Descending | Select-Object -First 1
            if ($hit) { return $hit.Path }
        }
        elseif (Test-Path -LiteralPath $expanded -PathType Leaf) {
            return $expanded
        }
    }
    return $null
}

function Get-ToolInstallCommand {
    param([Parameter(Mandatory)] $Tool)
    if ($Tool.install) { return [string]$Tool.install }
    if ($Tool.winget) { return "& '$script:WingetPath' install --id $($Tool.winget) --exact --source winget" }
    return $null
}

function Get-ToolInventory {
    # Every catalog tool that is installed (green, or yellow if it is there but does not report a version),
    # plus required tools that are missing (red). Version commands run in parallel.
    param([Parameter(Mandatory)] $Config)

    $index = Get-PathIndex
    $found = @(foreach ($tool in (Get-ToolCatalog -Config $Config)) {
        $path = Resolve-CatalogTool -Tool $tool -Index $index
        $required = ([string]$tool.name).ToLowerInvariant() -in $Config.RequiredTools
        if ($path -or $required) { [pscustomobject]@{ Tool = $tool; Path = $path; Required = $required; Handle = $null; Error = $null } }
    })

    foreach ($entry in $found) {
        if ($entry.Path -and $entry.Tool.versionArgs) {
            try { $entry.Handle = Start-CapturedProcess -Path $entry.Path -Arguments @($entry.Tool.versionArgs) }
            catch { $entry.Error = $_.Exception.Message }
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    foreach ($entry in $found) {
        $tool = $entry.Tool
        $state = 'Green'; $version = ''; $detail = ''
        if (-not $entry.Path) {
            $state = 'Red'; $detail = 'Required, but not installed'
        }
        elseif (-not $tool.versionArgs) {
            $version = (Get-Item -LiteralPath $entry.Path).VersionInfo.ProductVersion
            $detail = if ($version) { "Version $version" } else { 'Installed' }
        }
        elseif ($entry.Handle) {
            $result = Wait-CapturedProcess -Handle $entry.Handle -TimeoutMilliseconds ([int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
            $first = $result.Output | Where-Object { $_ -notmatch '^(picked up|warning)' } | Select-Object -First 1
            $match = [regex]::Match("$first", '\d+(\.\d+)+')
            if ($result.TimedOut) { $state = 'Yellow'; $detail = 'Installed, but did not report a version in time' }
            elseif ($result.ExitCode -eq 0 -and $first) { $detail = $first }
            elseif ($match.Success) { $detail = $first }
            else { $state = 'Yellow'; $detail = if ($first) { $first } else { 'Installed, but did not report a version' } }
            if ($match.Success) { $version = $match.Value }
        }
        else {
            $state = 'Yellow'; $detail = "Installed, but could not run: $($entry.Error)"
        }

        [pscustomobject]@{
            Name     = [string]$tool.name
            Category = [string]$tool.category
            State    = $state
            Version  = $version
            Detail   = $detail
            Path     = $entry.Path
            Open     = [string]$tool.open
            Install  = if ($state -ne 'Green') { Get-ToolInstallCommand -Tool $tool } else { $null }
            Required = $entry.Required
        }
    }
}

# --------------------------------------------------------------------------------------------------- server discovery

function Get-ScanGroups {
    # Folders to search, each a "group" that labels the servers found under it.
    param([Parameter(Mandatory)] $Config)

    $groups = [ordered]@{}
    $blocked = @($env:WINDIR, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramData, $env:APPDATA, $env:LOCALAPPDATA) |
        Where-Object { $_ } | ForEach-Object { Get-PathKey $_ }
    $profileKey = Get-PathKey $env:USERPROFILE
    $add = {
        param([string]$Path, [bool]$IsRoot)
        if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return }
        $full = (Get-Item -LiteralPath $Path -Force).FullName
        $key = Get-PathKey $full
        # Never sweep a whole profile, drive or system folder.
        if ($key -eq $profileKey -or $key.Length -le 3 -or @($blocked | Where-Object { $key -eq $_ -or $key.StartsWith("$_\") }).Count) { return }
        if (-not $groups.Contains($key)) { $groups[$key] = [pscustomobject]@{ Path = $full; IsRoot = $IsRoot } }
    }

    foreach ($root in $Config.ProjectRoots) { & $add $root $true }
    if ($Config.IncludeClaudeProjects) {
        $claudeJson = Join-Path $env:USERPROFILE '.claude.json'
        if (Test-Path -LiteralPath $claudeJson -PathType Leaf) {
            try {
                foreach ($project in (Get-Content -Raw -LiteralPath $claudeJson | ConvertFrom-Json).projects.PSObject.Properties.Name) { & $add $project $false }
            }
            catch { }
        }
    }
    return @($groups.Values)
}

function Get-ScanFolders {
    param([Parameter(Mandatory)] $Config, [Parameter(Mandatory)] [object[]]$Groups)

    $visited = @{}
    $folders = [Collections.Generic.List[string]]::new()
    $stack = [Collections.Generic.Stack[object]]::new()
    foreach ($group in $Groups) { $stack.Push(@($group.Path, 0)) }
    while ($stack.Count -and $folders.Count -lt $script:MaxScanFolders) {
        $path, $depth = $stack.Pop()
        $key = Get-PathKey $path
        if ($visited.ContainsKey($key)) { continue }
        $visited[$key] = $true
        $folders.Add($path)
        if ($depth -ge $Config.ScanDepth) { continue }
        $children = try { (New-Object IO.DirectoryInfo $path).GetDirectories() } catch { @() }
        foreach ($child in $children) {
            $name = $child.Name
            if ($name.StartsWith('.') -or $name -in $script:SkipFolders) { continue }
            if (([int]$child.Attributes -band 0x400) -or (Test-CloudOnly $child)) { continue }   # junctions, cloud-only
            if (@($Config.ExcludeFolders | Where-Object { $name -like $_ }).Count) { continue }
            $stack.Push(@($child.FullName, ($depth + 1)))
        }
    }
    return $folders
}

function Get-PortFromText {
    param([string]$Text, [string[]]$Patterns)
    foreach ($pattern in $Patterns) {
        $match = [regex]::Match("$Text", $pattern, 'IgnoreCase')
        if ($match.Success) { return [int]$match.Groups[1].Value }
    }
    return $null
}

function Find-Upward {
    # The nearest file of these names in Folder or its parents, without going above Stop.
    param([string]$Folder, [string[]]$Names, [string]$Stop)
    $current = $Folder
    while ($current) {
        foreach ($name in $Names) {
            $candidate = [IO.Path]::Combine($current, $name)
            if ([IO.File]::Exists($candidate)) { return $candidate }
        }
        if ((Get-PathKey $current) -eq (Get-PathKey $Stop)) { break }
        $current = Split-Path $current -Parent
    }
    return $null
}

function New-ServerCandidate {
    param([hashtable]$Fields)
    $values = @{ Kind = 'process'; Executable = ''; Arguments = @(); Port = 0; NeedsPort = $false; Signature = ''; Runtime = ''
        Service = ''; Registered = $false; Framework = ''; Source = ''; Dir = ''; Bin = ''; InstallDir = ''; InstallCommand = '' }
    foreach ($key in $Fields.Keys) { $values[$key] = $Fields[$key] }
    [pscustomobject]$values
}

$script:NodeFrameworks = @(
    @{ Pattern = '\bvite\s+preview\b'; Name = 'Vite preview'; Port = 4173; Config = 'vite' },
    @{ Pattern = '\bvite\b(?!\s+build)'; Name = 'Vite'; Port = 5173; Config = 'vite' },
    @{ Pattern = '\bnext\s+(dev|start)\b'; Name = 'Next.js'; Port = 3000 },
    @{ Pattern = '\b(nuxt|nuxi)\s+dev\b'; Name = 'Nuxt'; Port = 3000 },
    @{ Pattern = '\bastro\s+dev\b'; Name = 'Astro'; Port = 4321 },
    @{ Pattern = 'react-scripts\s+start'; Name = 'Create React App'; Port = 3000 },
    @{ Pattern = '\bng\s+serve\b'; Name = 'Angular'; Port = 4200 },
    @{ Pattern = 'webpack(-dev-server|\s+serve)'; Name = 'webpack'; Port = 8080 },
    @{ Pattern = '\bgatsby\s+develop\b'; Name = 'Gatsby'; Port = 8000 },
    @{ Pattern = '\bremix\s+dev\b'; Name = 'Remix'; Port = 3000 },
    @{ Pattern = '\bdocusaurus\s+start\b'; Name = 'Docusaurus'; Port = 3000 },
    @{ Pattern = '\bstorybook\s+dev\b|start-storybook'; Name = 'Storybook'; Port = 6006 },
    @{ Pattern = '\bwrangler\s+dev\b'; Name = 'Wrangler'; Port = 8787 },
    @{ Pattern = '\bexpo\s+start\b'; Name = 'Expo'; Port = 8081 },
    @{ Pattern = '\beleventy\b.*--serve'; Name = 'Eleventy'; Port = 8080 },
    @{ Pattern = '\bparcel\b(?!\s+build)'; Name = 'Parcel'; Port = 1234 },
    @{ Pattern = '\bhttp-server\b'; Name = 'http-server'; Port = 8080 },
    @{ Pattern = '^\s*serve\b'; Name = 'serve'; Port = 3000 }
)

function Find-NodeServer {
    param([string]$Folder, [string]$GroupPath, [hashtable]$Files)

    if (-not $Files.ContainsKey('package.json')) { return }
    $text = Read-SmallText $Files['package.json']
    if (-not $text) { return }
    try { $package = $text | ConvertFrom-Json } catch { return }
    if (-not $package.scripts) { return }

    foreach ($scriptName in 'dev', 'start', 'serve', 'develop', 'storybook') {
        $command = [string]$package.scripts.$scriptName
        if (-not $command) { continue }
        # Scripts written for a Linux shell (export X=..., ${VAR}) do not run from Windows npm.
        if ($command -match '(^|&&|;)\s*export\s|\$\{|\$[A-Z_]{2,}') { continue }

        $framework = $script:NodeFrameworks | Where-Object { $command -match $_.Pattern } | Select-Object -First 1
        $port = Get-PortFromText $command '(?:--port|-p)[=\s]+(\d{2,5})', '\bPORT=(\d{2,5})'
        $needsPort = $false
        $name = $null

        if ($framework) {
            $name = $framework.Name
            if (-not $port -and $framework.Config -eq 'vite') {
                $configFile = $Files.Keys | Where-Object { $_ -like 'vite.config.*' } | Select-Object -First 1
                $configText = if ($configFile) { Read-SmallText $Files[$configFile] } else { '' }
                $port = Get-PortFromText $configText '\bport\s*:\s*(\d{2,5})', 'rawPort\s*\?\s*Number\(rawPort\)\s*:\s*(\d{2,5})', 'process\.env\.PORT\s*(?:\|\||\?\?)\s*["'']?(\d{2,5})'
                if (-not $port -and $configText -match 'process\.env\.PORT') { $needsPort = $true }
            }
            if (-not $port -and -not $needsPort) { $port = $framework.Port }
        }
        else {
            $file = [regex]::Match($command, '^(?:cross-env\s+(?:\S+=\S+\s+)*)?(?:tsx|node|ts-node|nodemon|bun)\s+(?:watch\s+|--watch\s+)?(?:--\S+\s+)*([^\s]+\.[cm]?[jt]s)\b')
            if (-not $file.Success) { continue }
            $name = 'Node server'
            $serverText = Read-SmallText (Join-Path $Folder $file.Groups[1].Value)
            if (-not $port) {
                $port = Get-PortFromText $serverText '(?:const|let|var)\s+PORT\s*=\s*(?:Number\(|parseInt\()?\s*(?:process\.env\.PORT\s*(?:\|\||\?\?)\s*)?["'']?(\d{2,5})',
                    '\.listen\(\s*(\d{2,5})'
            }
            if (-not $port) { $needsPort = $true }
        }

        $lock = Find-Upward -Folder $Folder -Names 'pnpm-lock.yaml', 'yarn.lock', 'bun.lockb', 'bun.lock', 'package-lock.json' -Stop $GroupPath
        $manager = switch -Wildcard ([IO.Path]::GetFileName("$lock")) { 'pnpm-lock.yaml' { 'pnpm' } 'yarn.lock' { 'yarn' } 'bun.lock*' { 'bun' } default { 'npm' } }
        # The program the script runs (vite, tsx, next...) must exist in node_modules\.bin as a Windows .cmd shim.
        $bin = [regex]::Match($command, '^(?:cross-env\s+(?:\S+=\S+\s+)*)?([\w@./-]+)').Groups[1].Value
        if ($bin -in 'node', 'npm', 'pnpm', 'yarn', 'bun', 'npx') { $bin = '' }
        New-ServerCandidate @{
            Name = "$name ($manager run $scriptName)"; Framework = $name; Dir = $Folder; Executable = $manager
            Arguments = @('run', $scriptName); Port = [int]$port; NeedsPort = $needsPort; Runtime = 'node'; Source = "package.json `"$scriptName`""
            Bin = $bin; InstallDir = $(if ($lock) { Split-Path $lock -Parent } else { $Folder }); InstallCommand = "$manager install"
        }
        return
    }
}

function Find-PythonServers {
    param([string]$Folder, [hashtable]$Files)

    $pyFiles = @($Files.Keys | Where-Object { $_ -like '*.py' -and $_ -notlike 'test_*' } | Sort-Object | Select-Object -First 40)
    if (-not $pyFiles.Count) { return }
    $venv = Find-Upward -Folder $Folder -Names '.venv\Scripts\python.exe', 'venv\Scripts\python.exe' -Stop (Split-Path $Folder -Parent)
    $python = if ($venv) { $venv } else { 'python' }

    if ($Files.ContainsKey('manage.py')) {
        New-ServerCandidate @{ Name = 'Django (manage.py runserver)'; Framework = 'Django'; Dir = $Folder; Executable = $python
            Arguments = @('manage.py', 'runserver', '127.0.0.1:8000'); Port = 8000; Signature = 'manage.py'; Runtime = 'python'; Source = 'manage.py' }
        return
    }

    foreach ($fileName in $pyFiles) {
        $file = New-Object IO.FileInfo $Files[$fileName]
        $text = Read-SmallText $file.FullName
        if (-not $text -or $text -notmatch '__name__\s*==\s*["'']__main__["'']') { continue }
        $kind = if ($text -match 'uvicorn\.run\(|import uvicorn') { @('uvicorn', 8000) }
            elseif ($text -match '\bapp\.run\(') { @('Flask', 5000) }
            elseif ($text -match 'web\.run_app\(') { @('aiohttp', 8080) }
            elseif ($text -match 'serve_forever\(|HTTPServer\(|make_server\(') { @('Python HTTP', 8000) }
            else { $null }
        if (-not $kind) { continue }
        $port = Get-PortFromText $text '["'']--port["''][^)\r\n]*?default\s*=\s*["'']?(\d{2,5})', '--port\s+\[?(\d{2,5})', 'else\s+["''](\d{4,5})["'']',
            '\bport\s*=\s*(?:int\()?\s*(?:os\.environ\.get\(\s*["'']PORT["'']\s*,\s*)?["'']?(\d{2,5})', '\bPORT\s*=\s*(?:int\()?\s*["'']?(\d{2,5})'
        if (-not $port) { $port = $kind[1] }
        New-ServerCandidate @{ Name = "$($kind[0]) ($($file.Name))"; Framework = $kind[0]; Dir = $Folder; Executable = $python
            Arguments = @($file.Name); Port = $port; Signature = $file.Name; Runtime = 'python'; Source = $file.Name }
    }
}

function Find-ComposeServers {
    param([string]$Folder, [hashtable]$Files)

    foreach ($fileName in 'compose.yaml', 'compose.yml', 'docker-compose.yml', 'docker-compose.yaml') {
        if (-not $Files.ContainsKey($fileName)) { continue }
        $text = Read-SmallText $Files[$fileName]
        if (-not $text) { continue }
        $service = $null; $inServices = $false; $inPorts = $false; $serviceIndent = -1
        foreach ($line in ($text -split "`r?`n")) {
            if ($line -match '^\s*(#|$)') { continue }
            $indent = $line.Length - $line.TrimStart().Length
            if ($indent -eq 0) { $inServices = $line -match '^services\s*:'; $service = $null; $serviceIndent = -1; continue }
            if (-not $inServices) { continue }
            if ($serviceIndent -lt 0 -or $indent -le $serviceIndent) {
                if ($line -match '^\s*([A-Za-z0-9_.-]+)\s*:\s*$') { $service = $Matches[1]; $serviceIndent = $indent; $inPorts = $false }
                continue
            }
            if ($line -match '^\s*ports\s*:') { $inPorts = $true; continue }
            if ($inPorts -and $line -match '^\s*[A-Za-z_]+\s*:' -and $line -notmatch 'published') { $inPorts = $false }
            if (-not $inPorts -or -not $service) { continue }
            $port = Get-PortFromText $line '^\s*-\s*["'']?(?:[\d.]+:)?(\d{2,5}):\d{2,5}', 'published\s*:\s*["'']?(\d{2,5})'
            if ($port) {
                New-ServerCandidate @{ Kind = 'compose'; Name = "$service (docker compose)"; Framework = "Docker $service"; Dir = $Folder; Service = $service
                    Port = $port; Runtime = 'docker'; Source = $fileName }
            }
        }
        return
    }
}

function Find-DotNetServers {
    param([string]$Folder)
    $path = [IO.Path]::Combine($Folder, 'Properties', 'launchSettings.json')
    if (-not [IO.File]::Exists($path)) { return }
    $text = Read-SmallText $path
    if (-not $text) { return }
    try { $settings = $text | ConvertFrom-Json } catch { return }
    foreach ($profile in @($settings.profiles.PSObject.Properties)) {
        if ($profile.Value.commandName -ne 'Project' -or -not $profile.Value.applicationUrl) { continue }
        $port = Get-PortFromText ([string]$profile.Value.applicationUrl) 'http://[^:;]+:(\d{2,5})', 'https://[^:;]+:(\d{2,5})'
        if (-not $port) { continue }
        New-ServerCandidate @{ Name = ".NET ($($profile.Name))"; Framework = '.NET'; Dir = $Folder; Executable = 'dotnet'
            Arguments = @('run', '--launch-profile', $profile.Name); Port = $port; Signature = (Split-Path $Folder -Leaf); Runtime = 'dotnet'; Source = 'launchSettings.json' }
        return
    }
}

function Find-LaunchJsonServers {
    param([string]$Folder)
    $path = [IO.Path]::Combine($Folder, '.claude', 'launch.json')
    if (-not [IO.File]::Exists($path)) { return }
    $text = Read-SmallText $path
    if (-not $text) { return }
    try { $launch = $text | ConvertFrom-Json } catch { return }
    foreach ($config in @($launch.configurations)) {
        $arguments = @($config.runtimeArgs | ForEach-Object { [string]$_ })
        # Servers run from a Claude scratchpad are throwaway test copies, not apps.
        if (-not $config.port -or @($arguments + [string]$config.runtimeExecutable) -match '\\AppData\\(Local\\Temp|Roaming\\Claude\\scratch-workspaces)\\') { continue }
        New-ServerCandidate @{ Name = [string]$config.name; Framework = 'Claude Code'; Dir = $Folder; Executable = [string]$config.runtimeExecutable
            Arguments = $arguments; Port = [int]$config.port; Registered = $true
            Signature = [string]($arguments | Where-Object { $_ -notlike '-*' } | Select-Object -First 1); Source = '.claude\launch.json' }
    }
}

function Test-ProjectFolder {
    param([string]$Path)
    foreach ($marker in '.git', '.claude') { if ([IO.Directory]::Exists([IO.Path]::Combine($Path, $marker))) { return $true } }
    foreach ($marker in 'package.json', 'pyproject.toml', 'requirements.txt', 'CLAUDE.md', 'README.md', 'docker-compose.yml', 'compose.yaml', 'go.mod', 'Cargo.toml') {
        if ([IO.File]::Exists([IO.Path]::Combine($Path, $marker))) { return $true }
    }
    return $false
}

function Get-GroupPath {
    # Which project a folder belongs to: the deepest Claude project containing it, or, under a root, the first
    # folder on the way down that looks like a project (has .git, a README, package.json, ...). Whichever is deeper.
    param([string]$Folder, [object[]]$GroupKeys, [hashtable]$Cache)

    $folderKey = Get-PathKey $Folder
    $owner = $GroupKeys | Where-Object { $folderKey -eq $_.Key -or $folderKey.StartsWith("$($_.Key)\") } | Sort-Object { $_.Key.Length } -Descending | Select-Object -First 1
    if (-not $owner.IsRoot -or $folderKey -eq $owner.Key) { return $owner.Path }

    $path = $owner.Path
    foreach ($part in $Folder.Substring($owner.Path.Length).TrimStart('\').Split('\')) {
        $path = [IO.Path]::Combine($path, $part)
        $key = Get-PathKey $path
        if (-not $Cache.ContainsKey($key)) { $Cache[$key] = Test-ProjectFolder $path }
        if ($Cache[$key]) { return $path }
    }
    return [IO.Path]::Combine($owner.Path, $Folder.Substring($owner.Path.Length).TrimStart('\').Split('\')[0])
}

function Get-StablePort {
    param([string]$Key, [hashtable]$Taken)
    $hash = 0
    foreach ($char in $Key.ToCharArray()) { $hash = ($hash * 31 + [int]$char) % 1000003 }
    $port = 5200 + ($hash % 700)
    while ($Taken.ContainsKey($port)) { $port++ }
    return $port
}

function Find-DevServers {
    param([Parameter(Mandatory)] $Config)

    $groups = @(Get-ScanGroups -Config $Config)
    if (-not $groups.Count) { return @() }
    $folders = Get-ScanFolders -Config $Config -Groups $groups
    $groupKeys = @($groups | ForEach-Object { [pscustomobject]@{ Key = Get-PathKey $_.Path; Path = $_.Path; IsRoot = $_.IsRoot } })

    $projectRootCache = @{}
    $candidates = @(foreach ($folder in $folders) {
        $files = @{}
        try { foreach ($file in [IO.Directory]::GetFiles($folder)) { $files[[IO.Path]::GetFileName($file).ToLowerInvariant()] = $file } } catch { continue }
        $found = @(
            Find-LaunchJsonServers -Folder $folder
            Find-PythonServers -Folder $folder -Files $files
            Find-ComposeServers -Folder $folder -Files $files
            Find-DotNetServers -Folder $folder
        )
        $folderKey = Get-PathKey $folder
        $groupPath = $null
        if ($found.Count -or $files.ContainsKey('package.json')) {
            $groupPath = Get-GroupPath -Folder $folder -GroupKeys $groupKeys -Cache $projectRootCache
            $found += @(Find-NodeServer -Folder $folder -GroupPath $groupPath -Files $files)
        }
        foreach ($candidate in $found) {
            $relative = if ($folderKey -eq (Get-PathKey $groupPath)) { '' } else { $folder.Substring($groupPath.Length).TrimStart('\').Replace('\', '/') }
            $title = $Config.Titles[(Get-PathKey $groupPath)]
            if (-not $title) {
                # Nested projects read better with their parent: "EPiC/source", not "source".
                $root = $groupKeys | Where-Object { $_.IsRoot -and (Get-PathKey $groupPath).StartsWith("$($_.Key)\") } | Select-Object -First 1
                $parts = if ($root) { @($groupPath.Substring($root.Path.Length).TrimStart('\').Split('\')) } else { @() }
                $title = if ($parts.Count -ge 3) { "$($parts[-2])/$($parts[-1])" } else { Split-Path $groupPath -Leaf }
            }
            $label = if ($candidate.Registered) { $candidate.Name } elseif ($relative) { "$relative - $($candidate.Framework)" } else { $candidate.Framework }
            $candidate | Add-Member -NotePropertyName Project -NotePropertyValue $title
            $candidate | Add-Member -NotePropertyName GroupPath -NotePropertyValue $groupPath
            $candidate | Add-Member -NotePropertyName Label -NotePropertyValue $label
            $candidate | Add-Member -NotePropertyName Key -NotePropertyValue ("$folderKey|$($candidate.Name)".ToLowerInvariant())
            $candidate
        }
    })

    # A Claude launch.json entry is the authority for its port: drop code guesses on that port in the same project,
    # or in the folder its command runs from (launch.json often lives apart from the code it starts).
    $registered = @($candidates | Where-Object Registered | ForEach-Object {
        $scriptDir = if ($_.Signature -match '^[A-Za-z]:\\') { Get-PathKey (Split-Path $_.Signature -Parent) } else { '' }
        [pscustomobject]@{ Port = [int]$_.Port; Group = Get-PathKey $_.GroupPath; ScriptDir = $scriptDir }
    })
    $candidates = @($candidates | Where-Object {
        $candidate = $_
        if ($candidate.Registered) { return $true }
        $dirKey = Get-PathKey $candidate.Dir
        -not @($registered | Where-Object { $_.Port -eq $candidate.Port -and ($_.Group -eq (Get-PathKey $candidate.GroupPath) -or ($_.ScriptDir -and ($_.ScriptDir -eq $dirKey -or $_.ScriptDir.StartsWith("$dirKey\")))) }).Count
    })

    # Same folder, same port: keep one.
    $seen = @{}
    $candidates = @($candidates | Where-Object {
        if ($_.NeedsPort) { return $true }
        $key = "$(Get-PathKey $_.Dir)|$($_.Port)|$($_.Kind)"
        if ($seen.ContainsKey($key)) { return $false }
        $seen[$key] = $true
        return $true
    })

    # Ports: an explicit setting in showstack.json, else what the code says, else a stable port of its own.
    $taken = @{}
    foreach ($candidate in $candidates) { if ($candidate.Port) { $taken[[int]$candidate.Port] = $true } }
    foreach ($candidate in $candidates) {
        if ($Config.Ports.ContainsKey($candidate.Key)) { $candidate.Port = $Config.Ports[$candidate.Key]; $candidate.NeedsPort = $true }
        elseif ($candidate.NeedsPort) { $candidate.Port = Get-StablePort -Key $candidate.Key -Taken $taken; $taken[$candidate.Port] = $true }
    }

    return @($candidates | Sort-Object Project, Label)
}

# ------------------------------------------------------------------------------------------------------------ inventory

function Update-DevInventory {
    # Full scan of tools and servers, saved so the widget can show it instantly next time.
    param([switch]$ServersOnly)

    $config = Get-WidgetConfig
    $previous = Get-DevInventory
    $tools = if ($ServersOnly -and $previous) { @($previous.Tools) } else { @(Get-ToolInventory -Config $config) }
    $inventory = [pscustomobject]@{
        Tools     = $tools
        Servers   = @(Find-DevServers -Config $config)
        ScannedAt = (Get-Date).ToString('o')
    }
    Write-StateFile -Path $script:InventoryPath -Value $inventory
    return $inventory
}

function Get-DevInventory {
    $inventory = Read-StateFile -Path $script:InventoryPath -Default $null
    if ($inventory) {
        $inventory.Tools = @($inventory.Tools)
        $inventory.Servers = @($inventory.Servers | Where-Object { $_ } | ForEach-Object { $_.Arguments = @($_.Arguments); $_ })
    }
    return $inventory
}

# --------------------------------------------------------------------------------------------------------- live status

function New-StatusItem {
    param([string]$Section, [string]$Group = '', [string]$Name, [ValidateSet('Green', 'Yellow', 'Red')] [string]$State,
        [string]$Detail = '', [string]$Action = '', [string]$Url = '', $Server = $null)
    [pscustomobject]@{ Section = $Section; Group = $Group; Name = $Name; State = $State; Detail = $Detail; Action = $Action; Url = $Url; Server = $Server }
}

function Get-ProcessSnapshot {
    $processes = @{}
    Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine | ForEach-Object { $processes[[int]$_.ProcessId] = $_ }
    $ports = @{}
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in '127.0.0.1', '0.0.0.0', '::', '::1' } |
        ForEach-Object { if (-not $ports.ContainsKey([int]$_.LocalPort) -and $processes.ContainsKey([int]$_.OwningProcess)) { $ports[[int]$_.LocalPort] = $processes[[int]$_.OwningProcess] } }
    [pscustomobject]@{ Processes = $processes; Ports = $ports }
}

function Get-AncestorIds {
    param($Process, [hashtable]$Processes)
    $ids = @{}
    $current = $Process
    for ($i = 0; $current -and $i -lt 15; $i++) {
        if ($ids.ContainsKey([int]$current.ProcessId)) { break }
        $ids[[int]$current.ProcessId] = $true
        $current = $Processes[[int]$current.ParentProcessId]
    }
    return $ids
}

function Get-DockerState {
    $docker = (Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $docker) { return [pscustomobject]@{ Path = $null; Running = $false; Version = ''; Containers = @{} } }
    $version = Invoke-Captured -Path $docker -Arguments 'version', '--format', '{{.Server.Version}}' -TimeoutSeconds 8
    $containers = @{}
    if ($version.ExitCode -eq 0) {
        foreach ($line in (Invoke-Captured -Path $docker -Arguments 'ps', '--format', '{{.Labels}}' -TimeoutSeconds 8).Output) {
            $dir = [regex]::Match($line, 'com\.docker\.compose\.project\.working_dir=([^,]+)').Groups[1].Value
            $service = [regex]::Match($line, 'com\.docker\.compose\.service=([^,]+)').Groups[1].Value
            if ($dir -and $service) { $containers["$(Get-PathKey $dir)|$($service.ToLowerInvariant())"] = $true }
        }
    }
    [pscustomobject]@{ Path = $docker; Running = ($version.ExitCode -eq 0); Version = "$($version.Output | Select-Object -First 1)"; Containers = $containers }
}

function Get-DockerStatusItem {
    param([Parameter(Mandatory)] $Docker)

    if (-not $Docker.Path) { return }
    if ($Docker.Running) {
        return New-StatusItem -Section 'Services' -Name 'Docker' -State 'Green' -Detail "Engine $($Docker.Version) running"
    }
    if (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue) {
        return New-StatusItem -Section 'Services' -Name 'Docker' -State 'Yellow' -Detail 'Docker Desktop is starting'
    }
    $desktop = $script:DockerDesktopPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $desktop) {
        return New-StatusItem -Section 'Services' -Name 'Docker' -State 'Yellow' -Detail 'Docker CLI only; Docker Desktop not found'
    }
    $staleSockets = @(Get-ChildItem -LiteralPath $script:DockerRunPath -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
    if ($staleSockets.Count) {
        # Socket files left by an unclean shutdown make Docker Desktop crash on start ("initializing Inference manager").
        return New-StatusItem -Section 'Services' -Name 'Docker' -State 'Red' -Detail "Stale socket files in $script:DockerRunPath block startup. Rename that folder (or reboot). Do NOT reset to factory defaults."
    }
    New-StatusItem -Section 'Services' -Name 'Docker' -State 'Yellow' -Detail 'Stopped' -Action 'Start'
}

function Get-StartedRecord {
    $started = @{}
    $record = Read-StateFile -Path $script:StartedPath -Default $null
    if ($record) { foreach ($property in $record.PSObject.Properties) { $started[$property.Name] = [int]$property.Value } }
    return $started
}

function Get-ServerOwnership {
    # Is the process listening on this server's port this server? 'Yes', 'Likely', or 'No'.
    param($Server, $Owner, $Snapshot, [hashtable]$Started, [hashtable]$Claims)

    $ancestors = Get-AncestorIds -Process $Owner -Processes $Snapshot.Processes
    $launcher = $Started[$Server.Key]
    if ($launcher -and $ancestors.ContainsKey([int]$launcher)) { return 'Yes' }
    $commandLine = "$($Owner.CommandLine)"
    if ($Server.Signature -and $commandLine -like "*$($Server.Signature)*") { return 'Yes' }
    if ($Server.Dir -and $commandLine -like "*$($Server.Dir)*") { return 'Yes' }
    $runtime = [IO.Path]::GetFileNameWithoutExtension("$($Owner.Name)").ToLowerInvariant()
    if ($Server.Runtime -and $runtime -like "$($Server.Runtime)*" -and $Claims[[int]$Server.Port] -eq 1) { return 'Likely' }
    return 'No'
}

function Test-ServerDependencies {
    # False when a Node project's packages are missing, or were installed on another OS (no Windows .cmd shims).
    param($Server)
    if (-not $Server.Bin) { return $true }
    return [bool](Find-Upward -Folder $Server.Dir -Names "node_modules\.bin\$($Server.Bin).cmd" -Stop $Server.GroupPath)
}

function Get-ServerStatusItems {
    param([object[]]$Servers, $Snapshot, $Docker, [hashtable]$Hidden)

    $started = Get-StartedRecord
    $visible = @($Servers | Where-Object { $_ -and -not $Hidden.ContainsKey($_.Key) })
    $claims = @{}
    foreach ($server in $visible) { $claims[[int]$server.Port] = 1 + [int]$claims[[int]$server.Port] }

    $owned = @{}
    foreach ($server in $visible) {
        $item = @{ Section = 'Servers'; Group = $server.Project; Name = $server.Label; Url = "http://localhost:$($server.Port)"; Server = $server }
        $where = "port $($server.Port) - $($server.Source)"
        $owner = $Snapshot.Ports[[int]$server.Port]

        if ($server.Kind -eq 'compose') {
            if (-not $Docker.Running) { New-StatusItem @item -State 'Yellow' -Detail "Needs Docker - $where"; continue }
            if ($Docker.Containers.ContainsKey("$(Get-PathKey $server.Dir)|$($server.Service.ToLowerInvariant())")) {
                $owned[[int]$server.Port] = $true
                New-StatusItem @item -State 'Green' -Detail "Running - $where" -Action 'Restart'
            }
            elseif ($owner) { New-StatusItem @item -State 'Yellow' -Detail "Port $($server.Port) is in use by $($owner.Name) - $($server.Source)" }
            else { New-StatusItem @item -State 'Yellow' -Detail "Stopped - $where" -Action 'Start' }
            continue
        }

        if (-not $owner) {
            if (-not (Test-ServerDependencies -Server $server)) {
                New-StatusItem @item -State 'Yellow' -Detail "Packages not installed for Windows; Install runs '$($server.InstallCommand)' - $where" -Action 'Install'
                continue
            }
            $note = if ($server.NeedsPort) { ' (port picked by widget)' } else { '' }
            New-StatusItem @item -State 'Yellow' -Detail "Stopped - $where$note" -Action 'Start'
            continue
        }
        switch (Get-ServerOwnership -Server $server -Owner $owner -Snapshot $Snapshot -Started $started -Claims $claims) {
            'Yes' { $owned[[int]$server.Port] = $true; New-StatusItem @item -State 'Green' -Detail "Running (PID $($owner.ProcessId)) - $where" -Action 'Restart' }
            'Likely' { $owned[[int]$server.Port] = $true; New-StatusItem @item -State 'Green' -Detail "Running (PID $($owner.ProcessId), probably this one) - $where" -Action 'Restart' }
            default {
                $state = if ($server.Registered) { 'Red' } else { 'Yellow' }
                New-StatusItem @item -State $state -Detail "Port $($server.Port) is in use by $($owner.Name) (PID $($owner.ProcessId)) - $($server.Source)"
            }
        }
    }

    foreach ($port in ($Snapshot.Ports.Keys | Sort-Object)) {
        if ($owned.ContainsKey($port)) { continue }
        $owner = $Snapshot.Ports[$port]
        $runtime = [IO.Path]::GetFileNameWithoutExtension("$($owner.Name)")
        if ($runtime -notin $script:DevRuntimes) { continue }
        $commandLine = "$($owner.CommandLine)" -replace '\s+', ' '
        $what = if ($commandLine -match '\\AppData\\Local\\Temp\\claude\\') { 'Claude test copy' } else { $runtime }
        New-StatusItem -Section 'Other local servers' -Name "Port $port" -State 'Green' -Detail "$what (PID $($owner.ProcessId)): $commandLine" -Url "http://localhost:$port"
    }
}

function Get-HiddenServers {
    $hidden = @{}
    foreach ($key in @(Read-StateFile -Path $script:HiddenPath -Default @())) { if ($key) { $hidden[[string]$key] = $true } }
    return $hidden
}

function Get-DevStatus {
    # Live rows for the widget, using the saved inventory.
    param($Inventory)

    Update-ProcessPath
    if (-not $Inventory) { $Inventory = Get-DevInventory }
    if (-not $Inventory) { $Inventory = [pscustomobject]@{ Tools = @(); Servers = @(); ScannedAt = $null } }
    $hidden = Get-HiddenServers
    $snapshot = Get-ProcessSnapshot
    $docker = Get-DockerState
    $rows = @(
        Get-DockerStatusItem -Docker $docker
        Get-ServerStatusItems -Servers @($Inventory.Servers) -Snapshot $snapshot -Docker $docker -Hidden $hidden
    )
    [pscustomobject]@{
        Tools     = @($Inventory.Tools)
        Rows      = $rows
        ScannedAt = $Inventory.ScannedAt
        Hidden    = @(@($Inventory.Servers) | Where-Object { $_ -and $hidden.ContainsKey($_.Key) }).Count
    }
}

# ------------------------------------------------------------------------------------------------------------- actions

function Wait-ForPort {
    param([int]$Port, [int]$TimeoutSeconds, $Process)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { return $true }
        if ($Process -and $Process.HasExited) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-DevServer {
    # Starts a server, or restarts it if it is already running. Throws with the reason when it cannot.
    param([Parameter(Mandatory)] $Server, [ValidateRange(5, 300)] [int]$TimeoutSeconds = 60)

    Update-ProcessPath
    $label = "$($Server.Project): $($Server.Label)"

    if ($Server.Kind -eq 'compose') {
        $docker = Get-DockerState
        if (-not $docker.Running) { throw "$label needs Docker, which is not running. Start Docker first." }
        $running = $docker.Containers.ContainsKey("$(Get-PathKey $Server.Dir)|$($Server.Service.ToLowerInvariant())")
        $arguments = if ($running) { @('compose', 'restart', $Server.Service) } else { @('compose', 'up', '-d', $Server.Service) }
        $result = Invoke-Captured -Path $docker.Path -Arguments $arguments -WorkingDirectory $Server.Dir -TimeoutSeconds 180
        if ($result.ExitCode -ne 0) { throw "$label did not start: $($result.Output | Select-Object -Last 3)" }
        return
    }

    $snapshot = Get-ProcessSnapshot
    $owner = $snapshot.Ports[[int]$Server.Port]
    $started = Get-StartedRecord
    if ($owner) {
        $claims = @{ ([int]$Server.Port) = 1 }
        if ((Get-ServerOwnership -Server $Server -Owner $owner -Snapshot $snapshot -Started $started -Claims $claims) -eq 'No') {
            throw "Port $($Server.Port) is in use by $($owner.Name) (PID $($owner.ProcessId)), which is not $label. Not touching it."
        }
        Stop-ProcessTree -Id $owner.ProcessId
    }
    if ($started[$Server.Key]) { Stop-ProcessTree -Id $started[$Server.Key] }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Get-NetTCPConnection -LocalPort $Server.Port -State Listen -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 300 }

    $executable = if (Test-Path -LiteralPath $Server.Executable -PathType Leaf) { $Server.Executable }
        else { (Get-Command $Server.Executable -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
    if (-not $executable) { throw "$($Server.Executable) is not installed or not on PATH, so $label cannot start." }

    $logBase = Join-Path $script:StatePath (("$($Server.Project)-$($Server.Label)") -replace '[^\w.-]', '_')
    # cmd.exe runs .cmd shims (npm, pnpm) and writes the logs; the server is its child, so the record finds it later.
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = "$env:WINDIR\System32\cmd.exe"
    $psi.Arguments = '/d /s /c ""' + $executable + '" ' + (ConvertTo-ArgumentString @($Server.Arguments)) + ' 1>"' + $logBase + '.out.log" 2>"' + $logBase + '.err.log""'
    $psi.WorkingDirectory = $Server.Dir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    if ($Server.NeedsPort) { $psi.EnvironmentVariables['PORT'] = [string]$Server.Port }
    $process = [Diagnostics.Process]::Start($psi)

    $started[$Server.Key] = $process.Id
    Write-StateFile -Path $script:StartedPath -Value ([pscustomobject]$started)

    if (-not (Wait-ForPort -Port $Server.Port -TimeoutSeconds $TimeoutSeconds -Process $process)) {
        $tail = Get-Content -LiteralPath "$logBase.err.log" -Tail 3 -ErrorAction SilentlyContinue
        if (-not $tail) { $tail = Get-Content -LiteralPath "$logBase.out.log" -Tail 3 -ErrorAction SilentlyContinue }
        throw "$label did not start on port $($Server.Port). $($tail -join ' ') (log: $logBase.err.log)"
    }
}

function Start-DockerEngine {
    param([ValidateRange(5, 600)] [int]$TimeoutSeconds = 120)

    $status = Get-DockerStatusItem -Docker (Get-DockerState)
    if (-not $status -or $status.Action -ne 'Start') { return }
    $desktop = $script:DockerDesktopPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    Start-Process -FilePath $desktop -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds 2
    } until ((Get-DockerState).Running -or [DateTime]::UtcNow -ge $deadline)
}

function Restart-AllDev {
    # Docker if stopped; every Claude-registered server (started or restarted); every other server that is running.
    $problems = @()
    Start-DockerEngine
    foreach ($row in @((Get-DevStatus).Rows | Where-Object { $_.Server })) {
        if ($row.Server.Registered -or $row.Action -eq 'Restart') {
            try { Start-DevServer -Server $row.Server } catch { $problems += $_.Exception.Message }
        }
    }
    if ($problems.Count) { throw ($problems -join "`n") }
}

function Hide-DevServer {
    param([Parameter(Mandatory)] [string]$Key)
    $hidden = @(@(Read-StateFile -Path $script:HiddenPath -Default @()) + $Key | Where-Object { $_ } | Select-Object -Unique)
    Write-StateFile -Path $script:HiddenPath -Value $hidden
}

function Clear-HiddenServers {
    Write-StateFile -Path $script:HiddenPath -Value @()
}

function New-InstallScript {
    param([Parameter(Mandatory)] [string]$Name, [Parameter(Mandatory)] [string]$Command, [string]$WorkingDirectory)
    $quotedName = $Name -replace "'", "''"
    $shownCommand = $Command -replace '`', '``' -replace '"', '`"' -replace '\$', '`$'
    $location = if ($WorkingDirectory) { "Set-Location -LiteralPath '$($WorkingDirectory -replace "'", "''")'" } else { '' }
    @"
`$Host.UI.RawUI.WindowTitle = 'Installing $quotedName'
$location
Write-Host 'Installing $quotedName' -ForegroundColor Cyan
Write-Host "> $shownCommand   (in `$(Get-Location))" -ForegroundColor DarkGray
Write-Host ''
try {
$Command
}
catch {
    Write-Host `$_.Exception.Message -ForegroundColor Red
}
Write-Host ''
Read-Host 'Finished. Press Enter to close this window; the widget then checks again'
"@
}

function Install-DevTool {
    # Runs the tool's install command in its own console window, so the user sees progress and answers any prompt
    # (winget agreements, UAC), and returns when it is closed.
    param([Parameter(Mandatory)] [string]$Name)

    $tool = Get-ToolCatalog -Config (Get-WidgetConfig) | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if (-not $tool) { throw "No tool named '$Name' in the catalog." }
    $command = Get-ToolInstallCommand -Tool $tool
    if (-not $command) { throw "The catalog has no install command for '$Name'." }
    Invoke-InConsole -Name $Name -Command $command
}

function Invoke-InConsole {
    param([Parameter(Mandatory)] [string]$Name, [Parameter(Mandatory)] [string]$Command, [string]$WorkingDirectory)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes((New-InstallScript -Name $Name -Command $Command -WorkingDirectory $WorkingDirectory)))
    Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded -Wait
}

function Install-ServerDependencies {
    # Runs the project's package install (npm/pnpm/yarn/bun install) in a console window the user can watch.
    param([Parameter(Mandatory)] $Server)
    if (-not $Server.InstallCommand) { throw "No install command known for $($Server.Project): $($Server.Label)." }
    Invoke-InConsole -Name "$($Server.Project) packages" -Command $Server.InstallCommand -WorkingDirectory $Server.InstallDir
}

function Open-DevTool {
    param([Parameter(Mandatory)] $Tool)
    if ($Tool.Open -eq 'shell') {
        Start-Process -FilePath $Tool.Path -WorkingDirectory (Get-WidgetConfig).ShellFolder
    }
    else {
        Start-Process -FilePath $Tool.Path
    }
}

Export-ModuleMember -Function Get-WidgetConfig, Update-DevInventory, Get-DevInventory, Get-DevStatus, Start-DevServer, Start-DockerEngine,
    Restart-AllDev, Install-DevTool, Install-ServerDependencies, Open-DevTool, Hide-DevServer, Clear-HiddenServers
