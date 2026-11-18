# LUMIX — 英語字幕のみ＋日英2言語規約チェック版

以前配布した `YouTube_LUMIX_OAuth構文修正版` の投稿先照合（CHANNEL_ID）、規約チェック、OAuthの再認証機能は維持しています。

## 変更点

- faster-whisper の `TASK=dual` で日本語原文SRT（内部確認用）と英語SRT（画面表示用）を作成。**Whisperモデルの読み込みは1回ですが、音声のデコードは日英で2回**必要です。クラウド音声認識費は発生しませんが、Macでの処理時間は長くなります。
- 元動画の日本語音声はそのままに、**英語字幕だけ**を動画に焼き込みます（`BURN=hard`）。日本語字幕は焼き込みません。
- YouTube規約・広告適合性の検査は日本語原文を主資料、英訳を参考にして照合します。英訳の意味の変化も要確認として判定します。どちらかの字幕を取得できない英語字幕版は**投稿保留**にします。
- 作成動画は `/Volumes/LUMIX/out2/動画名_final_en_subbed.mp4`。既存の日本語字幕版 `*_final_subbed.mp4` は触りません。
- `.env` の `IMAGE_QUALITY=medium` なら、以後新しく生成されるサムネイルの画像品質がMediumになります。既に生成された画像は再利用。
- 既存の `.env`、`client_secret.json`、トークン、`youtube_publish_state.json`、生成済みサムネイルは配布ZIPに含めていません。

## セットアップ

以下は配布ZIPをDownloadsに保存した例です。

```bash
# 念のため主要スクリプトをバックアップ
mkdir -p /Volumes/LUMIX/youtube_auto_pipeline/backup_before_english
cp /Volumes/LUMIX/youtube_auto_pipeline/{publish_youtube.py,policy_review.py,fw_transcribe.py,merge_subtitle_op_ed.sh} \
  /Volumes/LUMIX/youtube_auto_pipeline/backup_before_english/

unzip -o "$HOME/Downloads/YouTube_LUMIX_英語字幕_規約二言語チェック版.zip" -d /Volumes/LUMIX
```

`.env` の既存キーは維持し、`IMAGE_QUALITY=medium` に設定すると節約できます。

```
IMAGE_QUALITY=medium
POLICY_REVIEW_ENABLED=true
PUBLISH_SUBTITLE_LANG=english
ALLOW_EN_REUPLOAD=false
```

`PUBLISH_SUBTITLE_LANG=english` は、完成動画の投稿プログラムを単独実行するときに英語版だけ選ぶ設定です。
`ALLOW_EN_REUPLOAD=false` は、同じ元動画の日本語版が既に投稿済みなら英語版を重複投稿しないための設定です。

## まず1本、YouTubeへ投稿せずにテスト

```bash
cd /Volumes/LUMIX
PYBIN="$HOME/.whispvenv/bin/python" \
DOWNLOADS_DIR=/Volumes/LUMIX \
TASK=dual FW_LANG=ja MODEL=small BURN=hard \
PROCESS_LIMIT=1 AUTO_YOUTUBE=1 PUBLISH_LIMIT=1 \
YOUTUBE_DRY_RUN=1 \
bash ./youtube_auto_pipeline/merge_subtitle_op_ed.sh
```

出力例:

```text
/Volumes/LUMIX/_work_merge_sub/動画名__main_trim.srt       # 日本語原文・非表示
/Volumes/LUMIX/_work_merge_sub/動画名__main_trim.en.srt    # 英語字幕・映像に焼込み
/Volumes/LUMIX/out2/動画名_final_en_subbed.mp4
```

動画の字幕・AIタイトル・サムネイル・規約チェック結果を確認してください。規約判定結果は動画別 `generated_assets/.../policy_review.json` に保存されます。

## 全件編集から非公開投稿まで

**実際にYouTubeへ投稿します。** 投稿先の `CHANNEL_ID` とOAuth認証先を確認し、非公開で実行してください。

```bash
cd /Volumes/LUMIX
PYBIN="$HOME/.whispvenv/bin/python" \
DOWNLOADS_DIR=/Volumes/LUMIX \
TASK=dual FW_LANG=ja MODEL=small BURN=hard \
PROCESS_LIMIT=0 AUTO_YOUTUBE=1 PUBLISH_LIMIT=0 \
AUTO_UPLOAD=true PRIVACY_STATUS=private \
POLICY_REVIEW_ENABLED=true YOUTUBE_DRY_RUN=0 \
bash ./youtube_auto_pipeline/merge_subtitle_op_ed.sh
```

**既に作成した英語字幕版だけ投稿する**（再編集せず投稿）：

```bash
cd /Volumes/LUMIX
PUBLISH_SUBTITLE_LANG=english AUTO_UPLOAD=true PRIVACY_STATUS=private \
POLICY_REVIEW_ENABLED=true "$HOME/.whispvenv/bin/python" \
./youtube_auto_pipeline/publish_youtube.py \
--input-dir /Volumes/LUMIX/out2 \
--source-dir /Volumes/LUMIX/output \
--work-dir /Volumes/LUMIX/_work_merge_sub --limit 0
```

## 注意事項

1. **既存の日本語字幕が映像に焼き込まれているMP4から日本語字幕だけを削除できません。** `/Volumes/LUMIX/output` の元動画から英語字幕版を新規作成してください。
2. YouTubeに投稿済みの動画ファイル自体をAPIで英語字幕版に差し替えることはできません。英語字幕版を新規投稿する場合は重複投稿に注意してください。
3. 英訳や自動音声認識には誤りがあります。規約判定は予備的なものです。映像、著作権、サムネイル、広告適合性を含むYouTubeの最終判定は保証できません。
4. 医療・政治・歴史・実在人物の告発について、用語だけでは機械的にペナルティ扱いせず、文脈を考慮します。疑義がある動画は自動投稿保留にします。
5. 旧状態の `upload_uncertain=true` は今回も勝手に解除しません。YouTube Studioで投稿状況を確認してください。
6. 旧日本語版の投稿履歴が残っている場合は、`ALLOW_EN_REUPLOAD=false` のままだと英語字幕版は保留されます。意図的に再投稿するときだけ `ALLOW_EN_REUPLOAD=true` を指定し、Studioで重複を確認してください。
7. ffmpegとPython `faster-whisper`、`openai`、`python-dotenv` などのライブラリが必要です。

### テスト範囲

Python構文チェックとbash構文チェック、模擬Whisper＋ffmpegを使った1本の動画生成、2言語SRT・規約チェック連携のテストを実施。実際のOpenAI API課金やYouTube投稿は実行していません。
