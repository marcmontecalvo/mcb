Set-Location "d:\mcb"
& "C:\Users\marcm\AppData\Local\hermes\bin\uv.exe" run --locked python -u scripts/run-build.py --build tests/fixtures/schematics/house-large.build.json --supply --coal item --seed 31415926 --start -24 64 56 --inspect *>&1 | Out-File -FilePath "d:\mcb\build_inspect.log" -Encoding utf8
