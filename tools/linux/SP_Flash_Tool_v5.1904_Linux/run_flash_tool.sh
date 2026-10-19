#!/bin/bash
STAGE_DIR="/home/deck/Documents/Workspaces/UpdaterNeo/updater-ce-neo/tools/linux/SP_Flash_Tool_v5.1904_Linux"
export LD_LIBRARY_PATH="$STAGE_DIR:$STAGE_DIR/lib:${LD_LIBRARY_PATH:-}"
export QT_PLUGIN_PATH="$STAGE_DIR/plugins"
export QT_QPA_PLATFORM="xcb"
if command -v fuser >/dev/null 2>&1; then
    fuser -k -9 /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true
fi
chmod 666 /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true
cd "$STAGE_DIR"
exec "$STAGE_DIR/flash_tool" "$@"
