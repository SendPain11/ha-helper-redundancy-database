# HA Helper - hentikan dan hapus mode berjalan terus.
$task = "HA Helper"
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "HA Helper dashboard" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*jalankan.py*--latar*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "`nHA Helper tidak lagi berjalan di latar. Tugas terjadwal dan aturan firewall dihapus."
