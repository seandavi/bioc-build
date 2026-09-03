#!/usr/bin/env python3
"""Compute the build.yml matrix for dispatch.yml (issue #16 / SPEC-014).

Reads packages/*.yaml from a checked-out bioc-manifest, the attempts.json
state file (may not exist yet -> treated as {}), decides which
(package, stream) pairs to dispatch per mode, and prints
`matrix=<json>` / `count=<n>` lines to $GITHUB_OUTPUT.

mode=single   -> exactly the given packages/stream, no change-check.
mode=backfill -> every active (package, stream) from the manifest.
mode=changed  -> only pairs that are never-attempted, whose remote head
                 moved since the last attempt, or that failed at the same
                 commit with attempts < 3 (retry budget).
"""
import concurrent.futures
import json
import os
import subprocess
import sys
import urllib.request

MANIFEST_DIR = sys.argv[1]
MODE = sys.argv[2]
FILTER_PACKAGES = set(p for p in sys.argv[3].split(",") if p)  # "" -> empty set = no filter
FILTER_STREAM = sys.argv[4] or None
RELEASE_BRANCH = sys.argv[5]  # e.g. RELEASE_3_23
ATTEMPTS_URL = sys.argv[6]
CAP = 200


def yaml_flat(text):
    """Minimal parser for the flat packages/<name>.yaml shape (SPEC-014)."""
    d = {}
    for line in text.splitlines():
        if ":" not in line or line.startswith("#") or line.startswith(" "):
            continue
        k, v = line.split(":", 1)
        v = v.strip().strip('"')
        if v.startswith("[") and v.endswith("]"):
            v = [s.strip() for s in v[1:-1].split(",") if s.strip()]
        d[k.strip()] = v
    return d


def branch_for(stream):
    return RELEASE_BRANCH if stream == "release" else "devel"


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.load(r)
    except Exception:
        return {}  # 404 (not published yet) or any other fetch problem -> empty state


def ls_remote_head(git_url, branch):
    try:
        out = subprocess.run(
            ["git", "ls-remote", git_url, branch],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        return out.split()[0] if out.strip() else None
    except Exception:
        return None


pkg_dir = os.path.join(MANIFEST_DIR, "packages")
candidates = []
for fname in sorted(os.listdir(pkg_dir)) if os.path.isdir(pkg_dir) else []:
    if not fname.endswith(".yaml"):
        continue
    y = yaml_flat(open(os.path.join(pkg_dir, fname)).read())
    if y.get("state") != "active":
        continue
    name = y.get("name")
    if FILTER_PACKAGES and name not in FILTER_PACKAGES:
        continue
    streams = y.get("streams") or []
    for s in streams:
        if FILTER_STREAM and s != FILTER_STREAM:
            continue
        candidates.append({"package": name, "stream": s, "git_url": y.get("git_url"), "branch": branch_for(s)})

if MODE == "single":
    matrix = [{"package": c["package"], "stream": c["stream"]} for c in candidates][:CAP]
elif MODE == "backfill":
    matrix = [{"package": c["package"], "stream": c["stream"]} for c in candidates][:CAP]
elif MODE == "changed":
    attempts = fetch_json(ATTEMPTS_URL)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        heads = dict(zip(
            ((c["package"], c["stream"]) for c in candidates),
            ex.map(lambda c: ls_remote_head(c["git_url"], c["branch"]), candidates),
        ))
    matrix = []
    for c in candidates:
        head = heads[(c["package"], c["stream"])]
        prior = attempts.get(c["package"], {}).get(c["stream"])
        if not prior:
            dispatch = True
        elif head != prior.get("commit"):
            dispatch = True
        else:
            status = str(prior.get("status", ""))
            attempts_n = prior.get("attempts", 0)
            dispatch = status.startswith("failed:") and attempts_n < 3
        if dispatch:
            matrix.append({"package": c["package"], "stream": c["stream"]})
        if len(matrix) >= CAP:
            break
else:
    print(f"unknown mode: {MODE}", file=sys.stderr)
    sys.exit(1)

out_path = os.environ.get("GITHUB_OUTPUT")
line = f"matrix={json.dumps(matrix)}\ncount={len(matrix)}\n"
if out_path:
    with open(out_path, "a") as f:
        f.write(line)
else:
    sys.stdout.write(line)
print(f"{len(matrix)} entries: {[ (c['package'], c['stream']) for c in matrix ]}", file=sys.stderr)
