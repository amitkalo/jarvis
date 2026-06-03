$conns = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if ($conns) {
    $pids = $conns.OwningProcess | Sort-Object -Unique
    foreach ($p in $pids) {
        Write-Host "[Jarvis] Killing old backend (PID $p)..."
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 1500
} else {
    Write-Host "[Jarvis] Port 8765 already free."
}
