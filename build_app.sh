#!/bin/bash
# GCC Chat をネイティブの .app にビルドする。
set -e
cd "$(dirname "$0")"
source venv/bin/activate
pyinstaller --noconfirm "GCC Chat.spec"
echo ""
echo "ビルド完了: dist/GCC Chat.app"
echo "Finderからダブルクリックするか、Applicationsフォルダにドラッグしてください。"
