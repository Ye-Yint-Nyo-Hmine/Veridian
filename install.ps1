<#
.SYNOPSIS
Veridian installer for Windows.

.DESCRIPTION
Installs a versioned copy of Veridian under $env:VERIDIAN_HOME (default ~\.veridian), gives it its
own virtual environment, and puts a `veridian` launcher on PATH. Re-running is the upgrade path:
the new version is staged and smoke-tested in full before the one-line `current` pointer is
flipped, so a failed upgrade leaves the working install untouched.

.EXAMPLE
irm https://raw.githubusercontent.com/Ye-Yint-Nyo-Hmine/Veridian/main/install.ps1 | iex

.EXAMPLE
# With options, since `iex` cannot pass parameters:
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Ye-Yint-Nyo-Hmine/Veridian/main/install.ps1))) -Version 0.1.0
#>
[CmdletBinding()]
param(
    [string] $Version = $(if ($env:VERIDIAN_VERSION) { $env:VERIDIAN_VERSION } else { "latest" }),
    [string] $Tarball = $env:VERIDIAN_TARBALL,
    [switch] $NoModifyPath,
    [switch] $Force
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "Ye-Yint-Nyo-Hmine/Veridian"
$PythonVersion = "3.13"

function Say  { param([string] $m) Write-Host $m }
function Info { param([string] $m) Write-Host "  $m" -ForegroundColor DarkGray }
function Die  { param([string] $m) Write-Host "veridian: $m" -ForegroundColor Red; exit 1 }
function Have { param([string] $n) [bool](Get-Command $n -ErrorAction SilentlyContinue) }

# PowerShell's path parser reads the "~1" in an 8.3 short name as a home reference and refuses the
# path outright ("An object at the specified path C:\Users\YEYINT~1 does not exist"). Windows hands
# out short names routinely - %TEMP% is one whenever the account name contains a space - so expand
# to the long form once, up front, and let every cmdlet below see an ordinary path.
Add-Type -Namespace Veridian -Name Native -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
public static extern uint GetLongPathName(string lpszShortPath, System.Text.StringBuilder lpszLongPath, uint cchBuffer);
'@

function Expand-ShortPath {
    param([string] $Path)
    if ($Path -notmatch '~\d') { return $Path }
    # GetLongPathName only resolves a path that exists; .NET creates it without cmdlet parsing.
    [System.IO.Directory]::CreateDirectory($Path) | Out-Null
    $sb = New-Object System.Text.StringBuilder 32767
    $n = [Veridian.Native]::GetLongPathName($Path, $sb, $sb.Capacity)
    if ($n -gt 0 -and $n -lt $sb.Capacity) { return $sb.ToString() }
    return $Path
}

$requestedHome = if ($env:VERIDIAN_HOME) { $env:VERIDIAN_HOME } else { Join-Path $HOME ".veridian" }
$VeridianHome = Expand-ShortPath $requestedHome

# Prefer the bsdtar that ships in System32 over whatever `tar` PATH resolves to. With Git for
# Windows installed - which is to say, on most developer machines - `tar` is often MSYS tar, and
# MSYS tar reads "C:\..." as a remote host spec and refuses to open the archive.
$TarExe = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $TarExe)) {
    $found = Get-Command tar -ErrorAction SilentlyContinue
    if (-not $found) { Die "tar.exe is required (ships with Windows 10 1803 and later)" }
    $TarExe = $found.Source
}

# ------------------------------------------------------------------------ uv

function Ensure-Uv {
    if (Have "uv") { return }
    Say "uv not found - installing it (Veridian uses it to manage interpreters and brick environments)"
    try {
        & ([scriptblock]::Create((Invoke-RestMethod "https://astral.sh/uv/install.ps1"))) | Out-Null
    } catch {
        Die "could not install uv; install it from https://docs.astral.sh/uv/ and re-run"
    }
    foreach ($d in @("$HOME\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
        if (Test-Path (Join-Path $d "uv.exe")) { $env:Path = "$d;$env:Path" }
    }
    if (-not (Have "uv")) { Die "uv installed but is not on PATH; open a new shell and re-run" }
}

Ensure-Uv

# ------------------------------------------------------------------- download

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("veridian-install-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
[System.IO.Directory]::CreateDirectory($tmp) | Out-Null
$tmp = Expand-ShortPath $tmp

try {
    $archive = Join-Path $tmp "veridian.tar.gz"

    if ($Tarball) {
        if (-not (Test-Path $Tarball)) { Die "no such tarball: $Tarball" }
        Say "Installing Veridian from $Tarball"
        Copy-Item $Tarball $archive
        # A local tarball is trusted as given; its version comes from the tree it unpacks to.
        $ver = "local"
    } else {
        if ($Version -eq "latest") {
            try {
                $ver = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
            } catch {
                Die "could not determine the latest release of $Repo (try -Version <v>)"
            }
        } else {
            $ver = $Version
        }
        $ver = $ver -replace '^v', ''
        Say "Installing Veridian $ver"

        $name = "veridian-$ver.tar.gz"
        $base = "https://github.com/$Repo/releases/download/v$ver"
        Info "downloading $name"
        try {
            Invoke-WebRequest "$base/$name" -OutFile $archive -UseBasicParsing
            Invoke-WebRequest "$base/SHA256SUMS" -OutFile (Join-Path $tmp "SHA256SUMS") -UseBasicParsing
        } catch {
            Die "could not download $base/$name"
        }

        $line = Get-Content (Join-Path $tmp "SHA256SUMS") | Where-Object { $_ -match "\s\*?$([regex]::Escape($name))$" } | Select-Object -First 1
        if (-not $line) { Die "SHA256SUMS has no entry for $name" }
        $expected = ($line -split '\s+')[0]
        $actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
        if ($expected.ToLower() -ne $actual) {
            Die "checksum mismatch for $name (expected $expected, got $actual)"
        }
        Info "checksum verified"
    }

    # -------------------------------------------------------------- extract

    $stageApp = Join-Path $tmp "app"
    New-Item -ItemType Directory -Path $stageApp | Out-Null
    & $TarExe -xzf $archive -C $stageApp --strip-components=1
    if ($LASTEXITCODE -ne 0) { Die "could not extract the archive" }
    if (-not (Test-Path (Join-Path $stageApp "pyproject.toml"))) {
        Die "archive does not look like a Veridian tree"
    }

    if ($ver -eq "local") {
        $init = Get-Content (Join-Path $stageApp "src\veridian\__init__.py") -Raw
        if ($init -match '__version__\s*=\s*"([^"]+)"') { $ver = $Matches[1] } else {
            Die "could not read the version out of the archive"
        }
        Info "version $ver"
    }

    $target = Join-Path $VeridianHome "versions\$ver"
    $currentFile = Join-Path $VeridianHome "current"
    $active = if (Test-Path $currentFile) { (Get-Content $currentFile -Raw).Trim() } else { "" }
    if ((Test-Path $target) -and (-not $Force) -and ($active -eq $ver)) {
        Say "Veridian $ver is already installed (use -Force to reinstall)"
        exit 0
    }

    # ------------------------------------------------------------ environment

    # Everything below is built at its final path. A virtual environment records absolute paths and
    # does not survive being moved, so it cannot be assembled in the temp directory and relocated.
    # Building a *new* version directory still leaves the running install alone: `current` keeps
    # pointing at the old version until the very last step.
    #
    # The exception is -Force over the version that is currently live, which has to overwrite it.
    # If that fails, drop the pointer too: "no installation found" is a far better state to land in
    # than a pointer naming a directory that is now half-built.
    $rebuildingActive = ($active -eq $ver)
    function Abandon {
        param([string] $m)
        Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
        if ($rebuildingActive) {
            Remove-Item $currentFile -Force -ErrorAction SilentlyContinue
            Die "$m - and the previous $ver install was replaced by this attempt, so re-run to restore it"
        }
        Die $m
    }

    New-Item -ItemType Directory -Path (Join-Path $VeridianHome "versions") -Force | Out-Null
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Item -ItemType Directory -Path $target | Out-Null
    $appDir = Join-Path $target "app"
    $venvDir = Join-Path $target "venv"
    Move-Item $stageApp $appDir

    Info "creating the environment (python $PythonVersion)"
    & uv venv --python $PythonVersion --quiet $venvDir
    if ($LASTEXITCODE -ne 0) { Abandon "could not create a Python $PythonVersion environment" }

    Info "installing dependencies"
    # A PEP 508 direct reference, rather than a bare path: "$appDir[providers]" would parse as an
    # index expression, and changing directory first would put the path back through PowerShell's
    # parser for no benefit.
    $appUri = ([Uri] $appDir).AbsoluteUri
    & uv pip install --quiet --python $venvDir "veridian[providers] @ $appUri"
    if ($LASTEXITCODE -ne 0) { Abandon "could not install Veridian into its environment" }

    # Smoke-test before the pointer moves, so a broken build never becomes the active one.
    & (Join-Path $venvDir "Scripts\veridian.exe") --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Abandon "the freshly installed veridian failed to start; leaving your current install alone"
    }

    # The pointer file is written last: it is what makes this version the live one.
    $tmpPointer = "$currentFile.tmp"
    Set-Content -Path $tmpPointer -Value $ver -Encoding ascii -NoNewline
    Move-Item $tmpPointer $currentFile -Force

    # --------------------------------------------------------------- launcher

    # Copy the venv's real console-script .exe rather than writing a .cmd wrapper: a batch wrapper
    # would intercept Ctrl-C, and Ctrl-C is how you interrupt a running goal without ending the
    # session. The launcher embeds an absolute interpreter path, so it works from anywhere.
    $binDir = Join-Path $VeridianHome "bin"
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $launcher = Join-Path $binDir "veridian.exe"
    Copy-Item (Join-Path $target "venv\Scripts\veridian.exe") $launcher -Force

    & $launcher --version | Out-Null
    if ($LASTEXITCODE -eq 0) {
        # An earlier install may have fallen back to the shim; .exe wins on PATHEXT either way,
        # but leaving a stale one behind invites confusion when diagnosing PATH problems.
        Remove-Item (Join-Path $binDir "veridian.cmd") -Force -ErrorAction SilentlyContinue
    } else {
        # Fall back to a batch shim if the copied launcher cannot find its interpreter.
        Remove-Item $launcher -Force
        $launcher = Join-Path $binDir "veridian.cmd"
        @"
@echo off
setlocal
if "%VERIDIAN_HOME%"=="" set "VERIDIAN_HOME=$VeridianHome"
set /p _VER=<"%VERIDIAN_HOME%\current"
if not defined _VER echo veridian: no installation found under %VERIDIAN_HOME% 1>&2 & exit /b 1
"%VERIDIAN_HOME%\versions\%_VER%\venv\Scripts\veridian.exe" %*
exit /b %ERRORLEVEL%
"@ | Set-Content -Path $launcher -Encoding ascii
    }

    # ------------------------------------------------------------------- PATH

    $onPath = ($env:Path -split ';' | Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') }).Count -gt 0
    $pathAdded = $false

    if (-not $NoModifyPath -and -not $onPath) {
        # Read the UNEXPANDED user PATH straight from the registry and write it back with the same
        # value kind. Going through setx would truncate it at 1024 characters, and expanding it
        # would bake %USERPROFILE%-style entries into literals.
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
        try {
            $raw = $key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $kind = try { $key.GetValueKind("Path") } catch { [Microsoft.Win32.RegistryValueKind]::ExpandString }
            $already = $raw -split ';' | Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') }
            if (-not $already) {
                $new = if ([string]::IsNullOrWhiteSpace($raw)) { $binDir } else { $raw.TrimEnd(';') + ";" + $binDir }
                $key.SetValue("Path", $new, $kind)
                $pathAdded = $true

                # Tell the shell the environment changed. Without this, nothing picks the new PATH
                # up until you log out. Note it only reaches apps started *after* this point: a
                # terminal already open keeps the copy it inherited when it launched, which is why
                # the message below says to restart it rather than to open a new tab.
                try {
                    Add-Type -Namespace Veridian -Name Env -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
'@
                    $res = [System.UIntPtr]::Zero
                    # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG, 5s
                    [Veridian.Env]::SendMessageTimeout([System.IntPtr]0xffff, 0x1A, [System.IntPtr]::Zero, "Environment", 2, 5000, [ref]$res) | Out-Null
                } catch {
                    # Cosmetic only — the registry write above is what actually matters.
                }
            }
        } finally {
            $key.Close()
        }
        $env:Path = "$binDir;$env:Path"
    }

    # ------------------------------------------------------------------- done

    Say ""
    Say "Veridian $ver installed."
    Info "root      $target\app"
    Info "launcher  $launcher"
    Say ""
    if ($onPath) {
        Say "Run 'veridian doctor' to check your setup, then 'veridian' in any project directory."
    } elseif ($pathAdded) {
        Say "Added $binDir to your user PATH."
        Say "Quit and reopen your terminal app - a new tab in an already-running window keeps the"
        Say "old PATH - then run 'veridian doctor'. To use it in this window right now:"
        Info '$env:Path = [Environment]::GetEnvironmentVariable(''Path'',''Machine'') + '';'' + [Environment]::GetEnvironmentVariable(''Path'',''User'')'
    } else {
        Say "Add this to your PATH, then run 'veridian doctor':"
        Info $binDir
    }
    Say ""
    Say "Veridian treats the directory you run it from as its workspace."
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
