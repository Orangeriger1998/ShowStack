# ShowStack: floating developer-environment widget. Top: a box per installed developer tool. Below: Docker, every localhost server found in
# your project code (grouped by project, with Start/Restart), and anything else listening on a port.
# Launch through ShowStack.vbs so no console window shows.

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$modulePath = Join-Path $PSScriptRoot 'ShowStack.psm1'
$statePath = Join-Path $env:LOCALAPPDATA 'ShowStack'
$positionFile = Join-Path $statePath 'widget.json'
$logFile = Join-Path $statePath 'widget.log'
New-Item -ItemType Directory -Force -Path $statePath | Out-Null

$mutex = New-Object Threading.Mutex($false, 'Local\ShowStackWidget')
if (-not $mutex.WaitOne(0)) {
    exit   # already open
}

function Write-WidgetLog {
    param([string]$Text)
    Add-Content -LiteralPath $logFile -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $Text)
}

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="ShowStack" Width="430" SizeToContent="Height" WindowStyle="None" AllowsTransparency="True"
        Background="Transparent" Topmost="True" ResizeMode="NoResize" ShowInTaskbar="True"
        WindowStartupLocation="Manual" FontFamily="Segoe UI" FontSize="12">
  <Window.Resources>
    <Style x:Key="Btn" TargetType="Button">
      <Setter Property="Foreground" Value="#E6E9EF"/>
      <Setter Property="Background" Value="#2C3341"/>
      <Setter Property="Padding" Value="10,7"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="B" Background="{TemplateBinding Background}" CornerRadius="6" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="B" Property="Opacity" Value="0.82"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter TargetName="B" Property="Opacity" Value="0.4"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Small" TargetType="Button" BasedOn="{StaticResource Btn}">
      <Setter Property="Padding" Value="9,2"/>
      <Setter Property="FontSize" Value="11"/>
    </Style>
    <Style x:Key="Icon" TargetType="Button" BasedOn="{StaticResource Btn}">
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="Foreground" Value="#8A93A6"/>
      <Setter Property="Padding" Value="8,0"/>
      <Setter Property="FontSize" Value="15"/>
    </Style>
    <Style x:Key="Link" TargetType="Button" BasedOn="{StaticResource Btn}">
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="Foreground" Value="#7AA2F7"/>
      <Setter Property="Padding" Value="4,0"/>
      <Setter Property="FontSize" Value="11"/>
    </Style>
  </Window.Resources>
  <Border Background="#1E222B" CornerRadius="10" BorderBrush="#343A46" BorderThickness="1" Padding="14,10,14,12">
    <DockPanel>
      <Grid x:Name="Header" DockPanel.Dock="Top" Background="Transparent" Margin="0,0,0,6" Cursor="SizeAll">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Border Width="36" Height="36" CornerRadius="8" Margin="0,0,10,0" VerticalAlignment="Center">
          <Border.Background><ImageBrush x:Name="AvatarBrush" Stretch="UniformToFill"/></Border.Background>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center">
          <TextBlock Text="ShowStack" FontSize="15" FontWeight="SemiBold" Foreground="#E6E9EF"/>
          <TextBlock x:Name="Summary" Text="Loading..." FontSize="11" Foreground="#8A93A6" TextTrimming="CharacterEllipsis"/>
        </StackPanel>
        <Button x:Name="MinButton" Grid.Column="2" Style="{StaticResource Icon}" Content="&#x2013;" ToolTip="Minimize"/>
        <Button x:Name="CloseButton" Grid.Column="3" Style="{StaticResource Icon}" Content="&#xD7;" ToolTip="Close"/>
      </Grid>
      <StackPanel DockPanel.Dock="Bottom" Margin="0,12,0,0">
        <Grid>
          <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition Width="8"/><ColumnDefinition/></Grid.ColumnDefinitions>
          <Button x:Name="CheckButton" Style="{StaticResource Btn}" Background="#2563EB" FontWeight="SemiBold" Content="CHECK STATUS"
                  ToolTip="Look for new servers in your project code and check what is running"/>
          <Button x:Name="RestartButton" Grid.Column="2" Style="{StaticResource Btn}" FontWeight="SemiBold" Content="RESTART ALL"
                  ToolTip="Start Docker if stopped, start or restart every server Claude Code registered, and restart every other server that is running"/>
        </Grid>
        <TextBlock x:Name="Message" TextWrapping="Wrap" FontSize="11" Foreground="#8A93A6" Margin="0,8,0,0" Visibility="Collapsed"/>
      </StackPanel>
      <ScrollViewer x:Name="Scroller" VerticalScrollBarVisibility="Auto">
        <StackPanel>
          <StackPanel x:Name="Splash" Margin="0,10,0,6" Visibility="Collapsed">
            <Border Width="270" Height="270" CornerRadius="14" HorizontalAlignment="Center">
              <Border.Background><ImageBrush x:Name="SplashBrush" Stretch="UniformToFill"/></Border.Background>
            </Border>
            <TextBlock x:Name="SplashText" Text="Searching for tools and servers" FontSize="12" Foreground="#AEB6C6"
                       HorizontalAlignment="Center" Margin="0,12,0,0"/>
          </StackPanel>
          <StackPanel x:Name="Content">
          <Grid Margin="0,4,0,4">
            <TextBlock x:Name="ToolsHeader" Text="TOOLS" FontSize="10" FontWeight="SemiBold" Foreground="#6B7385" VerticalAlignment="Center"/>
            <Button x:Name="RescanButton" Style="{StaticResource Link}" Content="Rescan tools" HorizontalAlignment="Right"
                    ToolTip="Look again for installed developer tools (after you install or remove one)"/>
          </Grid>
          <WrapPanel x:Name="Chips"/>
          <StackPanel x:Name="Rows"/>
          </StackPanel>
        </StackPanel>
      </ScrollViewer>
    </DockPanel>
  </Border>
</Window>
'@

$window = [Windows.Markup.XamlReader]::Load((New-Object Xml.XmlNodeReader $xaml))
try { $window.Icon = [Windows.Media.Imaging.BitmapFrame]::Create([Uri](Join-Path $PSScriptRoot 'assets\showstack.ico')) } catch { }
$ui = @{}
foreach ($name in 'Header', 'Summary', 'MinButton', 'CloseButton', 'CheckButton', 'RestartButton', 'RescanButton', 'Message', 'Rows', 'Chips',
        'ToolsHeader', 'Scroller', 'AvatarBrush', 'SplashBrush', 'Splash', 'SplashText', 'Content') {
    $ui[$name] = $window.FindName($name)
}

function New-Bitmap {
    param([string]$Path, [int]$Width)
    $bitmap = New-Object Windows.Media.Imaging.BitmapImage
    $bitmap.BeginInit()
    $bitmap.UriSource = [Uri]$Path
    $bitmap.DecodePixelWidth = $Width
    $bitmap.CacheOption = 'OnLoad'
    $bitmap.EndInit()
    $bitmap.Freeze()
    return $bitmap
}
try {
    $ui.AvatarBrush.ImageSource = New-Bitmap -Path (Join-Path $PSScriptRoot 'assets\avatar.png') -Width 72
    $ui.SplashBrush.ImageSource = New-Bitmap -Path (Join-Path $PSScriptRoot 'assets\splash.png') -Width 540
}
catch { }
$ui.Scroller.MaxHeight = [Math]::Max(300, [Windows.SystemParameters]::WorkArea.Height - 190)

$converter = New-Object Windows.Media.BrushConverter
$brushes = @{
    Green = $converter.ConvertFromString('#22C55E'); Yellow = $converter.ConvertFromString('#EAB308')
    Red = $converter.ConvertFromString('#EF4444'); Text = $converter.ConvertFromString('#E6E9EF')
    Muted = $converter.ConvertFromString('#8A93A6'); Section = $converter.ConvertFromString('#6B7385')
    Group = $converter.ConvertFromString('#AEB6C6'); Link = $converter.ConvertFromString('#7AA2F7')
    Chip = $converter.ConvertFromString('#272C37'); ChipBorder = $converter.ConvertFromString('#343A46')
}

# ---- Background work: one worker runspace with the module loaded; a timer collects the result on the UI thread.
$sessionState = [Management.Automation.Runspaces.InitialSessionState]::CreateDefault()
$sessionState.ExecutionPolicy = 'Bypass'
$sessionState.ImportPSModule($modulePath)
$worker = [runspacefactory]::CreateRunspace($sessionState)
$worker.Open()
Import-Module $modulePath   # quick calls on the UI thread (cached inventory, opening apps)

$script:job = $null
$script:splashText = ''
$script:splashTicks = 0
$script:expanded = @{}
$script:lastStatus = $null
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(250)

function Show-Message {
    param([string]$Text, $Color)
    $ui.Message.Text = $Text
    $ui.Message.Foreground = $Color
    $ui.Message.Visibility = if ($Text) { 'Visible' } else { 'Collapsed' }
}

function Show-Splash {
    # The splash artwork replaces the lists while ShowStack searches for tools and servers.
    param([string]$Text)
    $script:splashText = $Text
    $script:splashTicks = 0
    $ui.SplashText.Text = $Text
    $ui.Splash.Visibility = 'Visible'
    $ui.Content.Visibility = 'Collapsed'
}

function Hide-Splash {
    $script:splashText = ''
    $ui.Splash.Visibility = 'Collapsed'
    $ui.Content.Visibility = 'Visible'
}

function Set-Busy {
    param([bool]$Busy, [string]$Text = '')
    foreach ($control in $ui.CheckButton, $ui.RestartButton, $ui.RescanButton, $ui.Rows, $ui.Chips) { $control.IsEnabled = -not $Busy }
    Show-Message -Text $Text -Color $brushes.Muted
}

function Invoke-Background {
    # $Body runs in the worker; every job ends by returning fresh status plus any failure message.
    param([string]$Body = '', [object[]]$Arguments = @(), [string]$Text, [switch]$Splash)

    if ($script:job) { return }
    if ($Splash) {
        Show-Splash -Text $Text
        Set-Busy -Busy $true
    }
    else {
        Set-Busy -Busy $true -Text $Text
    }
    $script_ = "param(`$arg)`n`$failure = `$null`ntry {`n$Body`n} catch { `$failure = `$_.Exception.Message }`n" +
        "[pscustomobject]@{ Status = Get-DevStatus; Failure = `$failure }"
    $ps = [powershell]::Create()
    $ps.Runspace = $worker
    [void]$ps.AddScript($script_)
    foreach ($argument in $Arguments) { [void]$ps.AddArgument($argument) }
    $script:job = [pscustomobject]@{ PS = $ps; Handle = $ps.BeginInvoke() }
    $timer.Start()
}

$timer.Add_Tick({
    if ($script:splashText) {
        $script:splashTicks++
        $ui.SplashText.Text = $script:splashText + ('.' * ([Math]::Floor($script:splashTicks / 2) % 4))
    }
    if (-not $script:job -or -not $script:job.Handle.IsCompleted) { return }
    $timer.Stop()
    $current = $script:job
    $script:job = $null
    $result = $null
    $failure = $null
    try {
        $result = @($current.PS.EndInvoke($current.Handle)) | Select-Object -First 1
        $failure = $result.Failure
    }
    catch {
        $failure = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
    }
    finally {
        $current.PS.Dispose()
    }

    Set-Busy -Busy $false
    Hide-Splash
    if ($result -and $result.Status) { Show-Status -Status $result.Status }
    if ($failure) {
        Write-WidgetLog $failure
        Show-Message -Text $failure -Color $brushes.Red
    }
})

# ---- Rendering
function New-Text {
    param([string]$Text, $Brush, [double]$Size = 12, [string]$Weight = 'Normal')
    $block = New-Object Windows.Controls.TextBlock
    $block.Text = $Text
    $block.Foreground = $Brush
    $block.FontSize = $Size
    $block.FontWeight = $Weight
    $block.VerticalAlignment = 'Center'
    return $block
}

function New-Light {
    param([string]$State, [double]$Size = 10)
    $light = New-Object Windows.Shapes.Ellipse
    $light.Width = $Size; $light.Height = $Size
    $light.Fill = $brushes[$State]
    $light.VerticalAlignment = 'Center'
    return $light
}

function Show-Tools {
    param([object[]]$Tools)

    $ui.Chips.Children.Clear()
    $installed = @($Tools | Where-Object { $_.State -ne 'Red' }).Count
    $ui.ToolsHeader.Text = "TOOLS  ($installed found)"
    foreach ($tool in $Tools) {
        $chip = New-Object Windows.Controls.Border
        $chip.Background = $brushes.Chip
        $chip.BorderBrush = if ($tool.State -eq 'Red') { $brushes.Red } else { $brushes.ChipBorder }
        $chip.BorderThickness = 1
        $chip.CornerRadius = 5
        $chip.Padding = '6,2,7,2'
        $chip.Margin = '0,0,5,5'
        $panel = New-Object Windows.Controls.StackPanel
        $panel.Orientation = 'Horizontal'
        $light = New-Light -State $tool.State -Size 7
        $light.Margin = '0,0,5,0'
        [void]$panel.Children.Add($light)
        [void]$panel.Children.Add((New-Text -Text $tool.Name -Brush $brushes.Text -Size 11))
        $chip.Child = $panel

        $tip = @("$($tool.Name)$(if ($tool.Version) { " $($tool.Version)" })", $tool.Category, $tool.Detail)
        if ($tool.Path) { $tip += $tool.Path }
        if ($tool.Open) { $tip += 'Click to open' }
        elseif ($tool.Install) { $tip += 'Click to install' }
        $chip.ToolTip = ($tip | Where-Object { $_ } | Select-Object -Unique) -join "`n"
        if ($tool.Open -or $tool.Install) {
            $chip.Cursor = [Windows.Input.Cursors]::Hand
            $chip.Tag = $tool
            $chip.Add_MouseLeftButtonUp({ param($sender) Invoke-ToolAction -Tool $sender.Tag })
        }
        [void]$ui.Chips.Children.Add($chip)
    }
}

function Get-CodeFolder {
    # Where a server's code lives. A launch.json entry often sits in another folder than the script it runs.
    param($Server)
    if ($Server.Signature -match '^[A-Za-z]:\\' -and (Test-Path -LiteralPath $Server.Signature -PathType Leaf)) {
        return Split-Path $Server.Signature -Parent
    }
    return $Server.Dir
}

function New-FolderIcon {
    param([string]$Path)

    $shape = New-Object Windows.Shapes.Path
    $shape.Data = [Windows.Media.Geometry]::Parse('M0,1.5 C0,0.7 0.7,0 1.5,0 L4.5,0 L6,1.5 L11.5,1.5 C12.3,1.5 13,2.2 13,3 L13,8.5 C13,9.3 12.3,10 11.5,10 L1.5,10 C0.7,10 0,9.3 0,8.5 Z')
    $shape.Fill = $brushes.Section
    $icon = New-Object Windows.Controls.Border
    $icon.Child = $shape
    $icon.Background = [Windows.Media.Brushes]::Transparent   # the whole box is clickable, not just the shape
    $icon.Padding = '1,2,4,2'
    $icon.VerticalAlignment = 'Center'
    $icon.Cursor = [Windows.Input.Cursors]::Hand
    $icon.Tag = $Path
    $icon.ToolTip = "Open in File Explorer:`n$Path"
    $icon.Add_MouseEnter({ param($sender) $sender.Child.Fill = $brushes.Group })
    $icon.Add_MouseLeave({ param($sender) $sender.Child.Fill = $brushes.Section })
    $icon.Add_MouseLeftButtonUp({
        param($sender, $e)
        $e.Handled = $true   # don't also collapse or expand the project group
        if (Test-Path -LiteralPath $sender.Tag) { Start-Process explorer.exe -ArgumentList "`"$($sender.Tag)`"" }
        else { Show-Message -Text "That folder no longer exists: $($sender.Tag). Click CHECK STATUS to refresh the list." -Color $brushes.Red }
    })
    return $icon
}

function New-SectionHeader {
    param([string]$Text)
    $header = New-Text -Text $Text.ToUpperInvariant() -Brush $brushes.Section -Size 10 -Weight 'SemiBold'
    $header.Margin = '0,10,0,3'
    return $header
}

function New-Row {
    param($Item)

    $row = New-Object Windows.Controls.Grid
    $row.Margin = '0,2,0,2'
    # Columns: folder icon (servers only), light, name, detail, button.
    foreach ($width in [Windows.GridLength]::Auto, (New-Object Windows.GridLength 16), [Windows.GridLength]::Auto,
            (New-Object Windows.GridLength 1, ([Windows.GridUnitType]::Star)), [Windows.GridLength]::Auto) {
        $column = New-Object Windows.Controls.ColumnDefinition
        $column.Width = $width
        [void]$row.ColumnDefinitions.Add($column)
    }
    [void]$row.RowDefinitions.Add((New-Object Windows.Controls.RowDefinition))
    [void]$row.RowDefinitions.Add((New-Object Windows.Controls.RowDefinition))

    if ($Item.Server) {
        [void]$row.Children.Add((New-FolderIcon -Path (Get-CodeFolder -Server $Item.Server)))
    }
    $light = New-Light -State $Item.State
    $light.ToolTip = $Item.State
    [Windows.Controls.Grid]::SetColumn($light, 1)
    [void]$row.Children.Add($light)

    $name = New-Text -Text $Item.Name -Brush $brushes.Text -Weight 'SemiBold'
    $name.MaxWidth = 210
    $name.TextTrimming = 'CharacterEllipsis'
    if ($Item.Url) {
        $name.Foreground = $brushes.Link
        $name.Cursor = [Windows.Input.Cursors]::Hand
        $name.Tag = $Item.Url
        $name.ToolTip = "Open $($Item.Url)"
        $name.Add_MouseLeftButtonUp({ param($sender) Start-Process $sender.Tag })
    }
    [Windows.Controls.Grid]::SetColumn($name, 2)
    [void]$row.Children.Add($name)

    $detail = New-Text -Text $Item.Detail -Brush $brushes.Muted -Size 11
    $detail.ToolTip = $Item.Detail
    if ($Item.State -eq 'Red') {
        # Problems get the full text on a second line instead of being cut off.
        $detail.TextWrapping = 'Wrap'
        $detail.Foreground = $brushes.Red
        $detail.Margin = '0,1,0,2'
        [Windows.Controls.Grid]::SetRow($detail, 1)
        [Windows.Controls.Grid]::SetColumn($detail, 2)
        [Windows.Controls.Grid]::SetColumnSpan($detail, 3)
    }
    else {
        $detail.TextTrimming = 'CharacterEllipsis'
        $detail.Margin = '8,0,6,0'
        [Windows.Controls.Grid]::SetColumn($detail, 3)
    }
    [void]$row.Children.Add($detail)

    if ($Item.Action) {
        $button = New-Object Windows.Controls.Button
        $button.Style = $window.FindResource('Small')
        $button.Content = $Item.Action
        $button.Tag = $Item
        $button.Add_Click({ param($sender) Invoke-RowAction -Item $sender.Tag })
        [Windows.Controls.Grid]::SetColumn($button, 4)
        [void]$row.Children.Add($button)
    }

    if ($Item.Server) {
        $menu = New-Object Windows.Controls.ContextMenu
        foreach ($entry in @(@('Hide from list', 'Hide'), @('Open folder', 'Folder'))) {
            $menuItem = New-Object Windows.Controls.MenuItem
            $menuItem.Header = $entry[0]
            $menuItem.Tag = @($entry[1], $Item)
            $menuItem.Add_Click({ param($sender) Invoke-MenuAction -Action $sender.Tag[0] -Item $sender.Tag[1] })
            [void]$menu.Items.Add($menuItem)
        }
        $row.ContextMenu = $menu
        $row.Background = [Windows.Media.Brushes]::Transparent   # so a right-click anywhere on the row opens the menu
    }
    return $row
}

function Show-Status {
    param($Status)

    $script:lastStatus = $Status
    Show-Tools -Tools @($Status.Tools)
    $ui.Rows.Children.Clear()
    $rows = @($Status.Rows | Where-Object { $_ })

    foreach ($section in 'Services', 'Servers', 'Other local servers') {
        $sectionRows = @($rows | Where-Object Section -eq $section)
        if (-not $sectionRows.Count -and -not ($section -eq 'Servers' -and $Status.Hidden)) { continue }
        $header = New-SectionHeader -Text $section
        if ($section -eq 'Servers' -and $Status.Hidden) {
            $header.Text += "   ($($Status.Hidden) hidden, right-click to show)"
            $menu = New-Object Windows.Controls.ContextMenu
            $menuItem = New-Object Windows.Controls.MenuItem
            $menuItem.Header = 'Show hidden servers again'
            $menuItem.Add_Click({ Invoke-Background -Body 'Clear-HiddenServers' -Text 'Showing hidden servers...' })
            [void]$menu.Items.Add($menuItem)
            $header.ContextMenu = $menu
        }
        [void]$ui.Rows.Children.Add($header)

        if ($section -ne 'Servers') {
            foreach ($item in $sectionRows) { [void]$ui.Rows.Children.Add((New-Row -Item $item)) }
            continue
        }

        # Servers: one collapsible group per project. Open by default when something in it runs, needs attention,
        # or was registered by Claude Code.
        foreach ($group in ($sectionRows | Group-Object Group)) {
            $items = @($group.Group)
            $running = @($items | Where-Object State -eq 'Green').Count
            $problem = @($items | Where-Object State -eq 'Red').Count
            if (-not $script:expanded.ContainsKey($group.Name)) {
                $script:expanded[$group.Name] = [bool]($running -or $problem -or @($items | Where-Object { $_.Server.Registered }).Count)
            }
            $open = $script:expanded[$group.Name]

            $groupHeader = New-Object Windows.Controls.StackPanel
            $groupHeader.Orientation = 'Horizontal'
            $groupHeader.Margin = '0,4,0,1'
            $groupHeader.Cursor = [Windows.Input.Cursors]::Hand
            $groupHeader.Background = [Windows.Media.Brushes]::Transparent
            $groupHeader.Tag = $group.Name
            $groupHeader.ToolTip = if ($open) { 'Click to collapse' } else { 'Click to expand' }
            $state = if ($problem) { 'Red' } elseif ($running) { 'Green' } else { 'Yellow' }
            $projectFolder = @($items | ForEach-Object { $_.Server.GroupPath } | Where-Object { $_ } | Select-Object -First 1)
            if ($projectFolder) { [void]$groupHeader.Children.Add((New-FolderIcon -Path $projectFolder[0])) }
            $light = New-Light -State $state -Size 7
            $light.Margin = '2,0,7,0'
            [void]$groupHeader.Children.Add($light)
            [void]$groupHeader.Children.Add((New-Text -Text "$(if ($open) { [char]0x25BE } else { [char]0x25B8 }) $($group.Name)" -Brush $brushes.Group -Size 12 -Weight 'SemiBold'))
            [void]$groupHeader.Children.Add((New-Text -Text "   $running of $($items.Count) running" -Brush $brushes.Muted -Size 11))
            $groupHeader.Add_MouseLeftButtonUp({
                param($sender)
                $script:expanded[$sender.Tag] = -not $script:expanded[$sender.Tag]
                Show-Status -Status $script:lastStatus
            })
            [void]$ui.Rows.Children.Add($groupHeader)

            if ($open) {
                foreach ($item in $items) {
                    $row = New-Row -Item $item
                    $row.Margin = '14,2,0,2'
                    [void]$ui.Rows.Children.Add($row)
                }
            }
        }
    }

    $servers = @($rows | Where-Object Section -eq 'Servers')
    $parts = @("$(@($Status.Tools | Where-Object { $_.State -ne 'Red' }).Count) tools", "$(@($servers | Where-Object State -eq 'Green').Count) of $($servers.Count) servers running")
    $problems = @($rows + @($Status.Tools) | Where-Object { $_.State -eq 'Red' }).Count
    if ($problems) { $parts += "$problems need attention" }
    $ui.Summary.Text = "Checked {0:h:mm tt}  -  {1}" -f (Get-Date), ($parts -join ', ')
}

# ---- Actions
function Invoke-ToolAction {
    param($Tool)
    if ($Tool.Open -and $Tool.State -ne 'Red') {
        try { Open-DevTool -Tool $Tool } catch { Show-Message -Text $_.Exception.Message -Color $brushes.Red }
        return
    }
    if ($Tool.Install) {
        $answer = [Windows.MessageBox]::Show("Install $($Tool.Name)?`n`nThis opens a window that runs:`n$($Tool.Install)", 'ShowStack', 'YesNo', 'Question')
        if ($answer -eq 'Yes') {
            Invoke-Background -Body 'Install-DevTool -Name $arg; Update-DevInventory | Out-Null' -Arguments @($Tool.Name) -Text "Installing $($Tool.Name) in a separate window. Follow it there; the boxes update when you close it."
        }
    }
}

function Invoke-RowAction {
    param($Item)
    switch ($Item.Action) {
        'Start' {
            if ($Item.Section -eq 'Services') {
                Invoke-Background -Body 'Start-DockerEngine' -Text 'Starting Docker Desktop (can take up to 2 minutes)...'
            }
            else {
                Invoke-Background -Body 'Start-DevServer -Server $arg' -Arguments @(, $Item.Server) -Text "Starting $($Item.Server.Project): $($Item.Name)..."
            }
        }
        'Restart' {
            Invoke-Background -Body 'Start-DevServer -Server $arg' -Arguments @(, $Item.Server) -Text "Restarting $($Item.Server.Project): $($Item.Name)..."
        }
        'Install' {
            Invoke-Background -Body 'Install-ServerDependencies -Server $arg' -Arguments @(, $Item.Server) -Text "Installing packages for $($Item.Server.Project): $($Item.Name) in a separate window. The list updates when you close it."
        }
    }
}

function Invoke-MenuAction {
    param([string]$Action, $Item)
    switch ($Action) {
        'Hide' { Invoke-Background -Body 'Hide-DevServer -Key $arg' -Arguments @($Item.Server.Key) -Text "Hiding $($Item.Name)..." }
        'Folder' { Start-Process explorer.exe -ArgumentList "`"$(Get-CodeFolder -Server $Item.Server)`"" }
    }
}

$ui.CheckButton.Add_Click({ Invoke-Background -Body 'Update-DevInventory -ServersOnly | Out-Null' -Text 'Looking for servers in your projects and checking what is running...' })
$ui.RescanButton.Add_Click({ Invoke-Background -Body 'Update-DevInventory | Out-Null' -Text 'Searching for installed tools and servers' -Splash })
$ui.RestartButton.Add_Click({ Invoke-Background -Body 'Restart-AllDev' -Text 'Starting Docker if needed, then restarting servers...' })
$ui.MinButton.Add_Click({ $window.WindowState = 'Minimized' })
$ui.CloseButton.Add_Click({ $window.Close() })
$ui.Header.Add_MouseLeftButtonDown({ $window.DragMove() })

# ---- Position: last place it was left, otherwise the top-right corner of the screen.
$workArea = [Windows.SystemParameters]::WorkArea
$window.Left = $workArea.Right - $window.Width - 24
$window.Top = $workArea.Top + 24
if (Test-Path -LiteralPath $positionFile -PathType Leaf) {
    try {
        $saved = Get-Content -Raw -LiteralPath $positionFile | ConvertFrom-Json
        $screenRight = [Windows.SystemParameters]::VirtualScreenLeft + [Windows.SystemParameters]::VirtualScreenWidth
        $screenBottom = [Windows.SystemParameters]::VirtualScreenTop + [Windows.SystemParameters]::VirtualScreenHeight
        if ($saved.Left -ge [Windows.SystemParameters]::VirtualScreenLeft -and $saved.Left -lt ($screenRight - 80) -and
            $saved.Top -ge [Windows.SystemParameters]::VirtualScreenTop -and $saved.Top -lt ($screenBottom - 80)) {
            $window.Left = $saved.Left
            $window.Top = $saved.Top
        }
    }
    catch { }
}

$window.Add_Closing({
    try { @{ Left = $window.Left; Top = $window.Top } | ConvertTo-Json | Set-Content -LiteralPath $positionFile -Encoding UTF8 } catch { }
})

# On open: the splash shows while ShowStack checks status (servers in the code, what is running).
# Tools are scanned only the first time, or when Rescan tools is clicked; otherwise the saved list is used.
$window.Add_ContentRendered({
    $cached = $null
    try { $cached = Get-DevInventory } catch { }
    if ($cached -and @($cached.Tools).Count) {
        Show-Tools -Tools @($cached.Tools)
        Invoke-Background -Body 'Update-DevInventory -ServersOnly | Out-Null' -Text 'Searching for servers' -Splash
    }
    else {
        Invoke-Background -Body 'Update-DevInventory | Out-Null' -Text 'Searching for tools and servers' -Splash
    }
})

$window.Dispatcher.Add_UnhandledException({
    param($sender, $e)
    Write-WidgetLog $e.Exception.ToString()
    Show-Message -Text $e.Exception.Message -Color $brushes.Red
    $e.Handled = $true
})

try {
    [void]$window.ShowDialog()
}
catch {
    Write-WidgetLog $_.Exception.ToString()
    [Windows.MessageBox]::Show($_.Exception.Message, 'ShowStack') | Out-Null
}
finally {
    $timer.Stop()
    $worker.Dispose()
    $mutex.ReleaseMutex()
}
