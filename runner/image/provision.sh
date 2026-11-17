#!/usr/bin/env bash
# Provision the GPU runner image. Runs once, as root, on a stock Ubuntu 24.04
# LTS x86_64 VM (build-image.sh passes it as the startup script).
#
# Installs: Docker Engine, an NVIDIA server driver, nvidia-container-toolkit
# wired into Docker, and the GitHub Actions runner under user `runner`.
# Deliberately NOT installed: R, the CUDA toolkit. Both live in the job
# container (ghcr.io/r-universe-org/base-image + sysreqs), so the host only has
# to supply the kernel module and the driver libraries that `--gpus all`
# injects into the container.
set -euo pipefail

# --- Pins -------------------------------------------------------------------
# NVIDIA driver branch. The "-server" branches come from Ubuntu's own archive
# (not NVIDIA's apt repo) for two reasons: (1) Ubuntu ships signed prebuilt
# kernel modules for its kernels (linux-modules-nvidia-<branch>-server-gcp),
# so no DKMS compile or kernel headers at image-build time, and the build VM
# needs no GPU; (2) the archive is the same trust root as the rest of the OS.
# 580 is a long-lived production branch that supports the L4 (Ada, sm_89).
# Bumping the branch is a one-line change here plus a rebuild of the image.
NVIDIA_BRANCH="${NVIDIA_BRANCH:-580}"

# GitHub Actions runner. The sha256 is the one published in the release notes
# of https://github.com/actions/runner/releases/tag/v2.338.2 (linux-x64).
RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
RUNNER_SHA256="${RUNNER_SHA256:-baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40}"
# ---------------------------------------------------------------------------

RUNNER_USER=runner
RUNNER_HOME=/home/${RUNNER_USER}
RUNNER_DIR=${RUNNER_HOME}/actions-runner
METADATA=http://metadata.google.internal/computeMetadata/v1/instance/attributes

log() { echo "provision: $*" | tee /dev/console >&2 || true; }
trap 'log "FAILED at line $LINENO"; echo "PROVISION-FAILED" >/dev/ttyS0 2>/dev/null || true' ERR

export DEBIAN_FRONTEND=noninteractive

log "base packages"
apt-get update
apt-get -y dist-upgrade
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg jq python3 tar

log "docker engine (docker.com apt repo)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# shellcheck source=/dev/null
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  >/etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

log "nvidia driver branch ${NVIDIA_BRANCH}-server (Ubuntu archive)"
# Headless userland + utils (nvidia-smi, libnvidia-ml, libcuda) for the
# container toolkit to inject; the kernel module comes prebuilt for the GCP
# kernel. The -gcp metapackage tracks linux-gcp so a rebuilt image stays in
# step with the kernel it boots.
apt-get install -y \
  "nvidia-headless-no-dkms-${NVIDIA_BRANCH}-server" \
  "nvidia-utils-${NVIDIA_BRANCH}-server" \
  "linux-modules-nvidia-${NVIDIA_BRANCH}-server-gcp"

log "nvidia-container-toolkit (NVIDIA apt repo)"
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  >/etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl enable docker

log "runner user and actions runner ${RUNNER_VERSION}"
id "${RUNNER_USER}" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "${RUNNER_USER}"
usermod -aG docker "${RUNNER_USER}"
tarball="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
tmp=$(mktemp -d)
curl -fsSL -o "${tmp}/${tarball}" \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${tarball}"
echo "${RUNNER_SHA256}  ${tmp}/${tarball}" | sha256sum -c -
install -d -o "${RUNNER_USER}" -g "${RUNNER_USER}" "${RUNNER_DIR}"
tar -xzf "${tmp}/${tarball}" -C "${RUNNER_DIR}"
chown -R "${RUNNER_USER}:${RUNNER_USER}" "${RUNNER_DIR}"
rm -rf "${tmp}"
# Runner OS dependencies (libicu etc.).
"${RUNNER_DIR}/bin/installdependencies.sh"

log "runner-boot.sh and systemd unit"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "${here}/runner-boot.sh" ]]; then
  install -m 0755 "${here}/runner-boot.sh" /usr/local/bin/runner-boot.sh
else
  # Started as a GCE startup script: the script is copied elsewhere, so
  # build-image.sh also ships runner-boot.sh as instance metadata.
  curl -fsSL -H 'Metadata-Flavor: Google' "${METADATA}/runner-boot-sh" \
    -o /usr/local/bin/runner-boot.sh
  chmod 0755 /usr/local/bin/runner-boot.sh
fi

cat >/etc/systemd/system/runner-boot.service <<UNIT
[Unit]
Description=Run one GitHub Actions job, then delete this VM
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/runner-boot.sh
StandardOutput=journal+console
StandardError=journal+console
TimeoutStartSec=infinity

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable runner-boot.service

log "cleanup"
apt-get -y autoremove
apt-get clean
rm -rf /var/lib/apt/lists/*
# Leave no machine identity or logs in the image.
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
journalctl --rotate --vacuum-time=1s || true

log "done"
echo "PROVISION-DONE" >/dev/ttyS0 2>/dev/null || echo "PROVISION-DONE"
