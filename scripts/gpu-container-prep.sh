#!/usr/bin/env bash
# Run as root inside a job container started with `--gpus all`, BEFORE any
# apt install of sysreqs (r-universe's linux-deps step).
#
# Why: nvidia-container-toolkit bind-mounts the host's driver userspace
# (libcuda, libnvidia-ml, nvidia-smi, ...) into the container. The distro's
# CUDA toolkit depends on libnvidia-compute-<N>, and r-universe's sysreqs for
# CUDA packages also name nvidia-driver-<N>. Unpacking those over the mounts
# fails ("unable to make backup link ... Invalid cross-device link", apt exit
# 100), and where the file names differ the distro's libraries land beside the
# host's and take over the .so.1 links, so nvidia-smi reports "Driver/library
# version mismatch". Both were seen on an L4 VM on 2026-11-17 (host driver
# 580.178.04, container packages 595.99.02; bioc-build#51).
#
# Fix: tell dpkg to skip every driver-userspace path. The packages install and
# satisfy dependencies; their driver files never reach the disk; the host's
# stay in charge. The CUDA toolkit itself (nvcc, libcudart, headers) is not
# under these paths and installs normally.
set -euo pipefail
cat > /etc/dpkg/dpkg.cfg.d/00-host-nvidia-driver <<'CFG'
path-exclude=/usr/lib/x86_64-linux-gnu/libnvidia-*
path-exclude=/usr/lib/x86_64-linux-gnu/libcuda.so*
path-exclude=/usr/lib/x86_64-linux-gnu/libcudadebugger.so*
path-exclude=/usr/bin/nvidia-*
path-exclude=/usr/lib/firmware/nvidia/*
path-exclude=/etc/OpenCL/vendors/nvidia.icd
CFG
echo "gpu-container-prep: dpkg will skip driver userspace paths (host driver stays in use)"
