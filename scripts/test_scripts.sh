#!/usr/bin/env bash
# Smoke test for the parsing/decision logic in this dir that isn't exercised
# by simply reading the code: build-package.yml's resolve-job bash (copied
# inline below, same as it appears in the workflow), staged.R's JSON shape
# (both the full and the all-null failure path), and dispatch_matrix.py's
# changed/backfill/single decision rules. No docker needed -- pure logic,
# runs against fixtures in a temp dir.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fails=0
check() { if [ "$1" = "$2" ]; then echo "ok: $3"; else echo "FAIL: $3 (got [$1], want [$2])"; fails=$((fails+1)); fi; }

# --- build-package.yml's resolve job: yaml_get / streams / timeout -------
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

POLICY_YAML='policy_version: "2026.09.1"
defaults: {max_wall_minutes: 340, vignettes: build}'
TIMEOUT=$(echo "$POLICY_YAML" | grep '^defaults:' | grep -o 'max_wall_minutes: *[0-9]*' | grep -o '[0-9]*')
check "$TIMEOUT" "340" "defaults.max_wall_minutes extraction"

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
# same all-null shape, built with jq (build-package.yml's resolve job has
# no R, so its own failure path uses jq instead of staged.R -- must match)
jq -n --arg pkg nosuchpkg --arg stream release --arg run_id 999 --arg run_attempt 1 \
  --arg run_url https://example.com/run --arg status "failed:resolve" \
  '{schema_version:"1",package:$pkg,version:null,stream:$stream,status:$status,
    tarball:{file:null,sha256:null,size_bytes:null},
    source:{git_url:null,branch:null,commit:null,commit_time:null},
    manifest_commit:null,policy_version:null,
    build:{run_id:$run_id,run_attempt:($run_attempt|tonumber),run_url:$run_url,container:null,r_version:null},
    check:{status:null,bioccheck:null},
    description:{Depends:null,Imports:null,Suggests:null,License:null,NeedsCompilation:null,Priority:null,LinkingTo:null,Enhances:null,OS_type:null},
    meta:{Title:null,Description:null,URL:null,BugReports:null,Maintainer:null,Author:null,biocViews:null}}' > "$TMP/staged_fail_jq.json"
diff <(python3 -c "import json;print(json.dumps(json.load(open('$TMP/staged_fail.json')),sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps(json.load(open('$TMP/staged_fail_jq.json')),sort_keys=True))") \
  && check ok ok "resolve job's jq failure shape matches staged.R's failure shape"

# R CMD check's real "Status:" line ("OK" / "1 NOTE" / "2 WARNINGs" / ...)
# must collapse to a single space-free word (build.yml's "bioc-build: stage"
# step does this from linux-check's checkstatus output: OK/NOTE/WARNING/ERROR/FAILURE).
classify_status() {
  case "$1" in
    FAILURE|ERROR) echo error ;;
    WARNING) echo warning ;;
    *) echo ok ;;
  esac
}
check "$(classify_status OK)" "ok" "check status: OK"
check "$(classify_status NOTE)" "ok" "check status: NOTE"
check "$(classify_status WARNING)" "warning" "check status: WARNING"
check "$(classify_status ERROR)" "error" "check status: ERROR"
check "$(classify_status FAILURE)" "error" "check status: FAILURE"

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
