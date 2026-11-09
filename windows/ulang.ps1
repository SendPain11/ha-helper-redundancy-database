# HA Helper - mulai ulang (setelah mengubah config.ini atau menyimpan sandi baru).
$task = "HA Helper"
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*jalankan.py*--latar*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $task
Write-Host "`nHA Helper dimulai ulang."
