#!/usr/bin/env bash
set -euo pipefail
# =========================================================
# faster-whisper + OP/ED 結合 一体型スクリプト
#
# モード:
#   MERGE_STAGE=after_subtitle
#     -> 先に本編だけ字幕化して、その後 OP/ED を結合
#     -> hard字幕推奨
#
#   MERGE_STAGE=before_subtitle
#     -> 先に OP/本編/ED を結合して、その後 全体を字幕化
#     -> soft/hard 両対応
#
# 例:
#   BURN=hard MERGE_STAGE=after_subtitle ./merge_subtitle_op_ed.sh
#   BURN=soft MERGE_STAGE=before_subtitle ./merge_subtitle_op_ed.sh
# =========================================================
# ---------- Python 仮想環境 ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYBIN="${PYBIN:-$HOME/.whispvenv/bin/python}"
# ---------- 実行・入出力場所（LUMIX 外付けドライブ） ----------
# スクリプトの配置場所や起動時の作業ディレクトリに関係なく、
# 動画の読み込み・完成動画の保存は /Volumes/LUMIX を基準にします。
DOWNLOADS_DIR="${DOWNLOADS_DIR:-/Volumes/LUMIX}"
IN_DIR="${IN_DIR:-$DOWNLOADS_DIR/output}"              # 本編動画フォルダ
OUT_DIR="${OUT_DIR:-$DOWNLOADS_DIR/out2}"               # 最終出力
WORK_DIR="${WORK_DIR:-$DOWNLOADS_DIR/_work_merge_sub}"  # 字幕・中間ファイル
EXT="${EXT:-mp4}"
# ---------- OP / ED ----------
OP_FILE="${OP_FILE:-$DOWNLOADS_DIR/ed_1.mp4}"         # 無ければ自動スキップ
ED_FILE="${ED_FILE:-$DOWNLOADS_DIR/ed_2.mp4}"         # 無ければ自動スキップ
USE_OP="${USE_OP:-1}"                        # 1=使う / 0=使わない
USE_ED="${USE_ED:-1}"                        # 1=使う / 0=使わない
# ---------- 本編末尾カット ----------
TRIM_T="${TRIM_T:-0}"                      # 秒。0ならカットなし
# ---------- 動画統一設定 ----------
WIDTH="${WIDTH:-1920}"
HEIGHT="${HEIGHT:-1080}"
FPS="${FPS:-30}"
CRF="${CRF:-18}"
PRESET="${PRESET:-veryfast}"
ABIT="${ABIT:-192k}"
VENC="${VENC:-libx264}"                      # 例: libx264 / h264_videotoolbox
# ---------- Whisper / 字幕 ----------
MODEL="${MODEL:-small}"                      # small / medium / large-v3
TASK="${TASK:-ai_translate}"                 # ai_translate=Whisper日本語1回→AI英訳 / dual=Whisper2回
BURN="${BURN:-hard}"                         # hard / soft
FONT="${FONT:-Helvetica}"
FW_LANG="${FW_LANG:-ja}"
# ---------- 処理順 ----------
MERGE_STAGE="${MERGE_STAGE:-after_subtitle}" # after_subtitle / before_subtitle
# 完成動画をAIで解析・サムネイル生成・YouTube自動投稿
# 1=有効（.env の AUTO_UPLOAD=false ならAI生成のみ）
AUTO_YOUTUBE="${AUTO_YOUTUBE:-1}"
# 0=全ファイルを処理。初回テストは PUBLISH_LIMIT=1 を推奨
PUBLISH_LIMIT="${PUBLISH_LIMIT:-0}"
# 0=全件、1=最初の1本だけ編集から処理（テスト時用）
PROCESS_LIMIT="${PROCESS_LIMIT:-0}"
# 1=YouTubeへは送らず、AI生成のみ試す
YOUTUBE_DRY_RUN="${YOUTUBE_DRY_RUN:-0}"
# ---------- faster-whisper 環境 ----------
export MODEL TASK
if [[ "$TASK" == "translate" || "$TASK" == "dual" || "$TASK" == "ai_translate" ]]; then
  SUB_LANG="eng"
else
  SUB_LANG="jpn"
fi
export FWH_COMPUTE="${FWH_COMPUTE:-int8}"
export FWH_THREADS="${FWH_THREADS:-0}"
export FWH_WORKERS="${FWH_WORKERS:-2}"
mkdir -p "$OUT_DIR" "$WORK_DIR"
# ---------------------------------------------------------
# utility
# ---------------------------------------------------------
log() {
  echo
  echo "=================================================="
  echo "$*"
  echo "=================================================="
}
has_audio() {
  ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$1" | grep -q .
}
file_exists_and_enabled() {
  local flag="$1"
  local file="$2"
  [[ "$flag" == "1" && -f "$file" ]]
}
escape_subtitle_path() {
  # ffmpegのsubtitlesフィルタに渡すファイルパスをエスケープ
  local p="$1"
  p="${p//\\/\\\\}"
  p="${p//:/\\:}"
  p="${p//\'/\\\'}"
  printf "%s" "$p"
}
# ---------------------------------------------------------
# 本編を末尾カット
# ---------------------------------------------------------
trim_main() {
  local src="$1"
  local dst="$2"
  if (( TRIM_T > 0 )); then
    local dur main_len
    dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src")"
    main_len="$(awk -v d="$dur" -v t="$TRIM_T" 'BEGIN{printf "%.3f", d - t}')"
    if awk -v m="$main_len" 'BEGIN{exit (m<=0)?0:1}'; then
      echo "[SKIP] $(basename "$src"): 動画長が TRIM_T 以下"
      return 1
    fi
    ffmpeg -hide_banner -y -i "$src" -t "$main_len" -c copy "$dst"
  else
    # 元ファイルの更新日時を維持すると既存SRTを再利用できる。
    # cp -fだと毎回新しい更新日時になり、Whisperが毎回再実行される。
    if [[ -s "$dst" ]] && [[ ! "$src" -nt "$dst" ]] && [[ "$(stat -f %z "$src")" == "$(stat -f %z "$dst")" ]]; then
      echo "-> 本編コピー再利用: $(basename "$dst")"
    else
      cp -p "$src" "$dst"
    fi
  fi
  return 0
}
# ---------------------------------------------------------
# 各セグメントを統一形式へ正規化
# 音声が無い場合は無音 stereo を付与
# ---------------------------------------------------------
normalize_segment() {
  local src="$1"
  local dst="$2"
  if has_audio "$src"; then
    ffmpeg -hide_banner -y \
      -i "$src" \
      -vf "scale=${WIDTH}:${HEIGHT}:flags=lanczos,fps=${FPS},format=yuv420p" \
      -c:v libx264 -crf "$CRF" -preset "$PRESET" \
      -c:a aac -b:a "$ABIT" -ar 48000 -ac 2 \
      -movflags +faststart \
      "$dst"
  else
    ffmpeg -hide_banner -y \
      -i "$src" \
      -f lavfi -i anullsrc=r=48000:cl=stereo \
      -vf "scale=${WIDTH}:${HEIGHT}:flags=lanczos,fps=${FPS},format=yuv420p" \
      -c:v libx264 -crf "$CRF" -preset "$PRESET" \
      -c:a aac -b:a "$ABIT" -ar 48000 -ac 2 \
      -shortest \
      -movflags +faststart \
      "$dst"
  fi
}
# ---------------------------------------------------------
# 複数 mp4 を concat demuxer で結合
# 前提: normalize 済み
# ---------------------------------------------------------
concat_segments() {
  local out="$1"
  shift
  local listfile="$WORK_DIR/concat_$$.txt"
  : > "$listfile"
  local f
  for f in "$@"; do
    printf "file '%s'\n" "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" >> "$listfile"
  done
  ffmpeg -hide_banner -y \
    -f concat -safe 0 -i "$listfile" \
    -c copy \
    "$out"
  rm -f "$listfile"
}
# ---------------------------------------------------------
# faster-whisper で SRT 生成
# fw_transcribe.py が同ディレクトリにある前提
# ---------------------------------------------------------
generate_srt() {
  local video="$1"
  local stem
  stem="$(basename "$video")"
  stem="${stem%.*}"
  local dir
  dir="$(dirname "$video")"
  local srt_path
  if [[ "$TASK" == "translate" || "$TASK" == "dual" || "$TASK" == "ai_translate" ]]; then
    srt_path="$dir/$stem.en.srt"
  else
    srt_path="$dir/$stem.srt"
  fi
  echo "-> 字幕生成: $(basename "$video")" >&2
  "$PYBIN" "$SCRIPT_DIR/fw_transcribe.py" "$video" "$FW_LANG" >&2
  if [[ ! -f "$srt_path" ]]; then
    echo "字幕ファイルが生成されませんでした: $srt_path" >&2
    exit 1
  fi
  printf "%s\n" "$srt_path"
}
# ---------------------------------------------------------
# 字幕付与
# hard: 焼き込み
# soft: mov_text で添付
# ---------------------------------------------------------
subtitle_video() {
  local src_video="$1"
  local srt_path="$2"
  local out_video="$3"
  if [[ "$BURN" == "hard" ]]; then
    echo "-> hard字幕付与"
    local esc_srt
    esc_srt="$(escape_subtitle_path "$srt_path")"
    ffmpeg -hide_banner -y \
      -i "$src_video" \
      -vf "subtitles=filename='${esc_srt}':charenc=UTF-8:force_style='FontName=${FONT},FontSize=28,Outline=2'" \
      -c:v "$VENC" -crf "$CRF" -preset "$PRESET" \
      -c:a aac -b:a "$ABIT" \
      -movflags +faststart \
      "$out_video"
  else
    echo "-> soft字幕付与"
    ffmpeg -hide_banner -y \
      -i "$src_video" -i "$srt_path" \
      -map 0:v -map 0:a? -map 1 \
      -c:v copy -c:a copy -c:s mov_text \
      -metadata:s:s:0 language="$SUB_LANG" \
      -movflags +faststart \
      "$out_video"
  fi
}
# ---------------------------------------------------------
# OP / MAIN / ED の最終結合
# ---------------------------------------------------------
build_merged_video() {
  local main_video="$1"
  local base_name="$2"
  local merged_out="$3"
  local segs=()
  local norm_paths=()
  if file_exists_and_enabled "$USE_OP" "$OP_FILE"; then
    segs+=("$OP_FILE")
  fi
  segs+=("$main_video")
  if file_exists_and_enabled "$USE_ED" "$ED_FILE"; then
    segs+=("$ED_FILE")
  fi
  local idx=0
  local seg norm
  for seg in "${segs[@]}"; do
    norm="$WORK_DIR/${base_name}__norm_${idx}.mp4"
    echo "-> 正規化: $(basename "$seg")"
    normalize_segment "$seg" "$norm"
    norm_paths+=("$norm")
    idx=$((idx+1))
  done
  echo "-> 結合"
  concat_segments "$merged_out" "${norm_paths[@]}"
}
# =========================================================
# main
# =========================================================
log "開始"
echo "DOWNLOADS_DIR: $DOWNLOADS_DIR"
echo "IN_DIR       : $IN_DIR"
echo "OUT_DIR      : $OUT_DIR"
echo "WORK_DIR     : $WORK_DIR"
echo "MERGE_STAGE  : $MERGE_STAGE"
echo "AUTO_YOUTUBE : $AUTO_YOUTUBE"
echo "PUBLISH_LIMIT: $PUBLISH_LIMIT"
echo "PROCESS_LIMIT: $PROCESS_LIMIT"
echo "YOUTUBE_DRY_RUN: $YOUTUBE_DRY_RUN"
echo "BURN         : $BURN"
echo "MODEL        : $MODEL"
echo "TASK         : $TASK"
echo "USE_OP       : $USE_OP (${OP_FILE})"
echo "USE_ED       : $USE_ED (${ED_FILE})"
echo "TRIM_T       : $TRIM_T"
echo "字幕モード   : $SUB_LANG (ai_translate/dualなら判定用日本語SRTも作成)"
echo "VENC         : $VENC"
if [[ ! -x "$PYBIN" ]]; then
  echo "Python が見つかりません: $PYBIN" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/fw_transcribe.py" ]]; then
  echo "fw_transcribe.py が見つかりません: $SCRIPT_DIR/fw_transcribe.py" >&2
  exit 1
fi
if [[ "$MERGE_STAGE" != "after_subtitle" && "$MERGE_STAGE" != "before_subtitle" ]]; then
  echo "MERGE_STAGE は after_subtitle か before_subtitle を指定してください" >&2
  exit 1
fi
if [[ "$BURN" != "hard" && "$BURN" != "soft" ]]; then
  echo "BURN は hard か soft を指定してください" >&2
  exit 1
fi
if [[ "$MERGE_STAGE" == "after_subtitle" && "$BURN" == "soft" ]]; then
  echo "after_subtitle + soft は非推奨のため停止します。" >&2
  echo "→ BURN=hard にするか、MERGE_STAGE=before_subtitle にしてください。" >&2
  exit 1
fi
if [[ "$TASK" != "transcribe" && "$TASK" != "translate" && "$TASK" != "dual" && "$TASK" != "ai_translate" ]]; then
  echo "TASK は transcribe / translate / dual / ai_translate にしてください" >&2
  exit 1
fi
# 日本語字幕と英語字幕の完成動画は別名保存し、誤って上書きしない。
if [[ "$TASK" == "dual" || "$TASK" == "translate" || "$TASK" == "ai_translate" ]]; then
  FINAL_SUFFIX="_final_en_subbed"
else
  FINAL_SUFFIX="_final_subbed"
fi

publish_completed() {
  local final="$1"; local src="$2"; local ja_srt="$3"
  if [[ "$AUTO_YOUTUBE" != "1" ]]; then return; fi
  if [[ "$PUBLISH_LIMIT" != "0" && "$published_count" -ge "$PUBLISH_LIMIT" ]]; then
    echo "[SKIP] AI投稿上限に到達: $PUBLISH_LIMIT 本 (動画保存済み: $final)"
    return
  fi
  local publisher_args=(--file "$final" --source-file "$src" --source-dir "$IN_DIR" --work-dir "$WORK_DIR")
  if [[ ( "$TASK" == "dual" || "$TASK" == "ai_translate" ) && -s "$ja_srt" ]]; then
    publisher_args+=(--srt "$ja_srt")
  fi
  if [[ "$YOUTUBE_DRY_RUN" == "1" ]]; then
    publisher_args+=(--dry-run)
  fi
  if ! "$PYBIN" "$SCRIPT_DIR/publish_youtube.py" "${publisher_args[@]}"; then
    echo "[WARN] AI投稿工程失敗。動画は保存済み: $final" >&2
  fi
  published_count=$((published_count+1))
}

shopt -s nullglob
found=0
published_count=0
processed_videos=0
for SRC in "$IN_DIR"/*."$EXT"; do
  # 外付けドライブ上のAppleDouble隠しファイルは動画ではない。
  [[ "$(basename "$SRC")" == .* ]] && continue
  [[ -s "$SRC" ]] || continue
  if [[ "$PROCESS_LIMIT" != "0" && "$processed_videos" -ge "$PROCESS_LIMIT" ]]; then
    echo "[INFO] 動画処理上限: $PROCESS_LIMIT 本"
    break
  fi
  processed_videos=$((processed_videos+1))
  found=1
  BN="$(basename "$SRC")"
  STEM="${BN%.$EXT}"
  FINAL_CANDIDATE="$OUT_DIR/${STEM}${FINAL_SUFFIX}.${EXT}"
  if [[ "${SKIP_EXISTING_FINAL:-1}" == "1" && -s "$FINAL_CANDIDATE" ]]; then
    echo "[SKIP] 完成済み。字幕処理は省略: $FINAL_CANDIDATE"
    publish_completed "$FINAL_CANDIDATE" "$SRC" "$WORK_DIR/${STEM}__$([[ "$MERGE_STAGE" == "after_subtitle" ]] && echo main_trim || echo merged_raw).srt"
    continue
  fi
  log "処理中: $BN"
  MAIN_TRIM="$WORK_DIR/${STEM}__main_trim.mp4"
  if ! trim_main "$SRC" "$MAIN_TRIM"; then
    continue
  fi
  if [[ "$MERGE_STAGE" == "after_subtitle" ]]; then
    # -----------------------------------------------------
    # 1) 本編だけ字幕化
    # 2) OP / 字幕付き本編 / ED を結合
    # -----------------------------------------------------
    MAIN_SUB="$WORK_DIR/${STEM}__main_sub.mp4"
    FINAL_OUT="$OUT_DIR/${STEM}${FINAL_SUFFIX}.${EXT}"
    SRT_PATH="$(generate_srt "$MAIN_TRIM")"
    subtitle_video "$MAIN_TRIM" "$SRT_PATH" "$MAIN_SUB"
    build_merged_video "$MAIN_SUB" "$STEM" "$FINAL_OUT"
  else
    # -----------------------------------------------------
    # 1) OP / 本編 / ED を結合
    # 2) 結合済み全体に字幕付与
    # -----------------------------------------------------
    MERGED_RAW="$WORK_DIR/${STEM}__merged_raw.mp4"
    FINAL_OUT="$OUT_DIR/${STEM}${FINAL_SUFFIX}.${EXT}"
    build_merged_video "$MAIN_TRIM" "$STEM" "$MERGED_RAW"
    SRT_PATH="$(generate_srt "$MERGED_RAW")"
    subtitle_video "$MERGED_RAW" "$SRT_PATH" "$FINAL_OUT"
  fi
  echo "-> 完成: $FINAL_OUT"
  publish_completed "$FINAL_OUT" "$SRC" "$WORK_DIR/${STEM}__$([[ "$MERGE_STAGE" == "after_subtitle" ]] && echo main_trim || echo merged_raw).srt"
done
if [[ "$found" -eq 0 ]]; then
  echo "対象動画が見つかりません: $IN_DIR/*.$EXT"
  exit 0
fi
log "完了"
echo "出力先: $OUT_DIR"
