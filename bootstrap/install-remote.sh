#!/bin/sh
set -eu

REPO="${BOOTSTRAP_REPO:-480/480ai}"
REF="${BOOTSTRAP_REF:-main}"

token="${GITHUB_TOKEN:-}"
if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
  token="$(gh auth token 2>/dev/null || true)"
fi

if [ -z "$token" ]; then
  echo "Set GITHUB_TOKEN or log in with gh before running this installer." >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT INT TERM HUP

archive="$tmpdir/repo.tar.gz"
checkout_dir="$tmpdir/repo"

curl -fsSL \
  -H "Authorization: Bearer $token" \
  "https://api.github.com/repos/$REPO/tarball/$REF" \
  -o "$archive"

mkdir -p "$checkout_dir"
tar -xzf "$archive" -C "$checkout_dir" --strip-components=1
sh "$checkout_dir/install.sh"
