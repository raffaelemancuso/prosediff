#!/usr/bin/env bash
# Open the prosediff window, from this checkout of the project.
#
#   prosediff_gui.sh                      the window, with the last choices
#   prosediff_gui.sh REPOSITORY           the repository prefilled
#   prosediff_gui.sh OLD.docx NEW.docx    two Markdown or Word files prefilled
#
# The project is the folder above this script. The window runs in the
# background, so the prompt comes back at once. Under Cygwin and Git Bash, uv
# is a native Windows program: the paths it receives are converted.
set -o errexit -o nounset -o pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(cd "$script_dir/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  printf 'prosediff_gui: uv not found: install it from https://docs.astral.sh/uv/\n' >&2
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

nohup uv run --project "$(native "$project")" prosediff-gui "${args[@]}" >/dev/null 2>&1 &
disown
