#!/bin/bash
STAGE_DIR="/home/deck/Documents/Workspaces/UpdaterNeo/updater-ce-neo/tools/linux/SP_Flash_Tool_v5.1904_Linux"
export LD_LIBRARY_PATH="$STAGE_DIR:$STAGE_DIR/lib:${LD_LIBRARY_PATH:-}"
export QT_PLUGIN_PATH="$STAGE_DIR/plugins"
export QT_QPA_PLATFORM="xcb"
cd "$STAGE_DIR"
exec "$STAGE_DIR/flash_tool" "$@"
