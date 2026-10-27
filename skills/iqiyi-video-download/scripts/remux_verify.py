#!/usr/bin/env python3
"""Losslessly remux a local episode, fully decode it, and emit a local report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr[-6000:].strip() or f'{argv[0]} failed')
    return result.stdout


def probe(path):
    return json.loads(run([
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_type,codec_name,width,height',
        '-of', 'json', str(path),
    ]))


def media_check(info):
    streams = info.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if not video or not audio:
        raise RuntimeError('Expected both video and audio streams')
    if not video.get('width') or not video.get('height'):
        raise RuntimeError('Video dimensions are unavailable')
    if float(info.get('format', {}).get('duration', 0)) <= 0:
        raise RuntimeError('Media duration is unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    if not source.is_file():
        raise RuntimeError('Input file does not exist')
    if output.suffix.lower() != '.mp4':
        raise RuntimeError('Output must use the .mp4 extension')
    preview = output.with_suffix('.preview.jpg')
    report_path = output.with_suffix('.verification.json')
    for target in (output, preview, report_path):
        if target.exists():
            raise RuntimeError(f'Refusing to overwrite: {target}')
    for command in ('ffmpeg', 'ffprobe'):
        if not shutil.which(command):
            raise RuntimeError(f'Missing dependency: {command}')
    media_check(probe(source))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.iqiyi-verify-', dir=output.parent) as directory:
        staged = Path(directory) / 'episode.mp4'
        staged_preview = Path(directory) / 'preview.jpg'
        run(['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-xerror',
             '-i', str(source), '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy',
             '-movflags', '+faststart', '-n', str(staged)])
        info = probe(staged)
        media_check(info)
        run(['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-xerror',
             '-i', str(staged), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
        duration = float(info['format']['duration'])
        run(['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error',
             '-ss', str(duration / 2), '-i', str(staged), '-frames:v', '1',
             '-n', str(staged_preview)])
        if not staged_preview.is_file() or staged_preview.stat().st_size == 0:
            raise RuntimeError('Preview frame was not generated')
        digest = hashlib.sha256()
        with staged.open('rb') as media:
            for block in iter(lambda: media.read(1024 * 1024), b''):
                digest.update(block)
        report = {
            'status': 'verified', 'input': str(source), 'output': str(output),
            'full_decode_passed': True, 'duration_sec': duration,
            'size_bytes': staged.stat().st_size, 'sha256': digest.hexdigest(),
            'streams': info['streams'], 'preview': str(preview),
            'episode_identity': 'Must be checked against the browser episode and tvid',
            'report': str(report_path),
        }
        created = []
        try:
            # Atomic no-overwrite publication after successful validation.
            os.link(staged, output)
            created.append(output)
            os.link(staged_preview, preview)
            created.append(preview)
            with report_path.open('x', encoding='utf-8') as target:
                created.append(report_path)
                target.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        except Exception:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
