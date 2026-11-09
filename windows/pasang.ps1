# HA Helper - pasang agar berjalan terus di latar (Task Scheduler) + buka firewall port dashboard.
# Dijalankan lewat pasang-jalan-terus.bat (otomatis meminta hak Administrator).
$ErrorActionPreference = "Stop"
$dir  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$task = "HA Helper"
Write-Host "`n== PASANG HA HELPER AGAR BERJALAN TERUS ==`n"

# 1. cari pythonw.exe (python tanpa jendela)
$py = $null
foreach ($c in @("py -3", "python")) {
  try {
    $exe = & cmd /c "$c -c ""import sys;print(sys.executable)""" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { $py = $exe.Trim(); break }
  } catch {}
}
if (-not $py) { throw "Python tidak ditemukan. Pasang Python 3 dulu (centang Add python.exe to PATH)." }
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Write-Host "Python      : $pyw"
Write-Host "Folder      : $dir"

# 2. sandi harus sudah disimpan (dicek dengan akun yang sama)
if (-not (Test-Path (Join-Path $dir "data\sandi.dat"))) {
  throw "Sandi belum disimpan. Jalankan simpan-sandi.bat dulu, lalu ulangi."
}

# 3. port dashboard dari config.ini
$port = 8099
$m = Select-String -Path (Join-Path $dir "config.ini") -Pattern '^\s*port_dashboard\s*=\s*(\d+)' | Select-Object -First 1
if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
Write-Host "Port        : $port`n"

# 4. akun Windows: tugas berjalan walau belum ada yang login
$user = "$env:USERDOMAIN\$env:USERNAME"
Write-Host "Masukkan sandi LOGIN WINDOWS akun $user"
Write-Host "(agar dashboard tetap jalan setelah PC restart walau belum login)."
$sec = Read-Host "Sandi Windows" -AsSecureString
$pw  = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

$act = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\jalankan.py`" --latar" -WorkingDirectory $dir
$trg = New-ScheduledTaskTrigger -AtStartup
$set = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
         -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
         -StartWhenAvailable -MultipleInstances IgnoreNew
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $task -Action $act -Trigger $trg -Settings $set `
  -User $user -Password $pw -RunLevel Limited -Force | Out-Null
$pw = $null
Write-Host "Tugas terjadwal '$task' dibuat (mulai saat PC menyala, hidup lagi bila berhenti)."

# 5. firewall Windows untuk port dashboard
Get-NetFirewallRule -DisplayName "HA Helper dashboard" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "HA Helper dashboard" -Direction Inbound -Protocol TCP -LocalPort $port `
  -Action Allow -Profile Domain,Private,Public | Out-Null
Write-Host "Firewall Windows: port $port dibuka."

# 6. hentikan yang sedang jalan di jendela hitam (bila ada), lalu mulai tugas
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 5
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -ExpandProperty IPAddress) -join ", "
try {
  Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/masuk" -TimeoutSec 5 | Out-Null
  Write-Host "`nBERHASIL. Dashboard berjalan di latar."
} catch {
  Write-Host "`nTugas dibuat, tetapi dashboard belum menjawab. Lihat data\helper.log"
}
Write-Host "  Dari PC ini       : http://127.0.0.1:$port"
Write-Host "  Dari komputer lain: http://<IP-PC>:$port   (IP PC ini: $ip)"
Write-Host "`nPastikan PC tidak tidur: Settings > System > Power > Sleep = Never."
