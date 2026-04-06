#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_root="$repo_root/release/pfm-tans"

mkdir -p "$release_root"

rm -rf \
  "$release_root/bin" \
  "$release_root/config" \
  "$release_root/docs" \
  "$release_root/examples" \
  "$release_root/lib" \
  "$release_root/modules" \
  "$release_root/res0urces" \
  "$release_root/tests"

cp -R "$repo_root/bin" "$release_root/bin"
cp -R "$repo_root/config" "$release_root/config"
cp -R "$repo_root/docs" "$release_root/docs"
cp -R "$repo_root/examples" "$release_root/examples"
cp -R "$repo_root/lib" "$release_root/lib"
cp -R "$repo_root/modules" "$release_root/modules"
cp -R "$repo_root/res0urces" "$release_root/res0urces"
cp -R "$repo_root/tests" "$release_root/tests"

cp "$repo_root/README.md" "$release_root/README.md"
cp "$repo_root/SOFTWARE_DEPENDENCIES.txt" "$release_root/SOFTWARE_DEPENDENCIES.txt"
cp "$repo_root/CHANGELOG.md" "$release_root/CHANGELOG.md"
cp "$repo_root/.gitignore" "$release_root/.gitignore"
cp "$repo_root/tans_main_workflow.m" "$release_root/tans_main_workflow.m"
cp "$repo_root/tans_module.m" "$release_root/tans_module.m"
cp "$repo_root/tans_dose_workflow.m" "$release_root/tans_dose_workflow.m"

cat > "$release_root/RELEASE_LAYOUT.txt" <<'EOF'
This directory contains the self-contained PFM-TANS release tree intended for GitHub publication.

Refresh it from the development workspace with:
  bash bin/build_release_tree.sh
EOF

echo "Release tree updated at: $release_root"
