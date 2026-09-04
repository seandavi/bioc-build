#!/usr/bin/env bash
# Diffs .github/workflows/build.yml against r-universe-org/workflows' build.yml
# at the commit sha pinned in our own header comment. Our `uses:` lines carry
# `@<sha> # <tag>` instead of upstream's `@<tag>`, so that's normalized back
# before diffing -- otherwise every line would show as changed and the real
# divergences (job/step edits) would be lost in the noise.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OURS=.github/workflows/build.yml
SHA=$(grep -oE 'Pinned at commit [0-9a-f]{40}' "$OURS" | awk '{print $NF}')
UPSTREAM=$(curl -fsSL "https://raw.githubusercontent.com/r-universe-org/workflows/$SHA/.github/workflows/build.yml")
OURS_NORMALIZED=$(grep -v '^#' "$OURS" | sed -E 's/@[0-9a-f]{40} # (.+)$/@\1/')
diff -u <(echo "$UPSTREAM") <(echo "$OURS_NORMALIZED") && echo "no divergence from upstream $SHA"
