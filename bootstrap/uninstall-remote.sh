#!/bin/sh
set -eu

REPO="${BOOTSTRAP_REPO:-480/ai}"
REF="${BOOTSTRAP_REF:-main}"

token="${GITHUB_TOKEN:-}"
if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
  token="$(gh auth token 2>/dev/null || true)"
fi

if [ -z "$token" ]; then
  echo "Set GITHUB_TOKEN or log in with gh before running this uninstaller." >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT INT TERM HUP

archive="$tmpdir/repo.tar.gz"
checkout_dir="$tmpdir/repo"
api_url="https://api.github.com/repos/$REPO/tarball/$REF"

if ! http_code="$(curl -sSL -H "Authorization: Bearer $token" -o "$archive" -w "%{http_code}" "$api_url")"; then
  echo "Failed to download $REPO@$REF from GitHub. Check your network connection, authentication, and repo access." >&2
  exit 1
fi

if [ "$http_code" != "200" ]; then
  echo "Failed to download $REPO@$REF from GitHub (HTTP $http_code). Check your authentication and repo access." >&2
  exit 1
fi

mkdir -p "$checkout_dir"
tar -xzf "$archive" -C "$checkout_dir" --strip-components=1
sh "$checkout_dir/uninstall.sh"
