$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$distRoot = Join-Path $projectRoot 'dist'
$workRoot = Join-Path $projectRoot '.pyinstaller-work'

Push-Location $projectRoot
try {
    & py -c 'import PyInstaller' 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller não está instalado. Instala-o com: py -m pip install pyinstaller'
    }

    & py tools/prepare_tk_bundle.py
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar os recursos Tcl/Tk.' }

    & py -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name 'FIS AFN Desktop' `
        --distpath $distRoot `
        --workpath $workRoot `
        --specpath $workRoot `
        --collect-submodules serial `
        --add-data "$workRoot/tk-resources/_tcl_data;_tcl_data" `
        --add-data "$workRoot/tk-resources/_tk_data;_tk_data" `
        desktop_app.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller terminou com código $LASTEXITCODE."
    }

    $exePath = Join-Path $distRoot 'FIS AFN Desktop.exe'
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        throw "Executável não foi criado: $exePath"
    }

    $exe = Get-Item -LiteralPath $exePath
    $hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
    Write-Host "Executável: $exePath"
    Write-Host "Tamanho: $($exe.Length) bytes"
    Write-Host "SHA-256: $hash"
    Write-Host 'Abre o executável para usar a janela nativa. Não requer Python nem navegador.'
}
finally {
    Pop-Location
}
