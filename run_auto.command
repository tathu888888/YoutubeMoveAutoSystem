#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Macの「ダウンロード」フォルダを実行ディレクトリにする
DOWNLOADS_DIR="${DOWNLOADS_DIR:-$HOME/Downloads}"
mkdir -p "$DOWNLOADS_DIR/output" "$DOWNLOADS_DIR/out2"
cd "$DOWNLOADS_DIR"
if [[ ! -x "${PYBIN:-$SCRIPT_DIR/.whispvenv/bin/python}" ]]; then
  echo "[ERROR] Python環境がありません。README.mdのセットアップを行ってください。"
  read -r -p 'Enterキーで終了...' _ || true
  exit 1
fi
bash "$SCRIPT_DIR/merge_subtitle_op_ed.sh"
read -r -p '完了。Enterキーで終了...' _ || true
