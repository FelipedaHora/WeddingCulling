param([ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '1.1.0', [string]$IsccPath)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not $IsccPath) {
    $IsccPath = @("$PSScriptRoot\.tools\InnoSetup\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path $IsccPath)) { throw 'Instale Inno Setup 6 ou informe -IsccPath.' }
Push-Location $PSScriptRoot
try {
    $testTemp = Join-Path $PSScriptRoot ("build\tests-" + [guid]::NewGuid().ToString('N'))
    & $python -m pytest -q --basetemp $testTemp
    if ($LASTEXITCODE -ne 0) { throw 'Falha nos testes.' }
    & $python -m PyInstaller --noconfirm --clean WeddingCulling.spec
    if ($LASTEXITCODE -ne 0) { throw 'Falha no build.' }
    $check = Start-Process -FilePath "$PSScriptRoot\dist\WeddingCulling\WeddingCulling.exe" -ArgumentList '--self-test' -WindowStyle Hidden -PassThru
    if (-not $check.WaitForExit(60000)) {
        $check.Kill()
        throw 'Autoteste excedeu 60 segundos. Verifique as dependencias nativas.'
    }
    if ($check.ExitCode -ne 0) { throw 'Falha no autoteste.' }
    & $IsccPath "/DAppVersion=$Version" "$PSScriptRoot\installer\WeddingCulling.iss"
    if ($LASTEXITCODE -ne 0) { throw 'Falha na compilacao do instalador.' }
    $setup = "$PSScriptRoot\dist\installer\WeddingCulling-Setup-$Version-x64.exe"
    $hash = (Get-FileHash $setup -Algorithm SHA256).Hash
    "$hash  $(Split-Path $setup -Leaf)" | Set-Content "$setup.sha256" -Encoding ascii
    Write-Host "Instalador concluido: $setup"
} finally { Pop-Location }
