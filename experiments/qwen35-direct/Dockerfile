FROM lmsysorg/sglang:v0.5.19

USER root
WORKDIR /opt/qwen-direct
ENV CUDA_VISIBLE_DEVICES=0 \
    HF_HOME=/models/huggingface \
    USE_TF=0 \
    TORCH_DISABLE_NATIVE_JIT=1 \
    PYTHONUNBUFFERED=1

RUN python3 -m pip install --no-cache-dir accelerate==1.12.0

# Preserve SemIf's scoring implementation verbatim at this source revision.
RUN python3 - <<'PY'
from pathlib import Path
from urllib.request import urlopen
revision = "23cf1f39fc9534fe81437200959b6dfc7106e45a"
base = f"https://raw.githubusercontent.com/TheoLeeCJ/SemIf-OpenJev/{revision}/"
package = Path("semif_phase1")
package.mkdir()
for filename in ("__init__.py", "core.py", "direct.py"):
    with urlopen(base + "src/semif_phase1/" + filename, timeout=30) as response:
        (package / filename).write_bytes(response.read())
with urlopen(base + "LICENSE", timeout=30) as response:
    Path("SEMIF_LICENSE").write_bytes(response.read())
PY

COPY scorer.py /opt/qwen-direct/scorer.py
EXPOSE 8000
ENTRYPOINT ["python3", "/opt/qwen-direct/scorer.py"]
