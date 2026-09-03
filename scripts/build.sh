#!/usr/bin/env bash
# Runs entirely inside the stream's bioconductor_docker container. This one
# script IS steps 1-6 (resolve, fetch, deps, build, check, size) for both
# build.yml (host adds attest+stage) and selftest.yml (host just uploads
# logs); in CI the container comes from the job's `container:` key, but
# it's the same script either way. Runnable on a laptop with docker:
#   docker run --rm -v "$PWD/work:/work" -w /work \
#     -e PACKAGE=msdata -e STREAM=release -e BRANCH=RELEASE_3_23 \
#     -e UNIVERSE=bioc-release -e MANIFEST_REF=main \
#     -e RUN_ID=local -e RUN_ATTEMPT=1 -e RUN_URL=local \
#     -e CONTAINER=bioconductor/bioconductor_docker:RELEASE_3_23 \
#     bioconductor/bioconductor_docker:RELEASE_3_23 bash /work/scripts/build.sh
#
# All inputs are env vars so build.yml, selftest.yml and a human all call it
# the same way. Writes staged.json, events.ndjson, logs/* under the current
# directory, and on success <pkg>_<ver>.tar.gz alongside them.
set -uo pipefail

: "${PACKAGE:?}" "${STREAM:?}" "${BRANCH:?}" "${UNIVERSE:?}" "${MANIFEST_REF:=main}"
: "${RUN_ID:=local}" "${RUN_ATTEMPT:=1}" "${RUN_URL:=local}"
: "${CONTAINER:=unknown}"
WORK=$(pwd)
LOGS="$WORK/logs"
EVENTS="$WORK/events.ndjson"
STATE="$WORK/.pipeline_state"   # pipeline (child) -> parent handoff, see bottom
mkdir -p "$LOGS"
: > "$EVENTS"
rm -f "$STATE" "$WORK/.disk_exceeded" "$WORK/.disk_hwm_kb"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R_VERSION=$(Rscript -e 'cat(as.character(getRversion()))')
START_TS=$(date +%s)

emit() { # emit EVENT key=val key=val ...  (values are plain strings/numbers, no embedded quotes)
  local event="$1"; shift
  local extra="" kv k v
  for kv in "$@"; do
    k="${kv%%=*}"; v="${kv#*=}"
    if [[ "$v" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
      extra="$extra,\"$k\":$v"
    else
      extra="$extra,\"$k\":\"$v\""
    fi
  done
  printf '{"ts":"%s","event":"%s","package":"%s","stream":"%s"%s}\n' \
    "$(date -u +%FT%TZ)" "$event" "$PACKAGE" "$STREAM" "$extra" >> "$EVENTS"
}

# Writes staged.json via the R helper, which fills the description/meta
# blocks from DESCRIPTION when present. Values come from the caller's shell,
# so this only works correctly in the parent (see the pipeline/state-file
# split below) or before the pipeline is forked.
write_staged() {
  local status="$1"
  Rscript "$SCRIPT_DIR/staged.R" \
    "$status" "$PACKAGE" "${VERSION:-null}" "$STREAM" \
    "${TARBALL_FILE:-null}" "${SHA256:-null}" "${SIZE_BYTES:-null}" \
    "${GIT_URL:-null}" "$BRANCH" "${COMMIT:-null}" "${COMMIT_TIME:-null}" \
    "${MANIFEST_COMMIT:-null}" "${POLICY_VERSION:-null}" \
    "$RUN_ID" "$RUN_ATTEMPT" "$RUN_URL" "$CONTAINER" "$R_VERSION" \
    "${CHECK_STATUS:-null}" "${BIOCCHECK_STATUS:-null}" \
    "${DESC_PATH:-none}" "$WORK/staged.json"
}

fail() { # fail STAGE "message" — used inside the pipeline; writes staged.json itself
         # since parent can't see child variables, then exits so the parent's `wait` sees it.
  emit build_failed stage="$1" message="$2"
  write_staged "failed:$1"
  echo "FAILED at $1: $2" >&2
  exit 1
}

# --- disk envelope monitor (issue #10) --------------------------------
# ponytail: floor on *available* space rather than real per-runner quota math;
# raise MAX_DISK_FREE_MB if this false-positives on a bigger runner.
MAX_DISK_FREE_MB="${MAX_DISK_FREE_MB:-1024}"
echo 0 > "$WORK/.disk_hwm_kb"
monitor_disk() {
  while sleep 5; do
    local used avail
    used=$(df --output=used -k "$WORK" | tail -1 | tr -d ' ')
    avail=$(df --output=avail -k "$WORK" | tail -1 | tr -d ' ')
    [ "$used" -gt "$(cat "$WORK/.disk_hwm_kb" 2>/dev/null || echo 0)" ] 2>/dev/null && echo "$used" > "$WORK/.disk_hwm_kb"
    if [ "$avail" -lt $((MAX_DISK_FREE_MB * 1024)) ]; then
      touch "$WORK/.disk_exceeded"
      return 0
    fi
  done
}

# --- steps 1-5 (resolve, fetch, deps, build, check), forked so the disk
# monitor can kill the whole process group the instant it trips, instead of
# only noticing between steps. Variables it sets don't cross back to the
# parent shell (it's a subshell), so on success it hands them off via $STATE;
# on failure it has already written staged.json itself via fail() above.
pipeline() (
  VERSION=""; TARBALL_FILE=""; SHA256=""; SIZE_BYTES="null"
  GIT_URL=""; COMMIT=""; COMMIT_TIME=""; MANIFEST_COMMIT=""; POLICY_VERSION=""
  CHECK_STATUS="null"; BIOCCHECK_STATUS="null"; DESC_PATH="none"

  # --- 1. resolve ------------------------------------------------------
  emit build_started manifest_ref="$MANIFEST_REF"
  # top-level "sha" is the first "sha" key in the response (nested commit/parent
  # shas come later), so a plain grep is enough -- no need to spin up R for this.
  MANIFEST_COMMIT=$(curl -fsSL "https://api.github.com/repos/seandavi/bioc-manifest/commits/$MANIFEST_REF" \
    | grep -o '"sha": *"[a-f0-9]*"' | head -1 | sed -E 's/.*"([a-f0-9]+)"$/\1/') || true
  [ -n "$MANIFEST_COMMIT" ] || fail resolve "could not resolve manifest_ref $MANIFEST_REF to a commit"

  PKG_YAML=$(curl -fsSL "https://raw.githubusercontent.com/seandavi/bioc-manifest/$MANIFEST_COMMIT/packages/$PACKAGE.yaml") \
    || fail resolve "packages/$PACKAGE.yaml not found at $MANIFEST_COMMIT"
  POLICY_YAML=$(curl -fsSL "https://raw.githubusercontent.com/seandavi/bioc-manifest/$MANIFEST_COMMIT/policy.yaml") \
    || fail resolve "policy.yaml not found at $MANIFEST_COMMIT"

  yaml_get() { echo "$1" | grep -E "^$2:" | head -1 | sed -E "s/^$2:[[:space:]]*//; s/^\"(.*)\"$/\1/"; }

  STATE_FLAG=$(yaml_get "$PKG_YAML" state)
  STREAMS=$(yaml_get "$PKG_YAML" streams | tr -d '[]' | tr ',' '\n' | sed 's/^ *//;s/ *$//')
  PROFILE=$(yaml_get "$PKG_YAML" profile)
  GIT_URL=$(yaml_get "$PKG_YAML" git_url)

  [ "$STATE_FLAG" = "active" ] || fail resolve "package state is '$STATE_FLAG', not active"
  echo "$STREAMS" | grep -qx "$STREAM" || fail resolve "stream '$STREAM' not in package's streams ($STREAMS)"

  POLICY_VERSION=$(yaml_get "$POLICY_YAML" policy_version)
  # profiles.<profile>.check_args: pull the indented block under "  <profile>:" up to the next 2-space key.
  CHECK_ARGS=$(echo "$POLICY_YAML" | awk -v p="  $PROFILE:" 'BEGIN{f=0} $0==p{f=1;next} f && /^  [a-zA-Z]/{f=0} f && /check_args:/{print; exit}' \
    | sed -E 's/^[^"]*"([^"]*)".*/\1/')
  [ -n "$CHECK_ARGS" ] || fail resolve "no check_args for profile '$PROFILE' in policy.yaml"

  # --- 2. fetch ----------------------------------------------------------
  git clone --depth 1 --branch "$BRANCH" "$GIT_URL" pkgsrc >"$LOGS/build.log" 2>&1 \
    || fail fetch "git clone $GIT_URL@$BRANCH failed"
  COMMIT=$(git -C pkgsrc rev-parse HEAD)
  COMMIT_TIME=$(git -C pkgsrc log -1 --format=%cI)
  emit source_fetched commit="$COMMIT" branch="$BRANCH"

  # --- 3. deps -------------------------------------------------------------
  # installs the transitive dependency closure and appends the deps_resolved
  # event itself (it already has the closure in hand; no need to recompute).
  Rscript "$SCRIPT_DIR/deps.R" pkgsrc/DESCRIPTION "$EVENTS" "$PACKAGE" "$STREAM" "$UNIVERSE" >"$LOGS/00install.out" 2>&1 \
    || fail deps "dependency install failed, see logs/00install.out"

  # --- 4. build ------------------------------------------------------------
  R CMD build pkgsrc >"$LOGS/build.log" 2>&1 || fail build "R CMD build failed, see logs/build.log"
  TARBALL_FILE=$(basename "$(ls -1 ./*.tar.gz | head -1)")   # bare filename: staged.json.tarball.file must not carry "./"
  VERSION=$(echo "$TARBALL_FILE" | sed -E "s/^${PACKAGE}_(.*)\.tar\.gz$/\1/")
  SHA256=$(sha256sum "$TARBALL_FILE" | awk '{print $1}')
  SIZE_BYTES=$(stat -c%s "$TARBALL_FILE")
  emit tarball_built sha256="$SHA256" size_bytes="$SIZE_BYTES"

  # --- 5. check --------------------------------------------------------
  # shellcheck disable=SC2086 (check_args is a deliberately unquoted word-split flag list)
  R CMD check $CHECK_ARGS "$TARBALL_FILE" >"$LOGS/check_stdout.log" 2>&1
  CHECKDIR="${PACKAGE}.Rcheck"
  [ -f "$CHECKDIR/00check.log" ] && cp "$CHECKDIR/00check.log" "$LOGS/00check.log"
  [ -f "$CHECKDIR/00install.out" ] && cat "$CHECKDIR/00install.out" >> "$LOGS/00install.out"
  # R's actual "Status:" line is "OK" / "N NOTE(s)" / "N WARNING(s)" / "N ERROR(s)"
  # (and combinations) -- collapse to SPEC-014's {ok,warning,error} by worst
  # category. Must not leave a space in CHECK_STATUS: it's later written
  # verbatim into a KEY=VALUE state file that gets `source`d (see $STATE
  # below), and "CHECK_STATUS=1 note" would run `note` as a command.
  RAW_STATUS=$(grep -E '^Status:' "$LOGS/00check.log" 2>/dev/null | tail -1)
  if echo "$RAW_STATUS" | grep -qi ERROR; then CHECK_STATUS="error"
  elif echo "$RAW_STATUS" | grep -qi WARNING; then CHECK_STATUS="warning"
  elif [ -n "$RAW_STATUS" ]; then CHECK_STATUS="ok"   # covers "OK" and NOTE-only
  else CHECK_STATUS="error"; fi   # no Status line at all means check itself crashed

  DESC_PATH="pkgsrc/DESCRIPTION"
  # BiocCheck is guaranteed installed by deps.R already.
  Rscript -e 'BiocCheck::BiocCheck(commandArgs(TRUE)[1])' "$TARBALL_FILE" >"$LOGS/bioccheck.log" 2>&1
  if grep -qi '^\* ERROR' "$LOGS/bioccheck.log"; then BIOCCHECK_STATUS="error"
  elif grep -qi '^\* WARNING' "$LOGS/bioccheck.log"; then BIOCCHECK_STATUS="warning"
  else BIOCCHECK_STATUS="ok"; fi
  emit check_completed status="$CHECK_STATUS" bioccheck="$BIOCCHECK_STATUS"

  # hand results back to the parent (subshell vars don't propagate otherwise)
  {
    echo "VERSION=$VERSION"; echo "TARBALL_FILE=$TARBALL_FILE"; echo "SHA256=$SHA256"
    echo "SIZE_BYTES=$SIZE_BYTES"; echo "GIT_URL=$GIT_URL"; echo "COMMIT=$COMMIT"
    echo "COMMIT_TIME=$COMMIT_TIME"
    echo "MANIFEST_COMMIT=$MANIFEST_COMMIT"; echo "POLICY_VERSION=$POLICY_VERSION"
    echo "CHECK_STATUS=$CHECK_STATUS"; echo "BIOCCHECK_STATUS=$BIOCCHECK_STATUS"
    echo "DESC_PATH=$DESC_PATH"
  } > "$STATE"
)

set -m               # background jobs get their own process group, so kill -- -$pid takes everything with it
monitor_disk & MONITOR_PID=$!
pipeline & PIPE_PID=$!
set +m

DISK_EXCEEDED=0
while kill -0 "$PIPE_PID" 2>/dev/null; do
  if [ -f "$WORK/.disk_exceeded" ]; then
    DISK_EXCEEDED=1
    kill -- -"$PIPE_PID" 2>/dev/null || kill "$PIPE_PID" 2>/dev/null
    break
  fi
  sleep 2
done
wait "$PIPE_PID"; PIPE_STATUS=$?
kill -- -"$MONITOR_PID" 2>/dev/null || kill "$MONITOR_PID" 2>/dev/null
wait "$MONITOR_PID" 2>/dev/null

DISK_HWM_KB=$(cat "$WORK/.disk_hwm_kb" 2>/dev/null || echo 0)
WALL_MINUTES=$(( ($(date +%s) - START_TS) / 60 ))

if [ "$DISK_EXCEEDED" -eq 1 ]; then
  write_staged "failed:envelope-disk"
  emit build_failed stage=envelope-disk message="available disk below ${MAX_DISK_FREE_MB}MB"
  echo "FAILED: disk envelope exceeded" >&2
  exit 1
fi

if [ "$PIPE_STATUS" -ne 0 ]; then
  # pipeline already wrote staged.json + a build_failed event via fail() above.
  exit "$PIPE_STATUS"
fi

# --- 6. size ------------------------------------------------------------
# shellcheck source=/dev/null
source "$STATE"
emit size_report tarball_bytes="$SIZE_BYTES" disk_hwm_gb="$(awk "BEGIN{printf \"%.2f\", $DISK_HWM_KB/1024/1024}")" wall_minutes="$WALL_MINUTES"

# check gate: error fails the build (BiocCheck is advisory, never gates)
if [ "$CHECK_STATUS" = "error" ]; then
  write_staged "error"
  echo "R CMD check status: error" >&2
  exit 1
fi
write_staged "$CHECK_STATUS"   # ok | warning
echo "done: status=$CHECK_STATUS"
