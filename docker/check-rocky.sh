#!/usr/bin/env bash
set -euo pipefail

cat /etc/os-release
uname -m
id
python --version
uv --version

workspace=$(mktemp -d /tmp/hw04-rocky.XXXXXX)
trap 'rm -rf "$workspace"' EXIT
cp -R /app/. "$workspace/"
cd "$workspace"

make check
make demo
