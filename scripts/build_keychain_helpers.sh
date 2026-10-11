#!/bin/bash
set -euo pipefail

novelist_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
novelist_build="${NOVELIST_BUILD_PATH:-$novelist_root/.build}"
novelist_target="$(uname -m)-apple-macosx14.0"
novelist_compiler="$(/usr/bin/xcrun swiftc -version)"
mkdir -p "$novelist_build"

build_helper() {
  local source="$novelist_root/native/$1.swift"
  local output="$novelist_build/$2"
  local identifier="local.novelist.$2"
  local stamp="$output.build-fingerprint"
  local fingerprint
  fingerprint="$(
    {
      /usr/bin/shasum -a 256 "$source"
      printf '%s\n' "$novelist_target" "$novelist_compiler" "$identifier" '-O -framework Security'
    } | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}'
  )"
  # Keychain trusts the helper's code identity; leave unchanged helpers intact.
  if [[ -x "$output" && -f "$stamp" && "$(cat "$stamp")" == "$fingerprint" ]] \
     && /usr/bin/codesign --verify --strict "$output" 2>/dev/null; then
    return
  fi
  /usr/bin/xcrun swiftc -O -framework Security -target "$novelist_target" "$source" -o "$output"
  /usr/bin/codesign --force --sign - --identifier "$identifier" "$output"
  printf '%s\n' "$fingerprint" > "$stamp"
}

build_helper session_store session-store
build_helper api_key_store api-key-store
