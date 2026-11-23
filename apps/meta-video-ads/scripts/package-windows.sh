#!/usr/bin/env bash
# Builds dist/MetaVideoAds-Portable-win-x64.zip: unzip on Windows 10/11 x64 and double-click "Mo Meta Video Ads.bat".
set -euo pipefail
command -v sha256sum >/dev/null || sha256sum() { shasum -a 256 "$@"; }
cd "$(dirname "$0")/.."
NODE_VERSION="${NODE_VERSION:-24.22.2}"
FFMPEG_ZIP="${FFMPEG_ZIP:-ffmpeg-n8.1-latest-win64-lgpl-shared-8.1}"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
CACHE="${CACHE_DIR:-$PWD/.portable-cache}"
NAME=MetaVideoAds-Portable
OUT="$PWD/dist/$NAME"
mkdir -p "$CACHE" && rm -rf "$OUT" "$OUT-win-x64.zip" && mkdir -p "$OUT/app" "$OUT/runtime/ffmpeg"

node_zip="node-v$NODE_VERSION-win-x64.zip"
[ -f "$CACHE/$node_zip" ] || curl -fsSL -o "$CACHE/$node_zip" "https://nodejs.org/dist/v$NODE_VERSION/$node_zip"
curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt" | grep " $node_zip\$" | (cd "$CACHE" && sha256sum -c -)
curl -fsSL -o "$CACHE/ffmpeg.sha256" "$FFMPEG_URL/checksums.sha256"
if ! (cd "$CACHE" && grep " $FFMPEG_ZIP.zip\$" ffmpeg.sha256 | sha256sum -c - >/dev/null 2>&1); then
  curl -fsSL -o "$CACHE/$FFMPEG_ZIP.zip" "$FFMPEG_URL/$FFMPEG_ZIP.zip"
  (cd "$CACHE" && grep " $FFMPEG_ZIP.zip\$" ffmpeg.sha256 | sha256sum -c -)
fi

tar --exclude=./node_modules --exclude=./.next --exclude=./data --exclude=./dist --exclude=./.portable-cache --exclude='./.env*' -cf - . | tar -xf - -C "$OUT/app"
cd "$OUT/app"
npm ci --no-audit --no-fund && npm run build
rm -rf .next/cache node_modules app tests postcss.config.mjs tsconfig.json next-env.d.ts portable README.md .gitignore .env.example scripts/package-windows.sh scripts/smoke.ts
npm ci --omit=dev --os=win32 --cpu=x64 --ignore-scripts --no-audit --no-fund
rm -rf node_modules/@next/swc-*
cd - >/dev/null
unzip -q -j "$CACHE/$node_zip" "node-v$NODE_VERSION-win-x64/node.exe" "node-v$NODE_VERSION-win-x64/LICENSE" -d "$OUT/runtime"
unzip -q -j "$CACHE/$FFMPEG_ZIP.zip" "$FFMPEG_ZIP/bin/*" "$FFMPEG_ZIP/LICENSE.txt" -x "$FFMPEG_ZIP/bin/ffplay.exe" -d "$OUT/runtime/ffmpeg"
cp portable/windows/*.bat portable/windows/*.txt "$OUT/"
(cd dist && zip -qr -9 "$NAME-win-x64.zip" "$NAME")
echo "dist/$NAME-win-x64.zip"
