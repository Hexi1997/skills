"""Optional local subtitle ingestion and whisper.cpp recognition (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path


DEFAULT_MODEL = Path.home() / '.cache/bili-clip/models/ggml-small.bin'


def validate_lines(lines: list[dict], duration: float) -> list[dict]:
    result = []
    for row in lines:
        start, end = float(row['from']), float(row['to'])
        text = str(row['content']).strip()
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < duration
                and start < end <= duration + 1):
            raise ValueError(f'字幕时间越界: {start}–{end}，分 P 时长 {duration}')
        if text:
            result.append({'from': start, 'to': min(end, duration), 'content': text})
    result.sort(key=lambda x: (x['from'], x['to']))
    if not result:
        raise ValueError('没有识别到有效字幕，不写入正式字幕缓存')
    return result


def read_srt(path: Path, duration: float) -> list[dict]:
    text = path.read_text('utf-8-sig').replace('\r\n', '\n')
    pattern = re.compile(r'(?m)^(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*'
                         r'(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})[^\n]*\n')
    matches = list(pattern.finditer(text))
    rows = []
    for i, match in enumerate(matches):
        values = list(map(int, match.groups()))
        start = values[0]*3600 + values[1]*60 + values[2] + values[3]/1000
        end = values[4]*3600 + values[5]*60 + values[6] + values[7]/1000
        body = text[match.end():matches[i+1].start() if i+1 < len(matches) else len(text)]
        # The next cue's numeric index belongs to its header, not this cue.
        body = re.sub(r'\n\s*\n\s*\d+\s*$', '', body).strip()
        rows.append({'from': start, 'to': end, 'content': body})
    return validate_lines(rows, duration)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    tmp.replace(path)


def model_path(value: str | None) -> Path:
    path = Path(value or os.environ.get('BILI_CLIP_WHISPER_MODEL') or DEFAULT_MODEL).expanduser()
    if not path.is_file():
        raise ValueError(f'找不到 Whisper 模型: {path}。请下载多语言 ggml-small.bin，'
                         '或通过 --model / --whisper-model / BILI_CLIP_WHISPER_MODEL 指定')
    return path.resolve()


def recognize(audio: Path, model: Path, work: Path, duration: float, ffmpeg: str,
              start: float = 0, length: float | None = None, language: str = 'zh',
              chunk_seconds: int = 300) -> list[dict]:
    cli = shutil.which(os.environ.get('BILI_CLIP_WHISPER_CLI', 'whisper-cli'))
    if not cli:
        raise ValueError('找不到 whisper-cli；macOS 可 brew install whisper-cpp')
    stop = min(duration, start + length) if length is not None else duration
    if start < 0 or not start < stop or chunk_seconds < 1:
        raise ValueError('无效的识别区间或分块长度')
    # Separate caches for source/model/settings; completed chunks resume after interruption.
    settings = {'audio': str(audio.resolve()), 'audio_size': audio.stat().st_size,
                'audio_mtime': audio.stat().st_mtime_ns, 'model': str(model),
                'model_size': model.stat().st_size, 'model_mtime': model.stat().st_mtime_ns,
                'language': language, 'chunk_seconds': chunk_seconds, 'cli': cli,
                'schema': 1}
    key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    folder = work / key
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    cursor = start
    while cursor < stop:
        size = min(chunk_seconds, stop - cursor)
        stem = folder / f'{cursor:.3f}-{cursor+size:.3f}'
        # Append extensions to preserve fractional timestamps in filenames.
        result = Path(str(stem) + '.result.json')
        if result.exists():
            chunk = json.loads(result.read_text('utf-8'))
        else:
            wav = Path(str(stem) + '.wav')
            output = Path(str(stem) + '.whisper')
            print(f'本地识别 {cursor:.0f}–{cursor+size:.0f}s / {stop:.0f}s', flush=True)
            subprocess.run([ffmpeg, '-v', 'error', '-y', '-ss', str(cursor), '-i', str(audio),
                            '-t', str(size), '-vn', '-ar', '16000', '-ac', '1',
                            '-c:a', 'pcm_s16le', str(wav)], check=True)
            try:
                with Path(str(stem) + '.log').open('w') as log:
                    subprocess.run([cli, '-m', str(model), '-f', str(wav), '-l', language,
                                    '-oj', '-of', str(output), '-np'], stdout=log,
                                   stderr=subprocess.STDOUT, check=True)
                payload = json.loads(Path(str(output) + '.json').read_text('utf-8'))
                chunk = []
                for segment in payload['transcription']:
                    text = segment['text'].strip()
                    if not text or re.fullmatch(r'\[[^\]]+\]', text):
                        continue
                    a = max(0, segment['offsets']['from']/1000)
                    z = min(size, segment['offsets']['to']/1000)
                    if a < z:
                        chunk.append({'from': cursor+a, 'to': cursor+z, 'content': text})
                write_json(result, chunk)
            finally:
                wav.unlink(missing_ok=True)
        rows.extend(chunk)
        cursor += size
    return validate_lines(rows, duration)
