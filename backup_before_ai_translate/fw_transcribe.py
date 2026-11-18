#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""faster-whisper → SRT。元の統合bashの呼び出し形式: fw_transcribe.py 動画パス 言語"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def srt_timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('使い方: python fw_transcribe.py <動画> [ja|en|auto]')
    video = Path(sys.argv[1])
    if not video.is_file():
        sys.exit(f'動画がありません: {video}')
    language = sys.argv[2] if len(sys.argv) > 2 else os.getenv('FW_LANG', 'ja')
    if language.lower() in ('auto', 'none', ''):
        language = None
    task = os.getenv('TASK', 'transcribe').strip().lower()
    if task not in ('transcribe', 'translate'):
        sys.exit('TASK は transcribe または translate で指定してください')
    model_name = os.getenv('MODEL', 'small')
    compute_type = os.getenv('FWH_COMPUTE', 'int8')
    threads = int(os.getenv('FWH_THREADS', '0'))
    workers = int(os.getenv('FWH_WORKERS', '2'))
    from faster_whisper import WhisperModel
    print(f'[Whisper] model={model_name} compute={compute_type} task={task} language={language}', file=sys.stderr)
    model = WhisperModel(model_name, device='auto', compute_type=compute_type,
                         cpu_threads=threads, num_workers=workers)
    segments, info = model.transcribe(str(video), language=language, task=task,
                                      beam_size=5, vad_filter=True)
    target = video.with_name(video.stem + ('.en.srt' if task == 'translate' else '.srt'))
    count = 0
    with target.open('w', encoding='utf-8') as out:
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            count += 1
            out.write(f'{count}\n{srt_timestamp(segment.start)} --> {srt_timestamp(segment.end)}\n{text}\n\n')
    if not count:
        print('[Whisper] 音声認識テキストがありませんでした。AIは映像とファイル名を中心に判断します。', file=sys.stderr)
    print(f'[Whisper] {target} / {count} 行', file=sys.stderr)


if __name__ == '__main__':
    main()
