#!/bin/sh
set -eu

download_url="https://raw.githubusercontent.com/Diagonal-HQ/linear/main/bin/linear"
install_dir="${LINEAR_INSTALL_DIR:-$HOME/.local/bin}"
target="$install_dir/linear"

if ! command -v curl >/dev/null 2>&1; then
  echo "linear: curl is required to install" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "linear: Python 3.10 or newer is required" >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "linear: Python 3.10 or newer is required (found $(python3 --version 2>&1))" >&2
  exit 1
fi

if [ -d "$target" ]; then
  echo "linear: install target is a directory: $target" >&2
  exit 1
fi

mkdir -p "$install_dir"
temporary_file=$(mktemp "$install_dir/.linear.XXXXXX")
trap 'rm -f "$temporary_file"' EXIT HUP INT TERM

curl -fsSL "$download_url" -o "$temporary_file"
chmod 755 "$temporary_file"
mv -f "$temporary_file" "$target"
trap - EXIT HUP INT TERM

echo "linear installed to $target"
case ":${PATH:-}:" in
  *:"$install_dir":*) ;;
  *) echo "Add $install_dir to PATH, or run $target directly." ;;
esac
