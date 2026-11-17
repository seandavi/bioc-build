#!/usr/bin/env bash
# Runs once at boot (runner-boot.service). If the instance carries a
# `jit-config` metadata attribute, run exactly one job with it as user
# `runner`, then delete this VM whatever happened. Without the attribute
# (the image build VM, or someone's debugging VM) do nothing and delete nothing.
#
# The delete uses the Compute REST API with the metadata-server token, so it
# works with no gcloud on the host. The VM's service account needs only
# compute.instances.delete.
set -uo pipefail

MD=http://metadata.google.internal/computeMetadata/v1
RUNNER_DIR=/home/runner/actions-runner

log() { echo "runner-boot: $*" | tee /dev/console >&2 || true; }

md() { # md <path> -> stdout; non-zero on 404/error
  curl -fsS -H 'Metadata-Flavor: Google' "${MD}/$1"
}

delete_self() {
  local name zone project token code
  if ! name=$(md instance/name) || ! zone=$(md instance/zone) \
    || ! project=$(md project/project-id); then
    log "cannot read own identity from metadata; not deleting"
    return 1
  fi
  zone=${zone##*/}
  token=$(md instance/service-accounts/default/token | jq -r .access_token) || {
    log "cannot get a token; not deleting"; return 1; }
  log "deleting instance ${name} in ${zone}"
  code=$(curl -sS -o /dev/console -w '%{http_code}' -X DELETE \
    -H "Authorization: Bearer ${token}" \
    "https://compute.googleapis.com/compute/v1/projects/${project}/zones/${zone}/instances/${name}") || true
  log "delete request returned HTTP ${code:-none}"
}

JIT=$(md instance/attributes/jit-config) || JIT=""
if [[ -z "${JIT}" ]]; then
  log "no jit-config attribute; nothing to do"
  exit 0
fi

log "starting runner"
# The config is an argument of run.sh, so it is visible in ps(1) on this VM;
# acceptable because the VM is single-use and its metadata already carries it.
runuser -u runner -- env HOME=/home/runner bash -c "cd '${RUNNER_DIR}' && exec ./run.sh --jitconfig \"\$1\"" _ "${JIT}"
status=$?
log "runner exited with status ${status}"

# maxRunDuration deletes the VM too; this is the normal path.
delete_self
exit 0
