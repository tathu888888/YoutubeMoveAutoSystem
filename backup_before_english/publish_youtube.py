#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""完成動画 → AIメタデータ/サムネイル → YouTube投稿（重複防止・再開可）。"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parent
DOWNLOADS_DIR = Path(os.getenv('DOWNLOADS_DIR', str(Path.home() / 'Downloads'))).expanduser()
load_dotenv(BASE / '.env')
ASSETS = BASE / 'generated_assets'
STATE_PATH = BASE / 'youtube_publish_state.json'
LOG_PATH = BASE / 'youtube_publish_log.csv'
HISTORY_PATH = BASE / 'design_history_new.json'
CLIENT_SECRET = BASE / 'client_secret.json'
TOKEN_PATH = BASE / 'youtube_token_publish.json'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

TEXT_MODEL = os.getenv('TEXT_MODEL', 'gpt-5.6')
IMAGE_MODEL = os.getenv('IMAGE_MODEL', 'gpt-image-2')
IMAGE_API_SIZE = os.getenv('IMAGE_API_SIZE', '1984x1088')
IMAGE_QUALITY = os.getenv('IMAGE_QUALITY', 'high')
FINAL_W, FINAL_H = 1980, 1080
THUMB_MAX_BYTES = 2 * 1024 * 1024  # 添付のサムネイル.py と互換


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def update_state(state: dict, key: str, **changes: Any) -> None:
    record = state.setdefault(key, {})
    record.update(changes)
    record['updated_at'] = stamp()
    save_json(STATE_PATH, state)


def log_result(path: Path, status: str, detail: str = '', video_id: str = '') -> None:
    exists = LOG_PATH.exists()
    with LOG_PATH.open('a', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(['timestamp', 'local_video', 'status', 'youtube_id', 'detail'])
        writer.writerow([stamp(), str(path), status, video_id, detail])


def command(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def video_duration(path: Path) -> float:
    result = command('ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', str(path))
    return max(0.1, float(result.stdout.strip()))


def extract_video_frames(video: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    duration = video_duration(video)
    frames: list[Path] = []
    for index, portion in enumerate((0.18, 0.38, 0.60, 0.82), start=1):
        frame = dest / f'frame_{index}.jpg'
        if not frame.exists() or frame.stat().st_size == 0:
            at = max(0.0, min(duration - 0.08, duration * portion))
            command('ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-ss', str(at), '-i', str(video), '-frames:v', '1',
                    '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,format=yuv420p',
                    '-q:v', '3', str(frame))
        if frame.exists() and frame.stat().st_size:
            frames.append(frame)
    if not frames:
        raise RuntimeError('動画からサムネイル候補フレームを取得できませんでした')
    return frames


def video_stem(filename: Path) -> str:
    return filename.stem.removesuffix('_final_subbed')


def original_video(final: Path, source_dir: Path) -> Path:
    # イントロ・アウトロでなく本編からスクリーンショットを取る
    for ext in ('mp4', 'mov', 'm4v'):
        path = source_dir / f'{video_stem(final)}.{ext}'
        if path.exists():
            return path
    return final


def read_srt_for_video(final: Path, work_dir: Path, source_dir: Path) -> str:
    stem = video_stem(final)
    candidates = [
        work_dir / f'{stem}__main_trim.srt',
        work_dir / f'{stem}__merged_raw.srt',
        work_dir / f'{stem}__main_trim.en.srt',
        work_dir / f'{stem}__merged_raw.en.srt',
        source_dir / f'{stem}.srt',
        source_dir / f'{stem}.en.srt',
    ]
    for p in candidates:
        if p.is_file():
            content = p.read_text(encoding='utf-8-sig', errors='replace')
            content = re.sub(r'(?m)^\d+\s*$', '', content)
            content = re.sub(r'(?m)^\d\d:\d\d:\d\d[,\.]\d+\s*-->.*$', '', content)
            content = ' '.join(content.split())
            if content:
                # 長文でも前半・中間・後半をサンプルにして偏りを減らす
                if len(content) > 18000:
                    n = len(content)
                    content = content[:7500] + '\n［中盤抜粋］\n' + content[n//2-3500:n//2+3500] + '\n［終盤抜粋］\n' + content[-3500:]
                return content
    return ''



def read_srt_text(srt_path: Path) -> str:
    if not srt_path.is_file():
        raise FileNotFoundError(f'指定された字幕ファイルがありません: {srt_path}')
    content = srt_path.read_text(encoding='utf-8-sig', errors='replace')
    content = re.sub(r'(?m)^\d+\s*$', '', content)
    content = re.sub(r'(?m)^\d\d:\d\d:\d\d[,\.]\d+\s*-->.*$', '', content)
    content = ' '.join(content.split())
    if len(content) > 18000:
        n = len(content)
        content = (content[:7500] + '\n［中盤抜粋］\n' +
                   content[n//2-3500:n//2+3500] + '\n［終盤抜粋］\n' + content[-3500:])
    return content

def as_data_url(path: Path) -> str:
    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def parse_json_response(response_text: str) -> dict:
    text = response_text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
    text = re.sub(r'\s*```$', '', text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, flags=re.S)
        if not match:
            raise RuntimeError('AIの応答をJSONとして読み取れませんでした')
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise RuntimeError('AIの応答がJSONオブジェクトではありません')
    return obj


def clamp_text(text: Any, limit: int) -> str:
    result = str(text or '').strip()
    return result[:limit]


def normalize_metadata(raw: dict, original_name: str) -> dict:
    title = clamp_text(raw.get('title'), 100).strip(' \n')
    if not title:
        title = clamp_text(original_name, 100)
    if not title:
        raise RuntimeError('タイトルを生成できませんでした')
    description = clamp_text(raw.get('description'), 4800)
    tags = raw.get('tags', [])
    hashtags = raw.get('hashtags', [])
    if isinstance(tags, str):
        tags = re.split(r'[,、\n]', tags)
    if isinstance(hashtags, str):
        hashtags = re.split(r'[\s,、]+', hashtags)
    if not isinstance(tags, list):
        tags = []
    if not isinstance(hashtags, list):
        hashtags = []
    clean_tags = []
    for item in tags:
        val = str(item).strip().lstrip('#')
        if val and val not in clean_tags:
            clean_tags.append(val[:60])
    while len(','.join(clean_tags).encode('utf-8')) > 450:
        clean_tags.pop()
    hash_clean = []
    for item in hashtags:
        val = '#' + re.sub(r'[\s#]+', '', str(item))
        if len(val) > 1 and val not in hash_clean:
            hash_clean.append(val)
    if hash_clean:
        appendix = '\n\n' + ' '.join(hash_clean[:5])
        description = description[:max(0, 4950 - len(appendix))] + appendix
    design = raw.get('thumbnail_design')
    if not isinstance(design, dict):
        design = {}
    try:
        ref_index = int(design.get('reference_frame_index', 1))
    except (TypeError, ValueError):
        ref_index = 1
    design['reference_frame_index'] = max(1, min(4, ref_index))
    return {'title': title, 'description': description[:5000],
            'tags': clean_tags, 'hashtags': hash_clean[:5], 'thumbnail_design': design}


def generate_metadata(video: Path, frames: list[Path], transcript: str, history: list) -> dict:
    from openai import OpenAI
    if not os.getenv('OPENAI_API_KEY'):
        raise RuntimeError('.env に OPENAI_API_KEY を設定してください')
    prompt = f'''あなたは日本のYouTube向け動画SEO編集者・サムネイルクリエイティブディレクターです。
動画ファイル名: {video_stem(video)}
字幕/音声情報: {transcript if transcript else '字幕がありません。画像とファイル名で判明する範囲のみ判断すること。'}
直近のサムネイル構図履歴: {json.dumps(history[-8:], ensure_ascii=False)[:3000]}
次に添付する画像は動画の本編を時系列にキャプチャした4枚です（1〜4番）。
下記のJSONオブジェクト1個だけを回答してください。
{{
  "title": "日本語の魅力的なYouTubeタイトル（100文字以内）",
  "description": "動画内容に忠実な日本語の説明文。重要なキーワードを冒頭に、見どころや視聴案内を自然に記載。捏造はしない",
  "tags": ["関連YouTubeタグ", "検索語"],
  "hashtags": ["#短いハッシュタグ", "#ほか"],
  "thumbnail_design": {{
    "reference_frame_index": 1,
    "title_analysis": "確実に確認できる見どころ",
    "target_viewer": "対象視聴者",
    "click_trigger": "クリックしたくなる心理",
    "concept": "今回固有のサムネイルコンセプト",
    "headline": "画像内のメインコピー、原則2〜8文字",
    "subheadline": "必要な場合のみ補助コピー",
    "text_layout": "文字配置と安全余白",
    "composition": "人物/主題の大きさ・配置",
    "subject_preservation": "元画像で必ず維持する顔・衣装・ポーズなど",
    "background": "背景の設計",
    "lighting": "照明",
    "color_palette": "色の指定",
    "typography": "書体・縁取り",
    "effects": "演出",
    "avoid": "避ける表現",
    "image_prompt": "画像編集AIに渡す日本語の完成指示"
  }}
}}
重要:
- 字幕から判断できない出演者名・日時・イベント名・成果や原作設定は創作しない。ファイル名に含まれれば使用可。
- センセーショナルでも事実に忠実。未確認の主張は断定せず問いかけとして扱う。
- サムネイルは映画/ライブ広告の高品質。元映像の人物の顔立ち、体格、衣装、表情を維持。
- 顔・目・手に文字を重ねない。余白を確保。無意味な炎、ネオン、金色装飾の連発を避ける。
- 日本語のクリック率・検索流入を意識しつつ釣り・虚偽は避ける。
- reference_frame_index は実際に4枚の中から最適と思う1〜4の番号を指定。
- 動画に含まれない事象をサムネイルで捏造しない。
'''
    content: list[dict] = [{'type': 'input_text', 'text': prompt}]
    for frame in frames:
        content.append({'type': 'input_image', 'image_url': as_data_url(frame), 'detail': 'high'})
    response = OpenAI(api_key=os.getenv('OPENAI_API_KEY')).responses.create(
        model=TEXT_MODEL, input=[{'role': 'user', 'content': content}])
    if not response.output_text:
        raise RuntimeError('OpenAIからメタデータが返りませんでした')
    return normalize_metadata(parse_json_response(response.output_text), video_stem(video))


def make_image_prompt(metadata: dict) -> str:
    design = metadata['thumbnail_design']
    return f'''添付された元動画フレームを参照画像として、この動画に最適なYouTube用サムネイルを1枚作成。
動画タイトル: {metadata['title']}
デザイン設計書: {json.dumps(design, ensure_ascii=False, indent=2)}
制作条件:
- YouTube向け横長（最終的に1980×1080に加工する）。
- 参照写真の主役人物の顔・髪型・表情・衣装・ポーズ・身体の自然さをできるだけ維持し、別人化を防ぐ。
- 主役は顔や見せ場。スマホの小表示でも強い視線誘導。顔・目に文字を重ねない。
- 画像内の文字はheadline・subheadline以外を増やさず、判読しやすく正確に。
- 釣り画像や架空の出来事を描かず、元動画の見せ場に忠実。
- 品のある高品質な映画ポスター/音楽ライブ広告風。毎回異なる構図と配色。
- 重要な要素は端から少し離して配置する。
- 実在しないロゴ、余計な英字や透かしは不要。完成画像のみ生成。
'''


def save_youtube_jpeg(raw: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(raw)) as opened:
        pic = ImageOps.exif_transpose(opened).convert('RGB')
        pic = ImageOps.fit(pic, (FINAL_W, FINAL_H), method=Image.Resampling.LANCZOS)
        for subsampling in (0, 2):
            for quality in range(92, 45, -4):
                buf = io.BytesIO()
                pic.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True,
                         subsampling=subsampling)
                if buf.tell() < THUMB_MAX_BYTES:
                    path.write_bytes(buf.getvalue())
                    return
    raise RuntimeError('サムネイルを2MB以下に圧縮できませんでした')


def make_thumbnail(frame: Path, metadata: dict, output: Path) -> None:
    from openai import OpenAI
    # 添付.pyと同じ images.edit + GPT Image 2、サイズ統一・2MB制限
    with frame.open('rb') as src:
        result = OpenAI(api_key=os.getenv('OPENAI_API_KEY')).images.edit(
            model=IMAGE_MODEL, image=src, prompt=make_image_prompt(metadata),
            size=IMAGE_API_SIZE, quality=IMAGE_QUALITY,
            output_format='jpeg', output_compression=90,
        )
    if not result.data or not result.data[0].b64_json:
        raise RuntimeError('画像生成APIからデータを取得できませんでした')
    save_youtube_jpeg(base64.b64decode(result.data[0].b64_json), output)



def save_frame_fallback(frame: Path, output: Path) -> None:
    # 画像APIの制限・一時障害でも字幕付き完成動画を投稿できるよう、実際の動画フレームを使用
    with Image.open(frame) as img:
        rgb = ImageOps.fit(ImageOps.exif_transpose(img).convert('RGB'), (FINAL_W, FINAL_H),
                           method=Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        rgb.save(buf, 'JPEG', quality=88, optimize=True)
        save_youtube_jpeg(buf.getvalue(), output)

def get_youtube():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    if not CLIENT_SECRET.exists():
        raise FileNotFoundError('client_secret.json がありません。Google CloudのデスクトップOAuthクライアントを設置してください。')
    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except (ValueError, OSError):
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding='utf-8')
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(host='localhost', port=0, open_browser=True)
        TOKEN_PATH.write_text(creds.to_json(), encoding='utf-8')
    return build('youtube', 'v3', credentials=creds, cache_discovery=False)


def upload_new_video(service, path: Path, metadata: dict, visibility: str) -> str:
    from googleapiclient.http import MediaFileUpload
    body = {
        'snippet': {
            'title': metadata['title'],
            'description': metadata['description'],
            'tags': metadata['tags'],
            'categoryId': os.getenv('YOUTUBE_CATEGORY_ID', '24'),
            'defaultLanguage': 'ja',
        },
        'status': {
            'privacyStatus': visibility,
            'selfDeclaredMadeForKids': os.getenv('MADE_FOR_KIDS', 'false').lower() == 'true',
        },
    }
    media = MediaFileUpload(str(path), mimetype='video/mp4', resumable=True, chunksize=8*1024*1024)
    request = service.videos().insert(part='snippet,status', body=body, media_body=media)
    response = None
    retries = 0
    while response is None:
        try:
            _, response = request.next_chunk()
            retries = 0
        except Exception as e:
            # 5xx/429のみ同一再開可能セッションで再試行。曖昧な投稿結果は新規insertしない。
            status = getattr(getattr(e, 'resp', None), 'status', None)
            if status not in (429, 500, 502, 503, 504) or retries >= 4:
                raise
            retries += 1
            time.sleep(min(32, 2 ** retries))
    youtube_id = response.get('id') if isinstance(response, dict) else None
    if not youtube_id:
        raise RuntimeError('アップロード応答に動画IDがありません。重複防止のため自動再アップロードしません。')
    return youtube_id


def set_thumbnail(service, youtube_id: str, path: Path) -> None:
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(path), mimetype='image/jpeg')
    service.thumbnails().set(videoId=youtube_id, media_body=media).execute()


def process_video(video: Path, source_dir: Path, work_dir: Path, state: dict,
                  service: Any, dry_run: bool, visibility: str,
                  explicit_srt: Path | None = None, explicit_source: Path | None = None) -> None:
    video = video.resolve()
    if not video.exists() or video.stat().st_size == 0:
        raise RuntimeError(f'動画が存在しません: {video}')
    key = str(video)
    record = state.setdefault(key, {})
    if record.get('youtube_id') and record.get('thumbnail_uploaded'):
        print('  [SKIP] 投稿・サムネイル設定済み:', record['youtube_id'])
        return
    if record.get('upload_uncertain'):
        print('  [STOP] 前回のアップロード結果が不明です。YouTube Studioで確認後、状態ファイルを手作業で修正してください。')
        return
    label = hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
    folder = ASSETS / label
    folder.mkdir(parents=True, exist_ok=True)
    meta_file = folder / 'youtube_metadata.json'
    thumb_file = folder / 'thumbnail_1980x1080.jpg'
    meta_txt_file = folder / 'upload_preview.txt'

    if meta_file.exists():
        metadata = load_json(meta_file, {})
    else:
        source = explicit_source if explicit_source and explicit_source.is_file() else original_video(video, source_dir)
        print('  [1/4] 映像から4フレーム抽出:', source.name)
        frames = extract_video_frames(source, folder)
        transcript = (read_srt_text(explicit_srt) if explicit_srt else
                      read_srt_for_video(video, work_dir, source_dir))
        print('  [2/4] AIでタイトル・説明文・タグを作成（字幕文字数:', len(transcript), '）')
        history = load_json(HISTORY_PATH, [])
        metadata = generate_metadata(video, frames, transcript, history if isinstance(history, list) else [])
        save_json(meta_file, metadata)
        history = (history if isinstance(history, list) else []) + [{
            'title': metadata['title'], 'design': metadata['thumbnail_design'].get('concept', ''),
            'composition': metadata['thumbnail_design'].get('composition', ''),
            'color': metadata['thumbnail_design'].get('color_palette', ''),
        }]
        save_json(HISTORY_PATH, history[-30:])
    meta_txt_file.write_text(
        f"タイトル:\n{metadata['title']}\n\n説明文:\n{metadata['description']}\n\n"
        f"タグ:\n{', '.join(metadata.get('tags', []))}\n", encoding='utf-8')
    if not thumb_file.exists() or thumb_file.stat().st_size == 0:
        print('  [3/4] AIサムネイル画像を生成')
        source = explicit_source if explicit_source and explicit_source.is_file() else original_video(video, source_dir)
        frames = extract_video_frames(source, folder)
        index = max(1, min(len(frames), int(metadata['thumbnail_design'].get('reference_frame_index', 1))))
        try:
            make_thumbnail(frames[index - 1], metadata, thumb_file)
        except Exception as thumb_error:
            if os.getenv('ALLOW_FRAME_FALLBACK', 'true').lower() not in ('true', '1', 'yes'):
                raise
            print('  [WARN] AI画像生成に失敗。実映像のフレームを代替サムネイルとして使用:', thumb_error)
            save_frame_fallback(frames[index - 1], thumb_file)
            update_state(state, key, thumbnail_fallback_reason=str(thumb_error))
    else:
        print('  [3/4] サムネイル生成済み、再利用')
    print('  タイトル:', metadata['title'])
    print('  サムネイル:', thumb_file)
    update_state(state, key, metadata_path=str(meta_file), thumbnail_path=str(thumb_file),
                 local_size=video.stat().st_size, local_mtime=video.stat().st_mtime)
    if dry_run:
        print('  [4/4] テストモード: アップロードしません')
        log_result(video, 'dry_run', str(meta_file))
        return
    if service is None:
        service = get_youtube()
    youtube_id = state[key].get('youtube_id')
    if not youtube_id:
        # ネットワーク切断直前の結果不明時に新しい動画を重複投稿しないよう保留する
        update_state(state, key, upload_uncertain=True)
        print(f'  [4/4] YouTubeに{visibility}でアップロード中...')
        youtube_id = upload_new_video(service, video, metadata, visibility)
        update_state(state, key, youtube_id=youtube_id, upload_uncertain=False,
                     uploaded_at=stamp(), visibility=visibility)
    print('  [4/4] サムネイルをYouTubeに設定中:', youtube_id)
    # 処理直後にサムネイル設定可能になるまで短時間だけリトライ
    last_error = None
    for i in range(4):
        try:
            set_thumbnail(service, youtube_id, thumb_file)
            last_error = None
            break
        except Exception as e:
            last_error = e
            if i < 3:
                time.sleep(5 * (i + 1))
    if last_error:
        update_state(state, key, thumbnail_uploaded=False, thumbnail_error=str(last_error))
        raise RuntimeError(f'動画投稿済み({youtube_id})、サムネイル設定に失敗: {last_error}')
    update_state(state, key, thumbnail_uploaded=True, thumbnail_error='')
    log_result(video, 'uploaded', metadata['title'], youtube_id)
    print('  [OK] https://www.youtube.com/watch?v=' + youtube_id)


def main() -> int:
    parser = argparse.ArgumentParser(description='完成動画をAI解析してYouTubeへ自動投稿')
    parser.add_argument('--input-dir', type=Path, default=DOWNLOADS_DIR/'out2')
    parser.add_argument('--source-dir', type=Path, default=DOWNLOADS_DIR/'output')
    parser.add_argument('--work-dir', type=Path, default=DOWNLOADS_DIR/'_work_merge_sub')
    parser.add_argument('--file', type=Path, help='単独の完成MP4を処理')
    parser.add_argument('--srt', type=Path, help='字幕生成直後のSRTファイルを直接指定（再文字起こし不要）')
    parser.add_argument('--source-file', type=Path, help='OP/ED抜きの元動画を指定（サムネイル抽出用）')
    parser.add_argument('--limit', type=int, default=int(os.getenv('MAX_NEW_UPLOADS', '1')),
                        help='処理本数。0=無制限')
    parser.add_argument('--dry-run', action='store_true', help='AI生成のみ、投稿しない')
    args = parser.parse_args()
    visibility = os.getenv('PRIVACY_STATUS', 'private').lower()
    if visibility not in ('private', 'unlisted', 'public'):
        parser.error('PRIVACY_STATUS は private / unlisted / public です')
    auto_upload = os.getenv('AUTO_UPLOAD', 'true').lower() in ('true', '1', 'yes')
    dry_run = args.dry_run or not auto_upload
    if args.srt and not args.file:
        parser.error('--srt には --file の指定も必要です')
    if args.source_file and not args.file:
        parser.error('--source-file には --file の指定も必要です')
    if args.file:
        videos = [args.file]
    else:
        videos = sorted(args.input_dir.glob('*_final_subbed.mp4'), key=lambda p: p.stat().st_mtime)
    if not videos:
        print('[INFO] 完成動画がありません。出力ディレクトリ:', args.input_dir)
        return 0
    if not os.getenv('OPENAI_API_KEY'):
        print('[ERROR] OPENAI_API_KEYが未設定です。')
        return 1
    if not dry_run and not CLIENT_SECRET.exists():
        print('[ERROR] client_secret.json がありません。非公開テストなら --dry-run を使用してください。')
        return 1
    ASSETS.mkdir(exist_ok=True)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        raise RuntimeError('youtube_publish_state.json の内容が不正です')
    service = None
    attempted = 0
    failures = 0
    for video in videos:
        key = str(video.resolve())
        record = state.get(key, {})
        if record.get('youtube_id') and record.get('thumbnail_uploaded'):
            print('[SKIP] 完了:', video.name)
            continue
        if record.get('upload_uncertain'):
            print('[STOP] アップロード状態の確認待ち:', video.name)
            continue
        if args.limit > 0 and attempted >= args.limit:
            break
        attempted += 1
        print('\n' + '='*65 + '\n処理:', video.name)
        try:
            process_video(video, args.source_dir, args.work_dir, state, service, dry_run, visibility,
                          explicit_srt=args.srt, explicit_source=args.source_file)
        except Exception as e:
            failures += 1
            print('[ERROR]', str(e), file=sys.stderr)
            log_result(video, 'error', str(e), state.get(key, {}).get('youtube_id', ''))
            # 失敗時に実行を止めず、次の未処理ファイルも扱う
    print(f'\n完了: 対象{attempted}本 / 失敗{failures}本 / 保存先 {ASSETS}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
