#!/usr/bin/env bash
# Builds dist/patrolcraft-<VERSION>.tar.gz, its .sha256, patrolcraft-latest and patrolcraft.sh
# for the release server. Tests, dev files and runtime data are left out.
set -euo pipefail
cd "$(dirname "$0")"
V="$(tr -d '[:space:]' <VERSION)"
rm -rf dist; mkdir -p "dist/patrolcraft-$V"
tar --exclude='./dist' --exclude='./.git' --exclude='./tests' --exclude='./requirements-dev.txt' \
    --exclude='./make-release.sh' --exclude='./install.sh' --exclude='./.env' --exclude='./config.local.yaml' \
    --exclude='./data/patrol-points' --exclude='./data/preset-thumbs' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='._*' --exclude='.DS_Store' -cf - . | tar -xf - -C "dist/patrolcraft-$V"
COPYFILE_DISABLE=1 tar -czf "dist/patrolcraft-$V.tar.gz" -C dist "patrolcraft-$V"
rm -rf "dist/patrolcraft-$V"
( cd dist && { shasum -a 256 "patrolcraft-$V.tar.gz" 2>/dev/null || sha256sum "patrolcraft-$V.tar.gz"; } >"patrolcraft-$V.tar.gz.sha256" )
printf '%s\n' "$V" >dist/patrolcraft-latest
cp install.sh dist/patrolcraft.sh
ls -l dist
