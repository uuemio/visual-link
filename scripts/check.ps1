$ErrorActionPreference = 'Stop'
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pip check
exit $LASTEXITCODE
