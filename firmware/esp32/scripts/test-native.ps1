$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root 'build-native'
New-Item -ItemType Directory -Force $build | Out-Null
$vc = 'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat'
$command = 'call "' + $vc + '" >nul && cl /nologo /W4 /WX /std:c11 /I"' + (Join-Path $root 'include') + '" "' + (Join-Path $root 'test/native/test_kw1281.c') + '" "' + (Join-Path $root 'src/fis_kw1281.c') + '" /Fe:"' + (Join-Path $build 'test_kw1281.exe') + '" /Fo:"' + $build + '\\" && "' + (Join-Path $build 'test_kw1281.exe') + '"'
cmd /c $command
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
