#!/usr/bin/env bash
# Cloud session hook: install the pinned Quartus Prime Lite at session start.
# Spec: tools/toolchain/spec.md (Cloud session hook). Pins: cfg/toolchain.json.
#
# Quartus files cannot be downloaded from Altera by script, so they are kept in
# private Google Drive storage named by $QUARTUS_FILES_FOLDER (a link-shared
# folder URL), split into parts: <file>.part00, .part01, ... Nothing licensed
# is ever written inside the repository.
set -euo pipefail

[[ "${CLAUDE_CODE_REMOTE:-}" == "true" ]] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
CONFIG="$ROOT/cfg/toolchain.json"
log() { echo "[session-start] $*" >&2; }

pin() { python3 -I -c 'import json,sys; q=json.load(open(sys.argv[1]))["quartus"]; print(eval(sys.argv[2]))' "$CONFIG" "$1"; }
INSTALL_DIR="$(pin 'q["install_dir"]')"; INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"
QBIN="$INSTALL_DIR/quartus/bin"

export_env() {
  if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then
    {
      echo "export LC_ALL=C.UTF-8"
      echo "export QUARTUS_ROOTDIR=$INSTALL_DIR/quartus"
      echo "export PATH=$QBIN:\$PATH"
    } >> "$CLAUDE_ENV_FILE"
  fi
}

ready() { QUARTUS_SH="$QBIN/quartus_sh" python3 -I "$ROOT/tools/toolchain/check.py" --config "$CONFIG" >&2; }

if [[ -x "$QBIN/quartus_sh" ]] && ready; then
  export_env
  exit 0
fi

if [[ -z "${QUARTUS_FILES_FOLDER:-}" ]]; then
  log "QUARTUS_FILES_FOLDER is not set: Quartus not installed (see tools/toolchain/spec.md)"
  exit 0
fi

FOLDER_ID="${QUARTUS_FILES_FOLDER##*/folders/}"; FOLDER_ID="${FOLDER_ID%%[?/]*}"
DL="${XDG_CACHE_HOME:-$HOME/.cache}/quartus-download"
rm -rf "$DL"; mkdir -p "$DL"
trap 'rm -rf "$DL"' EXIT

log "listing private Quartus folder"
curl -fsSL --retry 4 "https://drive.google.com/embeddedfolderview?id=$FOLDER_ID" -o "$DL/listing.html"
python3 -I - "$DL/listing.html" > "$DL/parts.txt" <<'PY'
import re, sys
html = open(sys.argv[1], encoding="utf-8").read()
ids = re.findall(r'id="entry-([\w-]+)"', html)
names = re.findall(r'flip-entry-title">([^<]*)', html)
for i, n in zip(ids, names):
    if re.fullmatch(r"[\w.+-]+\.part\d+", n):
        print(i, n)
PY
[[ -s "$DL/parts.txt" ]] || { log "no .partNN files found in QUARTUS_FILES_FOLDER"; exit 1; }

log "downloading $(wc -l < "$DL/parts.txt") parts"
( cd "$DL" && xargs -P 6 -n 2 sh -c \
    'curl -fsS --retry 4 -L -o "$1" "https://drive.usercontent.google.com/download?id=$0&export=download&confirm=t"' \
    < parts.txt )

python3 -I -c 'import json,sys; [print(h, f) for f, h in json.load(open(sys.argv[1]))["quartus"]["files"].items()]' \
  "$CONFIG" > "$DL/SHA1SUMS"
while read -r _ file; do
  cat "$DL/$file".part* > "$DL/$file"
  rm -f "$DL/$file".part*
done < "$DL/SHA1SUMS"
( cd "$DL" && sha1sum -c SHA1SUMS >&2 ) || { log "checksum mismatch: not installing"; exit 1; }

SETUP="$(ls "$DL"/QuartusLiteSetup-*-linux.run)"
log "installing Quartus into $INSTALL_DIR"
chmod +x "$SETUP"
"$SETUP" --mode unattended --unattendedmodeui none --accept_eula 1 --installdir "$INSTALL_DIR" > "$DL/install.log" 2>&1 \
  || { tail -n 20 "$DL/install.log" >&2; exit 1; }
# The installer picks up the MAX 10 file next to it but not Cyclone IV.
unzip -qo "$DL"/cyclone-*.qdz -d "$INSTALL_DIR"

ready
export_env
