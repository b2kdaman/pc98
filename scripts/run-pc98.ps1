param(
    [string[]]$Image,
    [switch]$X86
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
if ((Split-Path -Leaf $ScriptDir) -ne 'scripts') {
    $Root = $ScriptDir
}
$EmulatorDir = Join-Path $Root 'emulator'
$ExeName = if ([Environment]::Is64BitOperatingSystem -and -not $X86) { 'np21x64w.exe' } else { 'np21w.exe' }
$ExePath = Join-Path $EmulatorDir $ExeName
$IniPath = Join-Path $EmulatorDir ([IO.Path]::ChangeExtension($ExeName, '.ini'))

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Missing emulator executable: $ExePath"
}

function Set-IniValue {
    param(
        [string]$Text,
        [string]$Key,
        [string]$Value
    )

    $escapedKey = [regex]::Escape($Key)
    if ($Text -match "(?m)^$escapedKey=") {
        return [regex]::Replace($Text, "(?m)^$escapedKey=.*$", { "$Key=$Value" })
    }

    return $Text.TrimEnd() + "`r`n$Key=$Value`r`n"
}

if ($Image) {
    $HardDiskExtensions = @('.hdi', '.thd', '.nhd', '.hdd', '.hdn', '.vhd')
    $FloppyExtensions = @('.d88', '.88d', '.d98', '.98d', '.fdi', '.fdd', '.2hd', '.tfd', '.hdm', '.xdf', '.dup', '.flp', '.img', '.ima')
    $IniText = Get-Content -LiteralPath $IniPath -Raw

    for ($i = 1; $i -le 4; $i++) {
        $IniText = Set-IniValue $IniText "FDD${i}FILE" ''
    }

    $FloppySlot = 1
    foreach ($ImagePath in $Image) {
        $ResolvedImage = (Resolve-Path -LiteralPath $ImagePath).Path
        $Extension = [IO.Path]::GetExtension($ResolvedImage).ToLowerInvariant()

        if ($HardDiskExtensions -contains $Extension) {
            $IniText = Set-IniValue $IniText 'HDD1FILE' $ResolvedImage
        } elseif ($FloppyExtensions -contains $Extension) {
            if ($FloppySlot -gt 4) {
                Write-Warning "Only four floppy drives can be pre-mounted. Skipping $ResolvedImage"
                continue
            }

            $IniText = Set-IniValue $IniText "FDD${FloppySlot}FILE" $ResolvedImage
            $FloppySlot++
        } else {
            Write-Warning "Unrecognized disk extension '$Extension'. Skipping $ResolvedImage"
        }
    }

    Set-Content -LiteralPath $IniPath -Value $IniText -Encoding Default
}

$Process = Start-Process -FilePath $ExePath -WorkingDirectory $EmulatorDir -PassThru
Write-Output $Process.Id
