import subprocess
import time

for _ in range(60):
    out = subprocess.run(
        ["uv", "run", "--locked", "mclab", "server", "status"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    print(time.strftime("%H:%M:%S"), out.strip().replace("\n", " "), flush=True)
    if '"ready"' in out or '"running"' in out:
        break
    time.sleep(10)
