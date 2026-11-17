# GPU runner

An ephemeral GPU runner for the GPU check lane (tracker
[#50](https://github.com/seandavi/bioc-build/issues/50), this work
[#51](https://github.com/seandavi/bioc-build/issues/51)). A job that asks for
`runs-on: [self-hosted, gpu-l4]` gets its own Compute Engine VM with one NVIDIA
L4. The VM runs that one job and deletes itself.

Everything GCP-side lives in project **`bioc-u24`**. The GitHub App's private key
and webhook secret live in Secret Manager in **`cdsci-infra`**. **No GitHub
Actions workflow in this repository deploys any of this or holds GCP
credentials**; a human runs the recipes in [`justfile`](justfile).

## Architecture

```
GitHub                      Cloud Run (bioc-u24)             Compute Engine (bioc-u24)
------                      --------------------             -------------------------
workflow_job queued  --1--> gpu-dispatcher
  (label gpu-l4)             |  verify HMAC
                             |  workflow-run guard   <-2--> GET actions/runs/{id}
                             |  (background task)
                             |  generate-jitconfig   <-3--> POST .../generate-jitconfig
                             |  instances.insert     --4--> gh-runner-<job id>  (g2-standard-8, L4)
                                                              | boot: runner-boot.sh
runner registers with JIT  <-----------------5--------------- | ./run.sh --jitconfig  (one job)
                                                              | exit -> DELETE self (REST + token)
Cloud Scheduler --6--> POST /sweep (OIDC)  ------7----------> delete runner VMs older than 7 h
```

Legend: numbers are the order of events. 1: the webhook (GitHub allows 10 s, so
the service answers fast). 2: the guard described below. 3 and 4: a single-use
runner registration, then one VM. 5: the VM's runner takes exactly that job.
6 and 7: a backstop for VMs that outlive their job.

- [`image/`](image/): `provision.sh` (Docker, NVIDIA driver, nvidia-container-toolkit,
  Actions runner), `runner-boot.sh` (run one job, then delete the VM),
  `build-image.sh` (builds image family `gh-runner-gpu`). The host has no R and
  no CUDA toolkit; those come in the job container
  (`ghcr.io/r-universe-org/base-image`) with `--gpus all`.
- [`dispatcher/`](dispatcher/): the FastAPI service. Pure decisions
  (`decide.py`: signature check, should-dispatch, attempt order, instance body)
  are separate from I/O (`github.py`, `compute.py`, `service.py`, `app.py`).
- [`../.github/workflows/gpu-smoke.yml`](../.github/workflows/gpu-smoke.yml):
  the manual smoke test.

### VM creation

One VM per job, named `gh-runner-<job id>` so a redelivered webhook cannot make
a second one (an existing instance, or a 409 on insert, counts as success).
`g2-standard-8` (the G2 series includes its L4, so no accelerator is requested),
200 GB `pd-balanced` boot disk from family `gh-runner-gpu`,
`onHostMaintenance: TERMINATE`, no automatic restart, `maxRunDuration` 6 h with
termination action DELETE, labels `purpose=gh-runner` and `gh-job-id=<id>`.
Attempts, each waiting on the zone operation so capacity and quota errors are
seen: Spot in `us-central1-a`, `-b`, `-c`, then on-demand in the same three.

### Cloud Run billing

GitHub gives a webhook 10 seconds. The service answers after the cheap checks
and creates the VM in a background task, which needs CPU after the response is
sent. The service must therefore be deployed with **instance-based billing**
(`--no-cpu-throttling`); the `deploy` recipe does this. With request-based
billing the background work can be starved and VMs would not appear.

## Security model

A queued job asking for the GPU label can come from a pull request, so the label
alone never earns a VM. The dispatcher starts a VM only when **all** hold:

1. `X-Hub-Signature-256` verifies (HMAC-SHA256, constant-time compare).
2. The event is `workflow_job`, action `queued`, label `gpu-l4`, repository
   `seandavi/bioc-build`.
3. The workflow run, fetched from GitHub with the App's installation token (not
   taken from the webhook body), has `event` in `schedule`/`workflow_dispatch`,
   `head_repository.full_name == seandavi/bioc-build`, and a workflow file in the
   allowlist (`dispatch.yml`, `build-package.yml`, `gpu-smoke.yml`).

A fork pull request fails 3 on all three counts. Anything refused gets a 2xx
with a reason and starts nothing. Settings: `ALLOWED_REPO`, `GPU_LABEL`,
`ALLOWED_EVENTS`, `ALLOWED_WORKFLOW_PATHS` (comma separated).

What this does not stop: a collaborator with write access can run
`workflow_dispatch` on a branch whose copy of an allowlisted workflow differs.
Write access to this repository is the trust boundary, as it is for any
self-hosted runner. The job's code can reach the VM's metadata server, so the
runner service account is deliberately weak: it can delete instances and write
logs, nothing else.

Other properties:

- **Single-use registration.** Runners use a JIT config (`generate-jitconfig`,
  name `gpu-<job id>`, group 1): ephemeral, one job, never reused.
- **Least privilege.** The dispatcher service account may manage instances in
  `bioc-u24`, act as the runner service account only, and read exactly two
  secrets in `cdsci-infra`. See `just setup`.
- **Secrets.** `GITHUB_APP_PRIVATE_KEY` and `GITHUB_WEBHOOK_SECRET` come from
  Secret Manager via Cloud Run. `GITHUB_APP_ID` is not secret. Nothing secret is
  in this repository. The JIT config is passed through instance metadata, as the
  VM has no other channel.
- **`/sweep` is not public by accident.** The Cloud Run service must allow
  unauthenticated invocation (GitHub cannot send IAM credentials), so `/sweep`
  authenticates in the application: it verifies a Google-signed OIDC token whose
  audience is the service URL and whose verified `email` is
  `SWEEP_CALLER_EMAIL` (the `gpu-sweeper` service account, called by Cloud
  Scheduler). If `SWEEP_OIDC_AUDIENCE` or `SWEEP_CALLER_EMAIL` is unset, `/sweep`
  answers 403. Do not deploy without them. `/sweep` deletes runner-labeled VMs
  older than `SWEEP_MAX_AGE_SECONDS` (default 7 h, one hour beyond `maxRunDuration`).

## Configuration

Environment variables only.

| Variable | Default | Notes |
| --- | --- | --- |
| `GITHUB_APP_ID` | required | App's numeric id |
| `GITHUB_APP_PRIVATE_KEY` | required | secret, PEM |
| `GITHUB_WEBHOOK_SECRET` | required | secret |
| `RUNNER_SERVICE_ACCOUNT` | required | email of the VM service account |
| `ALLOWED_REPO` | `seandavi/bioc-build` | |
| `GPU_LABEL` | `gpu-l4` | |
| `ALLOWED_EVENTS` | `schedule,workflow_dispatch` | |
| `ALLOWED_WORKFLOW_PATHS` | the three files above | |
| `GCP_PROJECT` | `bioc-u24` | |
| `ZONES` | 11 zones across us-central1, us-east4, us-east1, us-west1 (see `config.py`) | order matters; the image is global, so any zone with `g2-standard-8` works |
| `MACHINE_TYPE` | `g2-standard-8` | |
| `IMAGE_FAMILY` | `gh-runner-gpu` | |
| `DISK_SIZE_GB` | `200` | |
| `MAX_RUN_DURATION_SECONDS` | `21600` | |
| `RUNNER_GROUP_ID` | `1` | |
| `SWEEP_MAX_AGE_SECONDS` | `25200` | |
| `SWEEP_OIDC_AUDIENCE`, `SWEEP_CALLER_EMAIL` | unset | `/sweep` disabled without both |

## Human steps

None of these has been done.

1. **Check GPU quota.** Regional `NVIDIA_L4_GPUS` and `PREEMPTIBLE_NVIDIA_L4_GPUS`
   were 0/32 in `us-central1` (#51), but the project-wide `GPUS_ALL_REGIONS`
   quota was not verified and is often 0 on a project that has never used a GPU.
   Check it on the Quotas page before building anything.
2. **Enable Secret Manager in `cdsci-infra`** (`just setup` does it) and
   **create the two secrets** (names match the `justfile`, override with
   `APP_KEY_SECRET` and `WEBHOOK_SECRET_NAME`).
3. **Create the GitHub App** (owner `seandavi`, installed on `seandavi/bioc-build`
   only):
   - Repository permissions: **Administration: write** (needed to register
     runners), **Actions: read** (to read workflow runs). Nothing else.
   - Subscribe to the **`workflow_job`** event.
   - Generate a private key and a random webhook secret; store them as the two
     secrets above. Record the App id.
   - Webhook URL: the Cloud Run URL plus `/webhook`, set after step 5.
4. **`just setup`**: service accounts and IAM.
5. **`just image`** (build the VM image), then
   `GITHUB_APP_ID=<id> just deploy` and `just scheduler`.
6. Set the App's webhook URL, then run the smoke test.

Administration: write is a broad repository permission. It is the only one
GitHub offers for registering self-hosted runners, which is one reason the App
is installed on this single repository.

## Smoke test

```sh
gh workflow run gpu-smoke.yml -R seandavi/bioc-build
gh run watch -R seandavi/bioc-build
```

The job runs `nvidia-smi`, installs the packages r-universe's sysreqs resolution
installs for RbowtieCuda (`cmake make nvidia-cuda-dev nvidia-cuda-toolkit
nvidia-cuda-toolkit-gcc nvidia-driver-590`, recording whether apt succeeded), runs
`nvidia-smi` again, then compiles and runs a small CUDA program on the device.
The step summary says which of install, `nvidia-smi` after, compile or run
failed. This is the main technical risk in #51: whether those packages' driver
libraries shadow the host driver that `--gpus all` injects. The run tells us;
this repository does not claim it works until it has been run.

## Development

```sh
just check        # ruff + pytest for the dispatcher
just shellcheck   # image scripts (needs shellcheck)
```

## Known gaps

- **A preempted Spot VM is not re-dispatched.** If GCE preempts the VM mid-job,
  GitHub marks the job failed or stuck; nothing re-queues it. Rerun the workflow.
  (On-demand fallback only covers capacity at creation time.)
- A webhook whose background VM creation fails (no capacity in any zone, all
  attempts) is only logged; the job waits in the queue until it times out.
  Redeliver the webhook from the App's page to retry.
- The VM boot and driver install (`image/`) have not been exercised on real
  hardware yet; the three scripts were only syntax-checked (`bash -n`) and
  run through shellcheck (via its Docker image), which is clean.

## What has been run (2026-11-17)

Run by hand in `bioc-u24`, without the dispatcher or a GitHub job:

| Step | Result |
| --- | --- |
| `image/build-image.sh` | Worked first time; image `gh-runner-gpu-20261117-193016`. |
| Boot a `g2-standard-8` from the image | Host `nvidia-smi` sees the L4, driver 580.178.04. `runner-boot.service` does nothing without `jit-config`. |
| `docker run --gpus all` with `base-image:release` | `nvidia-smi` works in the container. Image pull took 2 min 53 s. |
| apt install of RbowtieCuda's sysreqs, no prep | Fails, apt exit 100: `libnvidia-compute-595`, `nvidia-utils-595` and `nvidia-compute-utils-595` cannot unpack over the mounted host files. Happens with the CUDA toolkit alone, too, so dropping the driver from the sysreqs would not fix it. |
| Same, excluding only the exact mounted paths | apt succeeds, then `nvidia-smi` fails with "Driver/library version mismatch": the 595 libraries land beside the host's 580 ones. |
| Same, after `scripts/gpu-container-prep.sh` | apt succeeds, `nvidia-smi` works, a CUDA program compiled with the distro's nvcc 12.4 (GCC 15.2) runs correctly on the L4. |

Capacity on that day: of 5 Spot VMs, 2 were preempted within 12 minutes, and
for about 20 minutes no zone in four US regions had an L4 at all, Spot or
on-demand. Expect queued jobs to wait; the dispatcher's zone list is long for
that reason, and a job preempted mid-run is still not re-dispatched (see the
known gaps above).

Not yet run: the dispatcher against real GitHub and Compute APIs, a JIT runner
taking a job, the self-delete, `just setup` / `deploy` / `scheduler`, and
`gpu-smoke.yml`.
