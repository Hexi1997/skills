import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bili_clip as b
import local_subtitles as local


class LocalSubtitlesTest(unittest.TestCase):
    def test_analyze_prefers_bilibili_and_local_only_when_missing(self):
        for official in (True, False):
            with self.subTest(official=official), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); (root/'raw').mkdir()
                part = {'index': 1, 'duration': 60, 'cid': 123, 'bvid': 'BVtest', 'page': 1, 'title': 'test'}
                (root/'raw/parts.json').write_text(json.dumps({'parts': [part], 'owner': {'name': 'test'}}))
                args = argparse.Namespace(session=str(root), parts='1', refresh=False, browser='chrome',
                                          local_subtitles=True, whisper_model=None)
                rows = [{'from': 1, 'to': 2, 'content': '测试字幕'}]
                subs = [{'lan': 'ai-zh', 'subtitle_url': 'https://example.com/sub'}] if official else []
                with patch('bili_clip.ensure_cookies'), patch('bili_clip.fetch_subtitle_list', return_value=subs), \
                     patch('bili_clip.fetch_subtitle_body', return_value=rows), \
                     patch('bili_clip.fetch_danmaku', return_value=[]), \
                     patch('bili_clip.local_subtitle_lines', return_value=(rows, {'source': 'whisper.cpp'})) as local_call:
                    b.cmd_analyze(args)
                self.assertEqual(local_call.call_count, 0 if official else 1)
                source = json.loads((root/'raw/p01_subtitle_source.json').read_text())['source']
                self.assertEqual(source, 'bilibili' if official else 'whisper.cpp')
                self.assertTrue((root/'raw/p01_compact.txt').exists())

    def test_srt_multiline_and_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'input.srt'
            path.write_text('\ufeff1\r\n00:00:01,500 --> 00:00:02,500\r\n第一行\r\n第二行\r\n\r\n2\r\n00:00:03.000 --> 00:00:04.000\r\n结尾\r\n')
            rows = local.read_srt(path, 5)
            self.assertEqual(rows, [{'from': 1.5, 'to': 2.5, 'content': '第一行\n第二行'},
                                    {'from': 3.0, 'to': 4.0, 'content': '结尾'}])
            with self.assertRaises(ValueError):
                local.read_srt(path, 1)
            path.write_text('empty')
            with self.assertRaises(ValueError):
                local.read_srt(path, 5)

    def test_chunk_offsets_clamp_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio, model = root/'audio', root/'model'
            audio.write_bytes(b'audio'); model.write_bytes(b'model')
            calls = []

            def run(cmd, **kwargs):
                if '-oj' in cmd:
                    calls.append(cmd)
                    prefix = cmd[cmd.index('-of')+1]
                    Path(prefix+'.json').write_text(json.dumps({'transcription': [
                        {'text': '第一句', 'offsets': {'from': 0, 'to': 1000}},
                        {'text': '末尾', 'offsets': {'from': 99000, 'to': 301000}}]}))

            with patch('local_subtitles.shutil.which', return_value='/whisper'), patch('local_subtitles.subprocess.run', side_effect=run):
                rows = local.recognize(audio, model, root/'cache', 1000, '/ffmpeg', 10, 400)
                self.assertEqual([x['from'] for x in rows], [10, 109, 310, 409])
                self.assertEqual([x['to'] for x in rows], [11, 310, 311, 410])
                self.assertEqual(local.recognize(audio, model, root/'cache', 1000, '/ffmpeg', 10, 400), rows)
            self.assertEqual(len(calls), 2)

    def test_sample_isolation_import_and_overwrite_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'raw').mkdir()
            (root/'raw/parts.json').write_text(json.dumps({'parts': [{'index': 1, 'duration': 60}]}))
            canonical = root/'raw/p01_subtitle.json'
            canonical.write_text('[{"original": true}]')
            args = argparse.Namespace(session=str(root), part=1, start='10', duration='20',
                                      force=False, import_srt=None, model=None, audio=None)
            rows = [{'from': 10, 'to': 11, 'content': '样片'}]
            with patch('bili_clip.local_subtitle_lines', return_value=(rows, {'source': 'whisper.cpp'})):
                b.cmd_subtitles(args)
            self.assertEqual(json.loads(canonical.read_text()), [{'original': True}])
            self.assertTrue((root/'raw/asr_samples/p01_10-30_subtitle.json').exists())
            args.start, args.duration = '0', None
            with self.assertRaises(SystemExit):
                b.cmd_subtitles(args)
            canonical.unlink()
            srt = root/'input.srt'; srt.write_text('1\n00:00:02,000 --> 00:00:03,000\n导入\n')
            args.import_srt = str(srt)
            b.cmd_subtitles(args)
            self.assertEqual(json.loads(canonical.read_text())[0]['content'], '导入')
            self.assertEqual(json.loads((root/'raw/p01_subtitle_source.json').read_text())['source'], 'import-srt')
            self.assertTrue((root/'raw/p01.srt').exists())


if __name__ == '__main__':
    unittest.main()
