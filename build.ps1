param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    throw 'Crea un entorno Python 3.13 de 64 bits en .venv e instala requirements.txt.'
}
$buildArgs = @('tools\build.py')
if ($SkipTests) { $buildArgs += '--skip-tests' }
& $runtime @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'No se pudo completar la compilación.' }
