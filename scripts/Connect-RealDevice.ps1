param(
  [int]$WebPort = 8768,
  [int]$TelemetryPort = 8766,
  [int]$CommandPort = 8767,
  [int]$WaitSeconds = 45,
  [switch]$StopExisting,
  [switch]$NoBrowser,
  [switch]$TestLed,
  [string]$SerialPort = ""
)

$ErrorActionPreference = "Stop"

function Write-Step($Text) {
  Write-Host ""
  Write-Host "== $Text" -ForegroundColor Cyan
}

function Get-PythonPath($Root) {
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    return $venvPython
  }
  return "python"
}

function Stop-PortOwner($Protocol, $Port) {
  if ($Protocol -eq "TCP") {
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique
  } else {
    $owners = Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique
  }

  foreach ($owner in $owners) {
    if (-not $owner) { continue }
    Write-Host "Stopping process $owner on $Protocol port $Port"
    Stop-Process -Id $owner -Force
  }
}

function Assert-PortAvailable($Protocol, $Port) {
  if ($Protocol -eq "TCP") {
    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  } else {
    $busy = Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue
  }
  if ($busy) {
    throw "$Protocol port $Port is already in use. Re-run with -StopExisting or close the old console."
  }
}

function Wait-HttpReady($StatusUri, $Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    try {
      return Invoke-RestMethod -Uri $StatusUri -TimeoutSec 2
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  throw "Console did not become ready at $StatusUri"
}

function Get-HardwareStatus($StatusUri) {
  try {
    return Invoke-RestMethod -Uri $StatusUri -TimeoutSec 2
  } catch {
    return $null
  }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Step "AI Desk Phone real-device SOP"
Write-Host "Repo: $RepoRoot"
Write-Host "Web: http://127.0.0.1:$WebPort/#config"
Write-Host "UDP telemetry: $TelemetryPort"
Write-Host "UDP command:   $CommandPort"

Write-Step "Local network addresses"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notlike "127.*" -and $_.AddressState -eq "Preferred" } |
  Select-Object InterfaceAlias, IPAddress |
  Format-Table -AutoSize

if ($StopExisting) {
  Write-Step "Stopping old listeners"
  Stop-PortOwner "TCP" $WebPort
  Stop-PortOwner "UDP" $TelemetryPort
  Start-Sleep -Milliseconds 500
}

Assert-PortAvailable "TCP" $WebPort
Assert-PortAvailable "UDP" $TelemetryPort

$Python = Get-PythonPath $RepoRoot
Write-Step "Python runtime"
Write-Host $Python

& $Python -c "import serial, requests, websockets, sounddevice" | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Step "Installing Python dependencies"
  & $Python -m pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
  }
}

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stdout = Join-Path $LogDir "real-device-console.out.log"
$Stderr = Join-Path $LogDir "real-device-console.err.log"

$Args = @(
  "tools\ai_desk_phone_console.py",
  "--web-port", "$WebPort",
  "--udp-port", "$TelemetryPort",
  "--device-command-port", "$CommandPort",
  "--no-simulation"
)

if ($SerialPort.Trim()) {
  $Args += @("--port", $SerialPort.Trim())
} else {
  $Args += "--no-serial"
}

Write-Step "Starting console"
$Process = Start-Process `
  -FilePath $Python `
  -ArgumentList $Args `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $Stdout `
  -RedirectStandardError $Stderr `
  -PassThru

Write-Host "Process: $($Process.Id)"
Write-Host "Stdout:  $Stdout"
Write-Host "Stderr:  $Stderr"

$StatusUri = "http://127.0.0.1:$WebPort/api/hardware/status"
$Url = "http://127.0.0.1:$WebPort/#config"
$Status = Wait-HttpReady $StatusUri 15

if (-not $NoBrowser) {
  Start-Process $Url | Out-Null
}

Write-Step "Waiting for real ESP32 UDP packets"
Write-Host "Expected firmware ports: telemetry $TelemetryPort, command $CommandPort"
Write-Host "Power-cycle the phone/ESP32 now if it was already running."

$Deadline = (Get-Date).AddSeconds($WaitSeconds)
$Connected = $false
$LastStatus = $Status
while ((Get-Date) -lt $Deadline) {
  $LastStatus = Get-HardwareStatus $StatusUri
  if ($LastStatus -and $LastStatus.real_device_connected) {
    $Connected = $true
    break
  }
  Start-Sleep -Seconds 1
}

if ($Connected) {
  Write-Step "Connected"
  $Sample = $LastStatus.current_sample
  Write-Host "Device:       $($LastStatus.udp_device)"
  Write-Host "UDP age:      $([math]::Round([double]$LastStatus.udp_last_seen_seconds, 2))s"
  if ($Sample) {
    Write-Host "Hook state:   $($Sample.hook_label)"
    Write-Host "Digital:      $($Sample.digital)"
    Write-Host "ADC:          $($Sample.adc)"
    Write-Host "Wi-Fi IP:     $($Sample.wifi_ip)"
    Write-Host "Wi-Fi RSSI:   $($Sample.wifi_rssi)"
  }

  if ($TestLed) {
    Write-Step "Optional LED test"
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$WebPort/api/hardware/led_on" -TimeoutSec 3 | Out-Null
    Start-Sleep -Milliseconds 700
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$WebPort/api/hardware/led_off" -TimeoutSec 3 | Out-Null
    Write-Host "LED on/off command sent."
  }

  Write-Host ""
  Write-Host "Open: $Url" -ForegroundColor Green
  exit 0
}

Write-Step "Not connected yet"
Write-Host "The console is running, but no real ESP32 UDP packet arrived within $WaitSeconds seconds."
Write-Host ""
Write-Host "Checklist:"
Write-Host "1. ESP32 firmware must use UDP telemetry $TelemetryPort and command $CommandPort."
Write-Host "2. PC and ESP32 must be on the same LAN/VLAN; disable VPN if broadcast is blocked."
Write-Host "3. Windows Firewall must allow inbound UDP $TelemetryPort for python.exe."
Write-Host "4. Power-cycle ESP32 and watch 调试与校准 in the web page."
Write-Host "5. If still blank, connect USB serial once and inspect logs for Wi-Fi status."
Write-Host ""
Write-Host "Open: $Url"
Write-Host "Status: $StatusUri"
exit 2
