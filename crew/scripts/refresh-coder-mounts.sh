#!/usr/bin/env bash
#
#
# The coders run in docker with NO host filesystem, so every "read your config"
# card failed and one emitted a 433-byte stub that just echoed its own prompt.
# We do NOT mount ~/.hermes/profiles directly: those dirs contain .env files
# holding API_SERVER_KEY. This copies ONLY the non-secret files.
set -euo pipefail
SRC="$HOME/.hermes/profiles"
DST="$HOME/.hermes/profiles-ro"
rm -rf "$DST"; mkdir -p "$DST"
for d in "$SRC"/*/; do
  n=$(basename "$d")
  mkdir -p "$DST/$n"
  for f in config.yaml SOUL.md profile.yaml distribution.yaml; do
    [ -f "$d$f" ] && cp "$d$f" "$DST/$n/$f"
  done
done
# Belt and braces: never ship a secret even if one is added later.
find "$DST" -name "*.env" -o -name ".env" -o -name "auth.json" | xargs -r rm -f
if grep -rlE "API_SERVER_KEY|sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]+" "$DST" 2>/dev/null | grep -q .; then
  echo "REFUSING: secret-shaped string found in snapshot" >&2
  grep -rlE "API_SERVER_KEY|sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]+" "$DST" >&2
  rm -rf "$DST"; exit 1
fi
echo "snapshot: $(find "$DST" -type f | wc -l) files across $(ls "$DST" | wc -l) profiles"
