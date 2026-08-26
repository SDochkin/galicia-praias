#!/bin/bash
set -euo pipefail
REPLACE="${REPLACE:?REPLACE required}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
BAKED="${RUNNER_TEMP:?}/baked"
rm -rf "$BAKED"
cp -a data "$BAKED"
git fetch origin main
git reset --mixed origin/main
git checkout origin/main -- data
python3 scripts/update_beaches.py --overlay "$BAKED" --replace "$REPLACE"
git add -A data
if git diff --staged --quiet; then
  echo "No changes"
  exit 0
fi
git commit -m "data: update beach temperatures"
git push origin HEAD:main || {
  git fetch origin main
  git reset --mixed origin/main
  git checkout origin/main -- data
  python3 scripts/update_beaches.py --overlay "$BAKED" --replace "$REPLACE"
  git add -A data
  if git diff --staged --quiet; then
    echo "No changes"
    exit 0
  fi
  git commit -m "data: update beach temperatures"
  git push origin HEAD:main
}
