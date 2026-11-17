#!/usr/bin/env bash
# Build the gh-runner-gpu image: temporary VM -> provision.sh -> stop -> image
# in the family -> delete the VM. Run by a human with compute.admin-ish rights
# on the project; nothing in CI calls this.
#
#   PROJECT=bioc-u24 ZONE=us-central1-a ./build-image.sh
set -euo pipefail

PROJECT="${PROJECT:-bioc-u24}"
ZONE="${ZONE:-us-central1-a}"
FAMILY="${FAMILY:-gh-runner-gpu}"
IMAGE_NAME="${IMAGE_NAME:-${FAMILY}-$(date -u +%Y%m%d-%H%M%S)}"
# The GPU is not needed to install the driver (prebuilt modules), so build on a
# cheap CPU VM and avoid spending GPU quota on the build.
MACHINE_TYPE="${MACHINE_TYPE:-n2-standard-4}"
BUILD_DISK_GB="${BUILD_DISK_GB:-200}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-1800}"

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
vm="${FAMILY}-build-$(date -u +%Y%m%d%H%M%S)"

cleanup() {
  echo "deleting build VM ${vm}"
  gcloud compute instances delete "${vm}" --project "${PROJECT}" --zone "${ZONE}" --quiet || true
}

echo "creating build VM ${vm} in ${PROJECT}/${ZONE}"
gcloud compute instances create "${vm}" \
  --project "${PROJECT}" --zone "${ZONE}" \
  --machine-type "${MACHINE_TYPE}" \
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
  --boot-disk-size "${BUILD_DISK_GB}GB" --boot-disk-type pd-balanced \
  --metadata-from-file "startup-script=${here}/provision.sh,runner-boot-sh=${here}/runner-boot.sh"
trap cleanup EXIT

echo "waiting for provisioning (up to ${TIMEOUT_SECONDS}s)"
deadline=$((SECONDS + TIMEOUT_SECONDS))
while :; do
  out=$(gcloud compute instances get-serial-port-output "${vm}" \
    --project "${PROJECT}" --zone "${ZONE}" --port 1 2>/dev/null || true)
  if grep -q 'PROVISION-DONE' <<<"${out}"; then break; fi
  if grep -q 'PROVISION-FAILED' <<<"${out}"; then
    echo "provisioning failed; last serial output:" >&2
    tail -n 60 <<<"${out}" >&2
    exit 1
  fi
  if (( SECONDS > deadline )); then
    echo "timed out waiting for provisioning" >&2
    exit 1
  fi
  sleep 15
done

echo "stopping ${vm}"
gcloud compute instances stop "${vm}" --project "${PROJECT}" --zone "${ZONE}"

echo "creating image ${IMAGE_NAME} in family ${FAMILY}"
gcloud compute images create "${IMAGE_NAME}" \
  --project "${PROJECT}" \
  --source-disk "${vm}" --source-disk-zone "${ZONE}" \
  --family "${FAMILY}"

echo "image ${IMAGE_NAME} created; build VM is deleted on exit"
