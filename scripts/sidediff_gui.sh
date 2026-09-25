#!/usr/bin/env bash
# Open the sidediff window, from this checkout of the project.
#
#   sidediff_gui.sh                      the window, with the last choices
#   sidediff_gui.sh REPOSITORY           the repository prefilled
#   sidediff_gui.sh OLD.docx NEW.docx    two Markdown or Word files prefilled
#
# The project is the folder above this script. The window runs in the
# background, so the prompt comes back at once. Under Cygwin and Git Bash, uv
# is a native Windows program: the paths it receives are converted.
set -o errexit -o nounset -o pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(cd "$script_dir/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  printf 'sidediff_gui: uv not found: install it from https://docs.astral.sh/uv/\n' >&2
  exit 1
fi

native() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath --windows "$1"
  else
    printf '%s\n' "$1"
  fi
}

args=()
for arg in "$@"; do
  args+=("$(native "$arg")")
done

nohup uv run --project "$(native "$project")" sidediff-gui "${args[@]}" >/dev/null 2>&1 &
disown
