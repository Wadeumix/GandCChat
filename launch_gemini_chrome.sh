#!/bin/bash
# GCC Chat専用のChromeプロファイルを、デバッグポート付きで起動する。
#
# 普段使いのChromeプロファイルとは完全に分離されており、
# このプロファイルにはGeminiだけログインしておく想定。
# 初回はGoogleアカウントへのログインが必要。

PROFILE_DIR="$HOME/.gcc-chrome-profile"
DEBUG_PORT=9222

mkdir -p "$PROFILE_DIR"

echo "GCC Chat専用Chromeを起動します（デバッグポート: $DEBUG_PORT）"
echo "プロファイル保存先: $PROFILE_DIR"

open -na "Google Chrome" --args \
  --remote-debugging-port=$DEBUG_PORT \
  --user-data-dir="$PROFILE_DIR" \
  "https://gemini.google.com/app"
