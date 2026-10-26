# Qwen3.5-4B SemIf direct-scoring experiment

This wrapper imports the unchanged SemIf `core.py` and `direct.py` at source
revision `23cf1f39fc9534fe81437200959b6dfc7106e45a`. Model revision is
`Qwen/Qwen3.5-4B@851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`.

It runs one BF16 forward pass, reads every declared answer-slot logit, and emits
zero answer tokens. It is distinct from the quantized Ollama approximation.
The base runtime is the existing SGLang image, not SemIf's exact pinned dependency
environment; the health and per-decision metadata expose actual package versions.
The implementation does not silently truncate inputs or fall back to CPU.

Use these files as a build context on gpu-docker. From this directory:

```bash
docker stop mcb-laya
curl -fsS http://10.10.40.20:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.5:4b","keep_alive":0}'
docker build -t mcb-qwen35-direct:local .
docker run -d --name mcb-qwen35-direct --gpus '"device=0"' \
  -p 10.10.40.20:8003:8000 \
  -v mcb-qwen35-direct-cache:/models \
  mcb-qwen35-direct:local
docker logs --tail 100 -f mcb-qwen35-direct
```

The first launch downloads the BF16 checkpoint (about 9 GB), separately from
Ollama's Q4 weights. A successful startup warmup runs before HTTP starts.

```bash
curl -fsS http://10.10.40.20:8003/health
```

The local Minecraft harness can use `http://10.10.40.20:8003/v1/systemone` through
its existing SystemOne adapter, with the original one-second decision deadline.
GPU model loading and zero-generation scoring were verified on gpu-docker. Both
the original criterion and a separate serialization-neutral criterion scored
28/144 on the frozen synthetic action matrix, with no deadline or response errors.
The latter had median whole-decision latency 179.5 ms. It was not promoted to
native gameplay under this representation. See
[the recorded proof](../../docs/G4_QWEN35_DIRECT_PROOF.json) and
[screening details](../../docs/G4_SCREENING.md).

The exact tested wrapper and Dockerfile are preserved here; the ZIP under ignored
`results/` was only a deployment convenience. Downloaded upstream modules retain
their MIT license in the image. No model weights, inference credentials or caches
are committed to this repository.
