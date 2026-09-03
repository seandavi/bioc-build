#!/usr/bin/env bash
# Smoke test for the parsing/decision logic in this dir that isn't exercised
# by simply reading the code: the flat-YAML bash parser, staged.R's JSON
# shape (both the full and the all-null failure path), and
# dispatch_matrix.py's changed/backfill/single decision rules. No docker
# needed -- pure logic, runs against fixtures in a temp dir.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fails=0
check() { if [ "$1" = "$2" ]; then echo "ok: $3"; else echo "FAIL: $3 (got [$1], want [$2])"; fails=$((fails+1)); fi; }

# --- yaml_get / check_args extraction (build.sh's parser, copied inline
# since it's a function defined inside build.sh's pipeline subshell) -------
yaml_get() { echo "$1" | grep -E "^$2:" | head -1 | sed -E "s/^$2:[[:space:]]*//; s/^\"(.*)\"$/\1/"; }

PKG_YAML='name: msdata
git_url: https://git.bioconductor.org/packages/msdata
component: data-experiment
profile: data-experiment
streams: [release, devel]
state: active
since: "2026-09-03"'
check "$(yaml_get "$PKG_YAML" state)" "active" "yaml_get state"
check "$(yaml_get "$PKG_YAML" git_url)" "https://git.bioconductor.org/packages/msdata" "yaml_get git_url"
STREAMS=$(yaml_get "$PKG_YAML" streams | tr -d '[]' | tr ',' '\n' | sed 's/^ *//;s/ *$//')
echo "$STREAMS" | grep -qx "release" && check ok ok "streams includes release"
echo "$STREAMS" | grep -qx "devel" && check ok ok "streams includes devel"

# the real policy.yaml has a trailing comment on the check_args line --
# regression test for the bug where it leaked into the parsed value.
POLICY_YAML='policy_version: "2026.09.1"
defaults: {max_wall_minutes: 340, vignettes: build}
profiles:
  data-experiment:
    check_args: "--no-manual --no-build-vignettes"   # vignettes built by R CMD build already
  workflows:
    check_args: "--no-manual"'
extract_check_args() {
  echo "$POLICY_YAML" | awk -v p="  $1:" 'BEGIN{f=0} $0==p{f=1;next} f && /^  [a-zA-Z]/{f=0} f && /check_args:/{print; exit}' \
    | sed -E 's/^[^"]*"([^"]*)".*/\1/'
}
check "$(extract_check_args data-experiment)" "--no-manual --no-build-vignettes" "check_args (trailing comment stripped)"
check "$(extract_check_args workflows)" "--no-manual" "check_args (workflows profile)"

# R CMD check's real "Status:" line ("OK" / "1 NOTE" / "2 WARNINGs" / ...)
# must collapse to a single space-free word: it gets written verbatim into a
# KEY=VALUE file that is later `source`d, and "CHECK_STATUS=1 note" runs
# `note` as a command (this actually happened on ARRmData).
classify_status() {
  if echo "$1" | grep -qi ERROR; then echo error
  elif echo "$1" | grep -qi WARNING; then echo warning
  elif [ -n "$1" ]; then echo ok
  else echo error; fi
}
check "$(classify_status 'Status: OK')" "ok" "check status: OK"
check "$(classify_status 'Status: 1 NOTE')" "ok" "check status: 1 NOTE (no space in output)"
check "$(classify_status 'Status: 2 WARNINGs')" "warning" "check status: WARNINGs"
check "$(classify_status 'Status: 1 ERROR')" "error" "check status: ERROR"
check "$(classify_status '')" "error" "check status: missing Status line"

# --- staged.R: full success path and the all-null failure path -----------
Rscript staged.R ok msdata 0.51.1 release msdata_0.51.1.tar.gz abc123 12345 \
  https://git.bioconductor.org/packages/msdata RELEASE_3_23 deadbeef 2026-08-30T12:00:00Z \
  manifestsha 2026.09.1 999 1 https://example.com/run \
  bioconductor/bioconductor_docker:RELEASE_3_23@sha256:xyz 4.6.1 \
  ok ok none "$TMP/staged_ok.json"
python3 -c "
import json
d = json.load(open('$TMP/staged_ok.json'))
assert d['status'] == 'ok'
assert d['tarball']['size_bytes'] == 12345
assert d['tarball']['file'] == 'msdata_0.51.1.tar.gz', d['tarball']['file']  # bare filename, no './'
assert d['source']['commit_time'] == '2026-08-30T12:00:00Z'
assert d['build']['run_attempt'] == 1
print('ok: staged.json success shape (bare tarball filename, commit_time)')
"
Rscript staged.R "failed:resolve" nosuchpkg null release null null null \
  null null null null null null 999 1 https://example.com/run null null \
  null null none "$TMP/staged_fail.json"
python3 -c "
import json
d = json.load(open('$TMP/staged_fail.json'))
assert d['status'] == 'failed:resolve'
assert d['version'] is None
assert d['tarball']['file'] is None
assert d['description']['License'] is None
print('ok: staged.json failure shape (all nulls, still valid)')
"

# --- deps.R: DESCRIPTION field parsing (missing fields must not error) ---
cat > "$TMP/DESCRIPTION" <<'EOF'
Package: fakepkg
Version: 1.0
Depends: R (>= 4.0.0), methods
Imports: jsonlite, stats
Suggests: knitr
EOF
Rscript -e '
desc <- read.dcf("'"$TMP"'/DESCRIPTION")[1,]
parse_field <- function(field) {
  if (is.na(field) || !nzchar(field)) return(character(0))
  parts <- strsplit(field, ",")[[1]]
  parts <- trimws(gsub("\\(.*\\)", "", parts))
  parts[nzchar(parts) & parts != "R"]
}
direct <- unique(unlist(lapply(c("Depends","Imports","LinkingTo","Suggests"), function(f) parse_field(desc[f]))))
stopifnot(setequal(direct, c("methods","jsonlite","stats","knitr")))
cat("ok: deps.R field parsing (LinkingTo absent, no error)\n")
'

# --- dispatch_matrix.py: single/backfill/changed decision rules ----------
mkdir -p "$TMP/manifest/packages"
cat > "$TMP/manifest/packages/msdata.yaml" <<'EOF'
name: msdata
git_url: https://git.bioconductor.org/packages/msdata
component: data-experiment
profile: data-experiment
streams: [release, devel]
state: active
since: "2026-09-03"
EOF
cat > "$TMP/manifest/packages/deprecatedpkg.yaml" <<'EOF'
name: deprecatedpkg
git_url: https://git.bioconductor.org/packages/deprecatedpkg
streams: [release]
state: deprecated
EOF

out=$(python3 dispatch_matrix.py "$TMP/manifest" single msdata release RELEASE_3_23 https://example.invalid/attempts.json)
echo "$out" | grep -q 'count=1' && check ok ok "dispatch single: 1 entry"

out=$(python3 dispatch_matrix.py "$TMP/manifest" backfill "" "" RELEASE_3_23 https://example.invalid/attempts.json)
echo "$out" | grep -q 'count=2' && check ok ok "dispatch backfill: 2 entries (deprecated excluded)"

REL_HEAD=$(git ls-remote https://git.bioconductor.org/packages/msdata RELEASE_3_23 | awk '{print $1}')
DEVEL_HEAD=$(git ls-remote https://git.bioconductor.org/packages/msdata devel | awk '{print $1}')
cat > "$TMP/attempts_unchanged.json" <<EOF
{"msdata": {"release": {"commit": "$REL_HEAD", "status": "ok", "attempts": 1},
             "devel": {"commit": "$DEVEL_HEAD", "status": "failed:check", "attempts": 4}}}
EOF
out=$(python3 dispatch_matrix.py "$TMP/manifest" changed "" "" RELEASE_3_23 "file://$TMP/attempts_unchanged.json")
echo "$out" | grep -q 'count=0' && check ok ok "dispatch changed: unchanged+ok and failed>=3 both skipped"

cat > "$TMP/attempts_retry.json" <<EOF
{"msdata": {"release": {"commit": "$REL_HEAD", "status": "ok", "attempts": 1},
             "devel": {"commit": "$DEVEL_HEAD", "status": "failed:check", "attempts": 2}}}
EOF
out=$(python3 dispatch_matrix.py "$TMP/manifest" changed "" "" RELEASE_3_23 "file://$TMP/attempts_retry.json")
echo "$out" | grep -q '"stream": "devel"' && check ok ok "dispatch changed: failed<3 at same commit retried"

echo
if [ "$fails" -eq 0 ]; then echo "ALL OK"; else echo "$fails FAILED"; exit 1; fi
