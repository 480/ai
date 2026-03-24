#!/bin/sh
set -eu

REPO="${BOOTSTRAP_REPO:-480/ai}"
REF="${BOOTSTRAP_REF:-main}"

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT INT TERM HUP

archive="$tmpdir/repo.tar.gz"
checkout_dir="$tmpdir/repo"
archive_url="https://codeload.github.com/$REPO/tar.gz/$REF"

if ! http_code="$(curl -fsSL -o "$archive" -w "%{http_code}" "$archive_url")"; then
  echo "Failed to download $REPO@$REF from GitHub. Check your network connection, repository name, and ref." >&2
  exit 1
fi

if [ "$http_code" != "200" ]; then
  echo "Failed to download $REPO@$REF from GitHub (HTTP $http_code). Check the repository name and ref." >&2
  exit 1
fi

mkdir -p "$checkout_dir"
tar -xzf "$archive" -C "$checkout_dir" --strip-components=1
sh "$checkout_dir/install.sh" "$@"
