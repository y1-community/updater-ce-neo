#!/usr/bin/env bash
# ==============================================================================
# build_linux_sp_flash.sh — Build customized SP Flash Tool for Linux
#
# Compiles the customized SP Flash Tool from tools/sp-flash-tool-src in a
# glibc 2.23 (Ubuntu 16.04) container and stages the resulting executable,
# bundled Qt4/dependency libraries, and plugins into:
#   tools/linux/SP_Flash_Tool_v5.1904_Linux
# ==============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Building Customized SP Flash Tool for Linux ==="
echo "Repo root: $REPO_ROOT"

SRC_DIR="$REPO_ROOT/tools/sp-flash-tool-src"
OUTPUT_DIR="$REPO_ROOT/tools/_Output/linux/release"
TARGET_DIR="$REPO_ROOT/tools/linux/SP_Flash_Tool_v5.1904_Linux"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: Source directory not found: $SRC_DIR"
    exit 1
fi

# Ensure docker is available
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker is required to build SP Flash Tool with maximum Linux compatibility."
    exit 1
fi

echo "-> Compiling in Ubuntu 16.04 container..."
docker run --rm -i \
    -v "$REPO_ROOT:/workspace" \
    -w /workspace/tools/sp-flash-tool-src \
    ubuntu:16.04 bash -s "$(id -u)" "$(id -g)" << 'EOF'
set -e
HOST_UID="$1"
HOST_GID="$2"

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    build-essential qt4-default libqtwebkit-dev qt4-dev-tools \
    libqt4-svg libqt4-sql-sqlite libaudio2 libmng2 libpng12-0 \
    libxml2 libxslt1.1 libicu55 libjpeg8 patchelf > /dev/null

rm -rf /workspace/tools/_Output/linux/release/*.o /workspace/tools/_Output/linux/release/moc_*.cpp || true
make HOST=linux BUILD_TYPE=release

cd /workspace/tools/_Output/linux/release
cp -L /usr/lib/x86_64-linux-gnu/libQt*.so.4* lib/
cp -L /usr/lib/x86_64-linux-gnu/libxml2.so.2* lib/
cp -L /usr/lib/x86_64-linux-gnu/libxslt.so.1* lib/
cp -L /usr/lib/x86_64-linux-gnu/libicu*.so.55* lib/
cp -L /usr/lib/x86_64-linux-gnu/libjpeg.so.8* lib/
cp -L /lib/x86_64-linux-gnu/libpng12.so.0* lib/
cp -L /usr/lib/x86_64-linux-gnu/libaudio.so.2* lib/
cp -L /usr/lib/x86_64-linux-gnu/libmng.so.2* lib/

patchelf --force-rpath --set-rpath '$ORIGIN:$ORIGIN/lib' flash_tool
for f in libflashtool*.so libsla_challenge.so; do
    if [ -f "$f" ]; then
        patchelf --force-rpath --set-rpath '$ORIGIN:$ORIGIN/lib' "$f" 2>/dev/null || true
    fi
done
for lib in lib/*.so*; do
    if [ -f "$lib" ] && [ ! -L "$lib" ]; then
        patchelf --force-rpath --set-rpath '$ORIGIN:$ORIGIN/..:$ORIGIN' "$lib" 2>/dev/null || true
    fi
done

mkdir -p plugins
cp -rL /usr/lib/x86_64-linux-gnu/qt4/plugins/* plugins/

chown -R "$HOST_UID:$HOST_GID" /workspace/tools/_Output
EOF

echo "-> Staging binary and libraries to $TARGET_DIR..."
mkdir -p "$TARGET_DIR/lib" "$TARGET_DIR/plugins"

rm -f "$TARGET_DIR/flash_tool"
cp "$OUTPUT_DIR/flash_tool" "$TARGET_DIR/flash_tool"
chmod +x "$TARGET_DIR/flash_tool"

rm -rf "$TARGET_DIR/lib"/*
cp -r "$OUTPUT_DIR/lib"/* "$TARGET_DIR/lib/"

rm -rf "$TARGET_DIR/plugins"/*
cp -r "$OUTPUT_DIR/plugins"/* "$TARGET_DIR/plugins/"

cp -f "$OUTPUT_DIR"/libflashtool*.so "$TARGET_DIR/" 2>/dev/null || true
cp -f "$OUTPUT_DIR"/libsla_challenge.so "$TARGET_DIR/" 2>/dev/null || true

if [ -f "$SRC_DIR/Lib/flash_tool.sh" ]; then
    cp "$SRC_DIR/Lib/flash_tool.sh" "$TARGET_DIR/flash_tool.sh"
    chmod +x "$TARGET_DIR/flash_tool.sh"
fi

# Clean logs and ensure clean history.ini
rm -rf "$TARGET_DIR/SP_FT_Logs"
if [ -f "$TARGET_DIR/history.ini" ]; then
    printf "[RecentOpenFile]\nscatterHistory=@Invalid()\nauthHistory=@Invalid()\n" > "$TARGET_DIR/history.ini"
fi

echo "-> Verifying staged binary execution..."
"$TARGET_DIR/flash_tool.sh" -h >/dev/null

echo "=== SP Flash Tool build & staging complete! ==="
