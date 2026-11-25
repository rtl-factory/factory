#!/usr/bin/env bash
# Cloud session hook: install the pinned toolchain at session start so that
# `bazel test --config=l0 //...` runs with no manual setup: the Ubuntu packages,
# Bazelisk (with the Bazel download mirror when the default hosts are blocked),
# the OSS CAD Suite (formal tools and Verilator) and Quartus Prime Lite.
# Spec: wiki/tools/toolchain/spec.md (Cloud session hook). Pins: cfg/toolchain.json.
#
# Every install is checked with tools/toolchain/check.py first and skipped when
# it passes; a failed install is logged and does not stop the others. Nothing is
# written inside the repository.
#
# Quartus files cannot be downloaded from Altera by script, so they are kept in
# private Google Drive storage named by $QUARTUS_FILES_FOLDER (a link-shared
# folder URL), split into parts: <file>.part00, .part01, ... Nothing licensed
# is ever written inside the repository.
set -euo pipefail

[[ "${CLAUDE_CODE_REMOTE:-}" == "true" ]] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
CONFIG="$ROOT/cfg/toolchain.json"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"
log() { echo "[session-start] $*" >&2; }
# One value from the versions file: pin <section> <key>; a list prints one item per line.
pin() {
  python3 -I -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]
for k in sys.argv[3].split("."): v=v[k]
print("\n".join(v) if isinstance(v, list) else v)' "$CONFIG" "$1" "$2"
}
home() { local p; p="$(pin "$@")"; echo "${p/#\~/$HOME}"; }
ready() { python3 -I "$ROOT/tools/toolchain/check.py" --config "$CONFIG" "$@" >&2; }

BAZELISK_DIR="$(home bazelisk install_dir)"

# Ubuntu packages for Verilator's model builds, FST waveforms and the nand2doom C
# programs. They need root: installed only as root or through passwordless
# sudo; otherwise the missing ones are logged.
install_packages() {
  local p missing=() sudo=()
  for p in $(pin packages apt); do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing+=("$p")
  done
  [[ ${#missing[@]} -eq 0 ]] && return 0
  if [[ "$(id -u)" -ne 0 ]]; then
    if command -v sudo >/dev/null && sudo -n true 2>/dev/null; then sudo=(sudo -n); else
      log "missing packages (no root or passwordless sudo to install them): ${missing[*]}"; return 1
    fi
  fi
  command -v apt-get >/dev/null || { log "missing packages (no apt-get): ${missing[*]}"; return 1; }
  log "installing packages: ${missing[*]}"
  "${sudo[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update -qq >&2 \
    && "${sudo[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" >&2 \
    || { log "package install failed: ${missing[*]}"; return 1; }
}

# Bazelisk: a checksum-pinned release binary, installed as <install_dir>/bazelisk
# with a bazel link beside it. It downloads the Bazel that .bazelversion pins.
install_bazelisk() {
  local sum tmp
  sum="$(pin bazelisk sha256)"
  if ! echo "$sum  $BAZELISK_DIR/bazelisk" | sha256sum -c --quiet - >/dev/null 2>&1; then
    log "downloading Bazelisk $(pin bazelisk version)"
    tmp="$(mktemp -d)"
    curl -fsSL --retry 4 -o "$tmp/bazelisk" "$(pin bazelisk url)" || { rm -rf "$tmp"; log "Bazelisk download failed"; return 1; }
    echo "$sum  $tmp/bazelisk" | sha256sum -c --quiet - >&2 || { rm -rf "$tmp"; log "Bazelisk checksum mismatch: not installing"; return 1; }
    mkdir -p "$BAZELISK_DIR"
    install -m 755 "$tmp/bazelisk" "$BAZELISK_DIR/bazelisk"; rm -rf "$tmp"
  fi
  ln -sfn bazelisk "$BAZELISK_DIR/bazel"
  PATH="$BAZELISK_DIR:$PATH" ready --tool bazel
}

# Bazel mirror: when the session proxy blocks releases.bazel.build or
# bcr.bazel.build, point Bazelisk at GitHub releases (~/.bazeliskrc; Bazelisk
# does not read .bazelrc) and Bazel at the registry's GitHub copy (~/.bazelrc).
# Machine-only files, never the repository's .bazelrc; a setting already there
# is kept.
# A small file that exists on each host; -f counts a proxy's 403 as blocked.
reachable() { curl -fsS -o /dev/null --max-time 15 "$1" 2>/dev/null; }
set_mirror() {
  local version; version="$(cat "$ROOT/.bazelversion")"
  if ! reachable "https://releases.bazel.build/$version/release/bazel-$version-linux-x86_64.sha256" && ! grep -q '^BAZELISK_BASE_URL=' "$HOME/.bazeliskrc" 2>/dev/null; then
    log "releases.bazel.build unreachable: Bazel downloads from GitHub releases (~/.bazeliskrc)"
    echo "BAZELISK_BASE_URL=$(pin bazelisk mirror.bazelisk_base_url)" >> "$HOME/.bazeliskrc"
  fi
  if ! reachable https://bcr.bazel.build/bazel_registry.json && ! grep -q -- '--registry=' "$HOME/.bazelrc" 2>/dev/null; then
    log "bcr.bazel.build unreachable: Bazel reads the registry from GitHub (~/.bazelrc)"
    echo "common --registry=$(pin bazelisk mirror.registry)" >> "$HOME/.bazelrc"
  fi
}

# Shared Bazel cache: on only when the cloud environment sets both secrets, the
# bucket URL ($BAZEL_REMOTE_CACHE) and a service account key's JSON
# ($BAZEL_REMOTE_CACHE_KEY). The key is written outside the repository, readable
# by this user only.
set_remote_cache() {
  local rc="$HOME/.bazelrc" key="$HOME/.config/rtl-factory/bazel-cache-key.json"
  local begin="# >>> rtl-factory remote cache >>>" end="# <<< rtl-factory remote cache <<<"
  # Why: the hook owns this block and rewrites it each session, so a changed bucket
  # URL takes effect and removed secrets turn the cache off.
  if [[ -f "$rc" ]]; then
    sed -i "/^$begin\$/,/^$end\$/d" "$rc"
  fi
  [[ -n "${BAZEL_REMOTE_CACHE:-}" && -n "${BAZEL_REMOTE_CACHE_KEY:-}" ]] || return 0
  mkdir -p "$(dirname "$key")"
  ( umask 077 && printf '%s' "$BAZEL_REMOTE_CACHE_KEY" > "$key" )
  log "shared Bazel cache on: $BAZEL_REMOTE_CACHE (~/.bazelrc)"
  printf '%s\nbuild --config=remote-cache\nbuild --remote_cache=%s\nbuild --google_credentials=%s\n%s\n' \
    "$begin" "$BAZEL_REMOTE_CACHE" "$key" "$end" >> "$rc"
}

# Download the pinned Bazel now, so the first bazel command does not.
fetch_bazel() { ( cd "$ROOT" && PATH="$BAZELISK_DIR:$PATH" bazel --version >&2 ); }

# OSS CAD Suite: a public, checksum-pinned release, unpacked outside the
# repository. Its bin/ is not put on PATH (it carries its own Python, which would
# shadow the host's); tools/fv/fv.py calls sby from the install directory and
# Bazel's @verilator links the suite's Verilator.
install_oss_cad_suite() {
  ready --tool oss-cad-suite && return 0
  local dir url sum cache="$CACHE/oss-cad-suite-download"
  dir="$(home oss_cad_suite install_dir)"
  url="$(pin oss_cad_suite url)"; sum="$(pin oss_cad_suite sha256)"
  rm -rf "$cache"; mkdir -p "$cache"
  log "downloading OSS CAD Suite $(pin oss_cad_suite version)"
  curl -fsSL --retry 4 -o "$cache/suite.tgz" "$url" || { rm -rf "$cache"; log "OSS CAD Suite download failed"; return 1; }
  echo "$sum  $cache/suite.tgz" | sha256sum -c --quiet - >&2 || { rm -rf "$cache"; log "OSS CAD Suite checksum mismatch: not installing"; return 1; }
  rm -rf "$dir"; mkdir -p "$dir"
  tar -xzf "$cache/suite.tgz" -C "$dir" --strip-components=1 && rm -rf "$cache"
  ready --tool oss-cad-suite
}

# Quartus Prime Lite from private storage. A subshell, so that its download
# folder is removed on any exit.
install_quartus() (
  INSTALL_DIR="$(home quartus install_dir)"
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
  qready() { QUARTUS_SH="$QBIN/quartus_sh" ready --tool quartus; }

  if [[ -x "$QBIN/quartus_sh" ]] && qready; then
    export_env
    exit 0
  fi

  if [[ -z "${QUARTUS_FILES_FOLDER:-}" ]]; then
    log "QUARTUS_FILES_FOLDER is not set: Quartus not installed (see wiki/tools/toolchain/spec.md)"
    exit 0
  fi

  FOLDER_ID="${QUARTUS_FILES_FOLDER##*/folders/}"; FOLDER_ID="${FOLDER_ID%%[?/]*}"
  DL="$CACHE/quartus-download"
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

  qready
  export_env
)

# Packages first, then the quick Bazel steps; the two downloads run side by side. Each runs as its
# own job, so errexit holds inside it and one failure does not stop the others.
install_packages || log "packages missing: some L0 targets may fail"
if install_bazelisk; then
  set_mirror
  set_remote_cache
  fetch_bazel || log "Bazel $(cat "$ROOT/.bazelversion") not downloaded yet: the first bazel command will retry"
else
  log "Bazelisk not installed: bazel commands will fail until it is"
fi

# Why: written before the long steps, so a hook that times out still leaves the
# session with bazel on PATH.
if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then
  echo "export PATH=$BAZELISK_DIR:\$PATH" >> "$CLAUDE_ENV_FILE"
fi

install_oss_cad_suite & suite_job=$!
install_quartus & quartus_job=$!
wait "$suite_job" || log "OSS CAD Suite not installed: simulation, lint and formal runs will fail until it is"
wait "$quartus_job" || log "Quartus not installed: FPGA images will fail until it is"

PATH="$BAZELISK_DIR:$PATH" ready || true
