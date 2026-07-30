$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$running = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*Лиды на бане*bot.py*"
}

if ($running) {
    Write-Output "Бот уже запущен."
    exit 0
}

Start-Process -FilePath "python" -ArgumentList "bot.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Write-Output "Бот запущен в фоне."
