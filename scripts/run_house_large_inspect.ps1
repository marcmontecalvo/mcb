Set-Location "d:\mcb"
& "C:\Users\marcm\AppData\Local\hermes\bin\uv.exe" run --locked python -u scripts/run-build.py --resume results/companion/20261109T023323003795Z/checkpoints/0045_recovered.json --inspect *>&1 | Out-File -FilePath "d:\mcb\build_inspect.log" -Encoding utf8

