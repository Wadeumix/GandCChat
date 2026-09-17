# G&C Chat

Gemini（ブラウザ上のAI）と Claude Code の会話を、話題ごとの「ルーム」に分けてチャット形式で蓄積するためのローカルWebアプリです。

## できること

- **ルーム（話題）を複数作成**し、話題ごとに会話を分離して管理
- 会話はSQLiteデータベース（`gcc_chat.db`）に永続化
- ルームごとに `logs/<slug>.md` へ**追記専用**でmdログも自動出力（人間が読む用・バックアップ用）
- Discord風サイドバー＋LINE風チャットバブルUIで閲覧
- Gemini・Claude それぞれの発言をブラウザから貼り付けて記録
- 「⬇ Geminiから取り込む」ボタンで、専用ChromeのGeminiタブから最新のAI発言を自動抽出して記録
- 重複（前回と同じ発言）・別セッション（前回と違うGemini会話）を検知して警告
- Claudeの発言に「📋 Gemini用にコピー」ボタン。ルームごとに初回だけ「GCC経由の会話です」という注釈を自動付与
- GCC導入前の会話を、共有ページの全文貼り付けで一括インポート可能

## できないこと（意図的な制約）

- Gemini・claude.ai いずれのWebチャットへの**自動送信は行いません**。送信は常にユーザーが手動でコピー＆ペーストしてください
- Geminiからの取り込みも**ボタンを押した時だけ**（常時ポーリングはしない）
- Claudeの発言を自動生成する機能はありません。このアプリは記録・閲覧専用です。Claude Codeセッション側で発言内容を考え、テキストエリアに貼り付けて「ログに追加」してください

## セットアップ

```bash
cd /Users/n00025/Desktop/ClaudeMade/GandCChat
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 起動方法

```bash
python app.py
```

起動後、ブラウザで `http://127.0.0.1:5050` を開いてください。初回起動時は「雑談」ルームが自動で1つ作られます。

## データの保存先（パス）

デフォルトではプロジェクト直下に保存されます。

| データ | デフォルトパス | 変更方法 |
|---|---|---|
| DB本体（ルーム・メッセージ） | `./gcc_chat.db` | 環境変数 `GCC_DB_PATH` |
| ルームごとのmdログ | `./logs/<slug>.md` | 環境変数 `GCC_LOGS_DIR` |

保存先を変えたい場合は起動前に環境変数を設定してください。

```bash
export GCC_DB_PATH=~/Documents/gcc/gcc_chat.db
export GCC_LOGS_DIR=~/Documents/gcc/logs
python app.py
```

## 使い方

1. サイドバー下部の「新しい話題名」に入力して「＋」を押し、話題ごとのルームを作成する
2. 左のルーム一覧から話題を選んで会話画面を開く
3. Geminiのブラウザで返ってきた発言をコピーし、「Gemini の発言を貼り付け」を選んで貼り付け →「ログに追加」
4. Claude Code側でそのログを読み、次の発言を考える
5. 考えた発言を「Claude の発言を記録」を選んで貼り付け →「ログに追加」（画面上に吹き出しとして表示される）
6. 表示されたClaudeの発言をコピーし、Geminiのブラウザ画面に**手動で**貼り付けて送信する

この繰り返しで、ルームごとに会話が時系列で蓄積されていきます。

## Geminiからの自動取り込み（任意機能）

普段使いのChromeとは別の**専用Chromeプロファイル**を使い、Geminiの最新発言をボタン一つで取り込めます。

### セットアップ（初回のみ）

```bash
./launch_gemini_chrome.sh
```

これで`~/.gcc-chrome-profile`という専用プロファイルのChromeが、デバッグポート付き（`--remote-debugging-port=9222`）で起動します。開いたウィンドウでGoogleアカウントにログインしてください（普段使いのChromeのログイン状態には一切影響しません）。

### 使い方

1. `./launch_gemini_chrome.sh` で専用Chromeを起動し、Geminiの会話を開いておく
2. GCC Chatの画面右上「⬇ Geminiから取り込む」を押す
3. 最新のAI発言が自動でルームに記録される

### 注意点

- **常時監視はしません**。ボタンを押した瞬間の最新発言だけを取得します
- 同じ発言を連続で取り込もうとすると「すでに記録済みです」と警告されます
- 前回と異なるGemini会話（別のURL）から取り込もうとすると警告が出ます。意図した動作なら「それでも取り込む」を選んでください
- `--remote-debugging-port`は同一マシン上の他プロセスからも接続できてしまうため、**専用プロファイルを分離**することで普段使いのChromeへの影響を防いでいます

## データ構造

```
rooms(id, name, slug, created_at, deleted_at, last_gemini_url, explainer_sent)
messages(id, room_id, speaker, body, created_at, source_url, imported)
```

`messages.room_id` は `rooms.id` を参照し、ルーム削除時は関連メッセージも削除されます（`ON DELETE CASCADE`）。ただしルーム削除機能自体は現時点でUIに未実装です。

`logs/<slug>.md` は常に追記されるだけなので、過去のログを直接編集・削除しないでください（DBが正データ、mdは可読なコピー）。
