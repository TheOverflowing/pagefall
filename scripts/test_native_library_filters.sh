#!/bin/bash
set -euo pipefail

novelist_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
novelist_build="$novelist_root/.build"
novelist_arch="$(uname -m)"

mkdir -p "$novelist_build/swift-module-cache"
/usr/bin/xcrun swiftc -parse-as-library \
  -target "$novelist_arch-apple-macosx14.0" \
  -module-cache-path "$novelist_build/swift-module-cache" \
  "$novelist_root/native/LibraryFiltering.swift" \
  "$novelist_root/tests/native_library_filter_tests.swift" \
  -o "$novelist_build/native-library-filter-tests"
"$novelist_build/native-library-filter-tests"
