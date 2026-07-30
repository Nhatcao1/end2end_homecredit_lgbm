# Docker and k3s HEIR benchmark plan

This plan makes a reproducible CPU HEIR/OpenFHE benchmark container and k3s
Job the next deployment milestone. The experimental FIDESlib GPU backend is a
later phase. It must not block or silently replace the CPU baseline.

## Scope and decision

The immediate benchmark is:

```text
official HEIR Python compile
  -> OpenFHE CKKS context and key setup
  -> encrypt a deterministic numeric vector
  -> encrypted SUM and MEAN
  -> final audit decrypt
  -> CSV, JSON, MLIR, and Markdown artifacts
```

Use
`code/heir/scripts/run_official_heir_py_sum_mean_trial.py` as the first
container entry point. It has no Home Credit data dependency and reports
compile, setup, encryption, encrypted evaluation, and audit decryption
separately.

This first trial proves packaging and orchestration. It is not a representative
performance claim.

## Phase D0: freeze the trial contract

The initial immutable trial parameters are:

```text
values       = 160 -100 0 60 250
width        = 8
repetitions  = 3
tolerance    = 1e-6
OMP threads  = 1
```

Required artifacts:

```text
results.csv
summary.json
REPORT.md
sum.mlir
mean.mlir
runtime-manifest.json
```

`runtime-manifest.json` is deployment metadata to add with the container work.
It must record the image reference/digest, git commit, Python version,
`heir_py` version, architecture, OS, CPU model/count, memory limit, k3s
version, node name, and benchmark arguments.

Acceptance:

1. Existing unit tests for the trial pass.
2. The local non-containerized trial reports `status: PASS`.
3. Both SUM and MEAN remain within the declared tolerance.
4. A second run writes to a new run directory and does not destroy the first.

## Phase D1: build the CPU benchmark image

Planned files:

```text
.dockerignore
deploy/heir_benchmark/Dockerfile
deploy/heir_benchmark/entrypoint.sh
deploy/heir_benchmark/README.md
```

Image rules:

- use Linux Python 3.12;
- pin `heir_py[python,openfhe]==2026.7.1` until a separately reviewed upgrade;
- install only required runtime system libraries;
- copy the application source after dependency installation to preserve the
  dependency layer cache;
- run as a non-root user;
- make `/artifacts` the only required writable application path;
- include no raw Home Credit data, keys, ciphertexts, benchmark output, virtual
  environments, or compiler caches;
- set `PYTHONUNBUFFERED=1` and an explicit `OMP_NUM_THREADS`;
- add OCI labels for source commit and image revision;
- fail the build if a small import smoke test cannot import `heir` and the
  project aggregate API.

The image entry point should accept benchmark arguments and invoke:

```bash
python3 code/heir/scripts/run_official_heir_py_sum_mean_trial.py \
  --values 160 -100 0 60 250 \
  --width 8 \
  --repetitions 3 \
  --tolerance 1e-6 \
  --output-dir /artifacts/<run-id>
```

Do not run the benchmark during `docker build`; it needs to be a container
runtime test.

Acceptance:

1. The image builds without using host HEIR/OpenFHE installations.
2. A local `docker run` produces all required artifacts.
3. `summary.json` reports `PASS`.
4. The container exits non-zero when the benchmark or artifact validation
   fails.
5. Rebuilding the same source and pinned dependencies is reproducible enough
   to preserve the dependency versions and entry-point behavior.

## Phase D2: import the image into k3s

The first k3s trial happens before DockerHub publication. A Docker image is not
automatically visible to k3s's embedded containerd.

For a single test server, use the following delivery flow:

```text
docker build
  -> docker save image.tar
  -> k3s ctr images import image.tar
  -> Kubernetes Job with imagePullPolicy: Never
```

Record the imported image ID. If the cluster has multiple worker nodes, either
import the image on the selected benchmark node or use a temporary private
registry. Do not depend on an unpushed DockerHub tag.

## Phase D3: run the k3s benchmark Job

Planned files:

```text
deploy/heir_benchmark/k8s/namespace.yaml
deploy/heir_benchmark/k8s/pvc.yaml
deploy/heir_benchmark/k8s/job.yaml
```

The first Job should:

- use a dedicated namespace;
- mount `/artifacts` from a small `ReadWriteOnce` PVC;
- use `restartPolicy: Never`;
- use `backoffLimit: 0` so a failed cryptographic run is not hidden by retries;
- set an explicit active deadline;
- run as non-root with privilege escalation disabled;
- use a read-only root filesystem if the packaged HEIR runtime permits it;
- select the node onto which the local image was imported;
- use `imagePullPolicy: Never`;
- set `OMP_NUM_THREADS=1`;
- request CPU and RAM explicitly and record both in the runtime manifest;
- retain the completed Job until logs and artifacts have been collected.

Use conservative functional-test resources first:

```text
request: 1 CPU, 2 GiB RAM
limit:   4 CPU, 8 GiB RAM
```

These values are provisional. If the pod is OOM-killed, inspect the event and
peak memory before changing the limit. Do not retry blindly.

Acceptance:

1. The pod starts from the locally imported image.
2. The Job completes once with exit code zero.
3. Logs identify the run ID and final `PASS` status.
4. All required artifacts exist on the PVC.
5. `summary.json` passes the tolerance gate.
6. A fresh Job with a fresh run ID produces a second independent result.
7. Pod events show no OOM, eviction, unexpected restart, or image pull.

## Phase D4: add a representative CPU benchmark matrix

Only after the width-8 k3s smoke passes, extend the benchmark runner with
deterministic generated inputs so large vectors do not have to be supplied as
thousands of command-line arguments.

The first matrix should cover:

```text
widths:       128, 1024, 8192
valid counts: sparse and full width
repetitions:  at least 5 after one warm-up
operations:   SUM, MEAN, sample VAR
```

For each case report:

- compile and one-time setup;
- encryption;
- encrypted evaluation;
- audit decryption;
- complete container wall time;
- maximum absolute and relative error;
- peak resident memory;
- CPU allocation and `OMP_NUM_THREADS`;
- output artifact size.

Performance runs should use a dedicated node when possible and equal CPU
requests/limits for stable Kubernetes QoS. The width-8 smoke result must never
be presented as a throughput benchmark.

## Phase G0: qualify the GPU node later

GPU work begins only after phases D0-D4 are repeatable.

The GPU preflight records:

- GPU model, VRAM, compute capability, driver, and `nvidia-smi`;
- supported CUDA version;
- NVIDIA container runtime/toolkit;
- k3s and embedded containerd versions;
- successful NVIDIA CUDA sample Job;
- successful discovery of `nvidia.com/gpu`.

This phase changes no application backend.

## Phase G1: standalone FIDESlib compatibility spike

Build a separate image with a pinned FIDESlib release, its required patched
OpenFHE version, and a matching CUDA development/runtime pair.

The spike must prove:

1. FIDESlib's own GPU tests pass in the container.
2. An OpenFHE-compatible client ciphertext can be loaded by the FIDESlib
   evaluator.
3. A GPU `subtract -> rotate/add SUM` result can be returned and decrypted by
   the trusted client.
4. Context, ciphertext, and evaluation-key restart/serialization work.
5. CPU and GPU results pass the same CKKS accuracy contract.

Keep this image and its shared libraries separate from the CPU image. Do not
mix the existing HEIR/OpenFHE runtime with FIDESlib's patched OpenFHE in one
process until ABI compatibility is demonstrated.

## Phase G2: project GPU benchmark

The first project GPU workload is:

```text
Enc(AMT_INSTALMENT), Enc(AMT_PAYMENT)
  -> FIDESlib GPU subtraction
  -> public validity mask
  -> GPU SUM and square-SUM reductions
  -> encrypted MEAN and sample VAR
  -> encrypted result return
  -> trusted client audit decrypt
```

Run the same widths and input fixtures as the CPU matrix. Measure setup,
host-to-device transfer, encrypted evaluation, device-to-host transfer, and
end-to-end time separately. Keep ciphertexts resident on the GPU for the whole
operation DAG.

The provisional GPU promotion gate is:

- no accuracy regression against the CPU oracle;
- no secret key or intermediate plaintext in the evaluator pod;
- no GPU OOM or CUDA error;
- peak VRAM below 80 percent on the selected representative case;
- at least 1.5x end-to-end speedup on a representative batched workload.

Exact MIN/MAX remains on the existing OpenFHE CKKS-to-FHEW CPU route until a
separate GPU-compatible scheme-switching proof exists.

## Phase P0: publish only validated images

DockerHub publication occurs after the k3s Job passes.

Before pushing:

1. pin the image tag to the git commit or release candidate;
2. generate an SBOM and run a vulnerability scan;
3. verify that no keys, ciphertexts, data, `.env` files, or benchmark outputs
   exist in any layer;
4. push the CPU candidate;
5. redeploy k3s by immutable image digest and repeat the smoke Job;
6. publish the FIDESlib GPU image only after its independent GPU gates pass.

## Milestone order

```text
D0 freeze test
  -> D1 Docker CPU image
  -> D2 local k3s image import
  -> D3 k3s smoke Job
  -> D4 representative CPU matrix
  -> G0 GPU node preflight
  -> G1 standalone FIDESlib spike
  -> G2 CPU/GPU project comparison
  -> P0 DockerHub promotion
```

