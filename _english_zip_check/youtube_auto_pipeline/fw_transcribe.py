#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""faster-whisper → SRT。元の統合bashの呼び出し形式: fw_transcribe.py 動画パス 言語"""
from __future__ import annotations

import os
import re
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
    if task not in ('transcribe', 'translate', 'dual'):
        sys.exit('TASK は transcribe / translate / dual で指定してください')
    model_name = os.getenv('MODEL', 'small')
    compute_type = os.getenv('FWH_COMPUTE', 'int8')
    threads = int(os.getenv('FWH_THREADS', '0'))
    workers = int(os.getenv('FWH_WORKERS', '2'))
    from faster_whisper import WhisperModel
    print(f'[Whisper] model={model_name} compute={compute_type} task={task} language={language}', file=sys.stderr)
    model = WhisperModel(model_name, device='auto', compute_type=compute_type,
                         cpu_threads=threads, num_workers=workers)
    # dual: 1度だけモデルをロードし、日本語原文と英語訳を個別にデコードする。
    # 日本語SRTは規約判定用の内部ファイル。映像に焼き込むのは英語SRTのみ。
    tasks = ('transcribe', 'translate') if task == 'dual' else (task,)
    for work_task in tasks:
        target = video.with_name(video.stem + ('.en.srt' if work_task == 'translate' else '.srt'))
        reuse = (os.getenv('REGENERATE_SRT', '0') != '1' and target.is_file()
                 and target.stat().st_mtime >= video.stat().st_mtime
                 and target.stat().st_size > 0
                 and '-->' in target.read_text(encoding='utf-8', errors='replace'))
        if reuse:
            print(f'[Whisper:{work_task}] 生成済みSRTを再利用: {target}', file=sys.stderr)
            continue
        segments, info = model.transcribe(
            str(video), language=language, task=work_task,
            beam_size=5, vad_filter=True,
        )
        # 最後まで成功する前にキャッシュが残らないよう、tmpへ書き込む。
        temp = target.with_name(target.name + '.tmp')
        count = 0
        with temp.open('w', encoding='utf-8') as out:
            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue
                count += 1
                out.write(f'{count}\n{srt_timestamp(segment.start)} --> {srt_timestamp(segment.end)}\n{text}\n\n')
        temp.replace(target)
        print(f'[Whisper:{work_task}] {target} / {count} 行', file=sys.stderr)



if __name__ == '__main__':
    main()
