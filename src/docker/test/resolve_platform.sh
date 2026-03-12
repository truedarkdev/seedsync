#!/bin/bash
set -euo pipefail

case "${1:-}" in
  amd64)
    echo "linux/amd64"
    ;;
  arm64)
    echo "linux/arm64"
    ;;
  arm/v7)
    echo "linux/arm/v7"
    ;;
  *)
    echo "Unsupported architecture '${1:-}'." >&2
    echo "Supported values: amd64, arm64, arm/v7" >&2
    exit 1
    ;;
esac
