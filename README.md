# G&C Chat

Gemini（ブラウザ上のAI）と Claude Code の会話を、話題ごとの「ルーム」に分けてチャット形式で蓄積するためのローカルWebアプリです。

## できること

- **ルーム（話題）を複数作成**し、話題ごとに会話を分離して管理
- 会話はSQLiteデータベース（`gcc_chat.db`）に永続化
- ルームごとに `logs/<slug>.md` へ**追記専用**でmdログも自動出力（人間が読む用・バックアップ用）
- Discord風サイドバー＋LINE風チャットバブルUIで閲覧
- Gemini・Claude それぞれの発言をブラウザから貼り付けて記録

## できないこと（意図的な制約）

- Gemini・claude.ai いずれのWebチャットへの**自動送信は行いません**。送信は常にユーザーが手動でコピー＆ペーストしてください。
- Claudeの発言を自動生成する機能はありません。このアプリは記録・閲覧専用です。Claude Codeセッション側で発言内容を考え、テキストエリアに貼り付けて「ログに追加」してください。

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

## データ構造

```
rooms(id, name, slug, created_at)
messages(id, room_id, speaker, body, created_at)
```

`messages.room_id` は `rooms.id` を参照し、ルーム削除時は関連メッセージも削除されます（`ON DELETE CASCADE`）。ただしルーム削除機能自体は現時点でUIに未実装です。

`logs/<slug>.md` は常に追記されるだけなので、過去のログを直接編集・削除しないでください（DBが正データ、mdは可読なコピー）。
