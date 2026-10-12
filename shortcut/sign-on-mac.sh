#!/bin/sh
# Sign the pre-built Weather Rundown shortcut so Shortcuts will import it,
# then open it. Uses Apple's own `shortcuts` command (macOS 12 or later).
#
#   sh shortcut/sign-on-mac.sh                      # the pre-built file
#   sh shortcut/sign-on-mac.sh my.unsigned.shortcut # one you generated
set -eu

here=$(cd "$(dirname "$0")" && pwd)
input=${1:-"$here/weather-rundown.unsigned.shortcut"}
# Shortcuts names an imported shortcut after its file name.
output="$(dirname "$input")/Weather Rundown.shortcut"

if ! command -v shortcuts >/dev/null 2>&1; then
    echo "The 'shortcuts' command wasn't found. Run this on a Mac with macOS 12 (Monterey) or later." >&2
    exit 1
fi

shortcuts sign --mode anyone --input "$input" --output "$output"
echo "Signed: $output"
echo "Opening it in Shortcuts. Click \"Add Shortcut\" to finish."
open "$output"
