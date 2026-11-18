# faster-whisper + OP/ED + YouTube AI自動投稿（一体型・Mac用）

## 何をするか

`~/Downloads/output/*.mp4` を検出 → faster-whisper で**一度だけ**音声を認識してSRTを作成 → 字幕焼き込み → `ed_1.mp4` / `ed_2.mp4`（あれば）と結合して `~/Downloads/out2/*_final_subbed.mp4` を完成 → 同じSRT + 元動画からの代表4フレームをAIに渡す → **タイトル・説明文・タグ・ハッシュタグ + サムネイル設計を1回の文章API呼び出しで作成** → 添付 `main(3).py` を参考にした画像編集APIでサムネイルを生成 → YouTube Data APIで動画・サムネイルを設定します。

※「字幕生成とAI投稿が一体」ですが、AI文章・画像のAPI呼び出しはWhisperとは別です。音声の**再文字起こしは行いません**。

## 1. Macに準備

ZIPをMacの**ダウンロード**フォルダ（`~/Downloads`）で解凍し、ターミナルで次の場所に移動します：

```bash
cd ~/Downloads/youtube_auto_pipeline
brew install ffmpeg
python3 -m venv .whispvenv
./.whispvenv/bin/python -m pip install --upgrade pip
./.whispvenv/bin/python -m pip install -r requirements.txt
```

`ffmpeg -version` と `ffprobe -version` が動くことを確認してください。既存の `.whispvenv` / `fw_transcribe.py` があればそれを流用できます（このパッケージにも互換版を同梱）。音声認識モデルは初回実行時にダウンロードされます。

## 2. OpenAIキー

`.env.example` を `.env` にコピーし、`OPENAI_API_KEY` に自分のキーを設定します。

```bash
cp .env.example .env
```

初回は `AUTO_UPLOAD=false`、`PRIVACY_STATUS=private` にしてください。

## 3. YouTube OAuth（アップロード時に必要）

Google Cloud Consoleでプロジェクトを選び、**YouTube Data API v3** を有効にします。OAuth同意画面を設定し、**デスクトップアプリ**用 OAuth クライアントを作成し、ダウンロードしたJSONを同じフォルダの `client_secret.json` として保存してください。OAuth同意画面がテストモードの場合、必要に応じて自分のGoogleアカウントをテストユーザーに追加します。

`AUTO_UPLOAD=true` にすると初回はブラウザが開き、動画を投稿するチャンネルを所有するGoogleアカウントで認証します。認証結果は `youtube_token_publish.json` に保存します。**APIキーでは動画投稿できません**。OAuthが必要です。

**注意**: `.env`, `client_secret.json`, `youtube_token_publish.json` は他人に渡さず、公開リポジトリ等へアップロードしないでください。API呼び出しは課金やYouTubeのクォータ対象になります。

## 4. 最初は動画1本でテスト

実行場所と動画の標準保存先は **Macのダウンロードフォルダ（`~/Downloads`）** に設定しています。ZIPを解凍した `youtube_auto_pipeline` フォルダにプログラムを入れ、編集対象の動画は1つ上の `output` に入れてください。

```text
~/Downloads/
 ├── youtube_auto_pipeline/    （プログラムを解凍したフォルダ）
 │   ├── run_auto.command      （ダブルクリックで起動）
 │   ├── merge_subtitle_op_ed.sh
 │   ├── fw_transcribe.py
 │   ├── publish_youtube.py
 │   ├── .env
 │   ├── client_secret.json    （投稿するとき）
 │   └── generated_assets/     （AI投稿文・サムネイル）
 ├── ed_1.mp4                 （任意：OP）
 ├── ed_2.mp4                 （任意：ED）
 ├── output/
 │   └── テスト動画.mp4         （ここに素材を置く）
 ├── out2/                    （完成した動画が入る）
 └── _work_merge_sub/         （SRT字幕と中間ファイル）
```

通常の起動は、`~/Downloads/youtube_auto_pipeline/run_auto.command` をダブルクリックします。Macがスクリプトの実行をブロックする場合は、以下のターミナルコマンドから起動できます。

```bash
cd ~/Downloads
bash ./youtube_auto_pipeline/run_auto.command
```

実行・入出力先は、ターミナルを開いた位置に影響されず `~/Downloads` になります。下のテスト・投稿コマンドは `cd ~/Downloads/youtube_auto_pipeline` を実行してから使ってください。

テスト（1本だけ、YouTubeへは送らない）:

```bash
PUBLISH_LIMIT=1 YOUTUBE_DRY_RUN=1 TASK=transcribe bash ./merge_subtitle_op_ed.sh
```

`~/Downloads/out2` に完成動画、`~/Downloads/youtube_auto_pipeline/generated_assets` に `youtube_metadata.json`、`upload_preview.txt`、`thumbnail_1980x1080.jpg` が生成されます。SRTは `~/Downloads/_work_merge_sub` の中に残ります。

投稿を開始するには、`.env` を `AUTO_UPLOAD=true` に変え、まず非公開で1本だけ試します。

```bash
PUBLISH_LIMIT=1 TASK=transcribe bash ./merge_subtitle_op_ed.sh
```

確認後に全件の完全自動処理:

```bash
PUBLISH_LIMIT=0 TASK=transcribe bash ./merge_subtitle_op_ed.sh
```

通常は `.env` の `PRIVACY_STATUS=private` により非公開です。`public` に変えると一般公開として投稿されます。対象動画の権利・内容・タイトル・サムネイルの妥当性を確認したうえで変更してください。

字幕や動画はすでに完成していて、**YouTube工程だけ**再試行したい場合:

```bash
./.whispvenv/bin/python publish_youtube.py --input-dir ~/Downloads/out2 --source-dir ~/Downloads/output --work-dir ~/Downloads/_work_merge_sub --limit 1 --dry-run
```

`--dry-run` はOpenAI文章・画像のAPIを実際に呼ぶため費用は発生しますが、YouTubeには投稿しません。再実行時は保存済みのメタデータ・サムネイルを再利用します。

## 5. 既存の字幕設定との互換性

元の `merge_subtitle_op_ed.sh` に処理を追加しています。`MERGE_STAGE=after_subtitle` / `before_subtitle`、`BURN=hard` / `soft`、`USE_OP`、`USE_ED`、`TRIM_T` 等は原則維持しています。

- `TASK=transcribe` が新しい標準です（日本語を忠実に記録し、日本語メタデータの生成精度を高める）。従来どおり英語翻訳字幕が必要なら `TASK=translate` としてください。
- `AUTO_YOUTUBE=0` でYouTube工程のみスキップできます。`YOUTUBE_DRY_RUN=1` ならAI生成のみです。
- `PUBLISH_LIMIT=1` は「今回作った動画のうち1本だけAI投稿工程を実行」です。`0` は無制限です。
- 元の動画への上書きはしません。完成MP4は `~/Downloads/out2` に残り、AI成果物は `~/Downloads/youtube_auto_pipeline/generated_assets` に残ります。
- 動画・字幕・OP/EDの標準場所は `~/Downloads` 以下です。必要なら `DOWNLOADS_DIR` や `IN_DIR`/`OUT_DIR`/`WORK_DIR`/`OP_FILE`/`ED_FILE` 環境変数で変更できます。

## 安全・再開・制限

- 初期設定は **非公開＋アップロードOFF**。画像生成に失敗しても `ALLOW_FRAME_FALLBACK=true` の場合は本編の実フレームをサムネイルに使います。
- `youtube_publish_state.json` にYouTube動画IDを記録し、投稿済み動画は重複アップロードしません。ネットワーク断などで動画IDが不明な場合は**自動再投稿せず** `upload_uncertain` で停止し、YouTube Studioで実際に投稿されたか確認します。
- `youtube_publish_log.csv` に成否を記録します。
- API設定・許可状態、動画内容や著作権によってはYouTube投稿・カスタムサムネイルの設定が拒否されます。APIの実動作は、ユーザーのキーとOAuth認証なしではこちらで確認できません。
