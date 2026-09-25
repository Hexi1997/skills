"""纯函数单元测试，不联网。运行: python3 -m unittest discover -s tests"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bili_clip as bc  # noqa: E402


def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_elem(progress_ms: int, mode: int, text: str) -> bytes:
    body = encode_varint((2 << 3) | 0) + encode_varint(progress_ms)
    body += encode_varint((3 << 3) | 0) + encode_varint(mode)
    body += encode_varint((9 << 3) | 1) + b"\x00" * 8  # 假装一个 fixed64 字段
    t = text.encode()
    body += encode_varint((7 << 3) | 2) + encode_varint(len(t)) + t
    return encode_varint((1 << 3) | 2) + encode_varint(len(body)) + body


class TimeTests(unittest.TestCase):
    def test_parse_ts_forms(self):
        self.assertEqual(bc.parse_ts("90"), 90)
        self.assertEqual(bc.parse_ts("1:30"), 90)
        self.assertEqual(bc.parse_ts("01:02:03"), 3723)
        self.assertEqual(bc.parse_ts("1h2m3s"), 3723)
        self.assertEqual(bc.parse_ts("2m"), 120)
        with self.assertRaises(ValueError):
            bc.parse_ts("abc")

    def test_fmt(self):
        self.assertEqual(bc.fmt_ts(3723.4), "01:02:03")
        self.assertEqual(bc.fmt_ts(3723.4, srt=True), "01:02:03,400")
        self.assertEqual(bc.fmt_compact(3723), "01h02m03s")


class SelectionTests(unittest.TestCase):
    def test_parse_selection(self):
        self.assertEqual(bc.parse_selection("1,3-4", 5), [1, 3, 4])
        self.assertEqual(bc.parse_selection("all", 3), [1, 2, 3])
        self.assertEqual(bc.parse_selection("2、3 5", 5), [2, 3, 5])
        with self.assertRaises(ValueError):
            bc.parse_selection("6", 5)


class FilenameTests(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(bc.sanitize_filename('a/b:c*d?"e<f>g|h'), "a_b_c_d_e_f_g_h")
        self.assertEqual(bc.sanitize_filename("   "), "untitled")
        self.assertEqual(len(bc.sanitize_filename("x" * 100, 20)), 20)


class UrlTests(unittest.TestCase):
    def test_parse_url(self):
        self.assertEqual(bc.parse_bili_url("https://www.bilibili.com/video/BV13AbH6QEak?p=3")["page"], 3)
        self.assertEqual(bc.parse_bili_url("BV13AbH6QEak")["bvid"], "BV13AbH6QEak")
        self.assertEqual(bc.parse_bili_url("https://www.bilibili.com/video/av12345")["aid"], 12345)
        self.assertEqual(bc.parse_bili_url("https://space.bilibili.com/452396450/lists/8971822?type=season")["list"],
                         {"type": "season", "mid": 452396450, "id": 8971822})
        self.assertEqual(bc.parse_bili_url("https://space.bilibili.com/27564630/lists/217666?type=series")["list"],
                         {"type": "series", "mid": 27564630, "id": 217666})
        self.assertEqual(bc.parse_bili_url("https://space.bilibili.com/1/channel/collectiondetail?sid=99")["list"]["type"], "season")
        self.assertEqual(bc.parse_bili_url("https://space.bilibili.com/1/channel/seriesdetail?sid=99")["list"]["type"], "series")
        self.assertEqual(bc.list_url({"type": "series", "mid": 1, "id": 2}), "https://space.bilibili.com/1/lists/2?type=series")
        with self.assertRaises(ValueError):
            bc.parse_bili_url("https://example.com")


class DanmakuTests(unittest.TestCase):
    def test_protobuf_parse(self):
        blob = encode_elem(1500, 1, "哈哈哈哈哈") + encode_elem(61000, 5, "名场面")
        dms = bc.parse_danmaku_segment(blob)
        self.assertEqual(dms, [{"t": 1.5, "mode": 1, "text": "哈哈哈哈哈"}, {"t": 61.0, "mode": 5, "text": "名场面"}])

    def test_bucket_and_normalize(self):
        dms = [{"t": 1, "text": "哈哈哈哈哈"}, {"t": 2, "text": "哈哈哈"}, {"t": 40, "text": "草！！"}, {"t": 999, "text": "越界"}]
        b = bc.bucket_danmaku(dms, duration=60, bucket=30)
        self.assertEqual([x["count"] for x in b], [2, 1, 0])
        self.assertEqual(b[0]["top"], ["哈哈"])
        self.assertEqual(b[1]["top"], ["草"])

    def test_percentile(self):
        self.assertEqual(bc.percentile([1, 2, 3, 4, 5], 0.95), 5)
        self.assertEqual(bc.percentile([], 0.5), 0)


class TranscriptTests(unittest.TestCase):
    def test_filler(self):
        for t in ["哎呦", "哈哈哈哈", "嗯", "啊啊啊", "♪ ♪", "。。。", "哦对对对"]:
            self.assertTrue(bc.is_filler(t), t)
        for t in ["纯黑来了", "给你个激活码", "♪ 不熄的光 ♪"]:
            self.assertFalse(bc.is_filler(t), t)

    def test_compress(self):
        lines = [{"from": 0.5, "to": 1, "content": "哎呦"}, {"from": 2, "to": 3, "content": "纯黑说十二点再玩"},
                 {"from": 31, "to": 32, "content": "♪ 不熄的光 ♪"}]
        buckets = bc.bucket_danmaku([{"t": 3, "text": "草"}] * 5, duration=60)
        out = bc.compress_transcript(lines, buckets, 60, pct_threshold=5)
        self.assertIn("[00:00:00] (弹幕5 🔥 [草]) 纯黑说十二点再玩", out)
        self.assertIn("[00:00:30] (弹幕0) ♪ 不熄的光 ♪", out)
        self.assertNotIn("哎呦", out)


class CandidateTests(unittest.TestCase):
    parts = [{"bvid": "BV1", "cid": 11, "page": 1, "duration": 3600, "index": 1}]

    def test_validate_ok(self):
        data = {"candidates": [{"id": 1, "part": 1, "start": "10:00", "end": "12:30", "type": "搞笑", "title": "t", "reason": "r"}]}
        self.assertEqual(bc.validate_candidates(data, self.parts), [])
        bc.normalize_candidates(data, self.parts)
        c = data["candidates"][0]
        self.assertEqual((c["start"], c["end"], c["bvid"], c["cid"], c["status"]), (600, 750, "BV1", 11, "proposed"))

    def test_validate_errors(self):
        data = {"candidates": [
            {"id": 1, "part": 1, "start": "10:00", "end": "09:00", "type": "x", "title": "t", "reason": "r"},
            {"id": 1, "part": 9, "start": 0, "end": 1, "type": "x", "title": "t"},
        ]}
        errs = bc.validate_candidates(data, self.parts)
        self.assertTrue(any("越界" in e for e in errs))
        self.assertTrue(any("重复" in e for e in errs))
        self.assertTrue(any("part 无效" in e for e in errs))
        self.assertTrue(any("缺少字段 reason" in e for e in errs))

    def test_publish_plan(self):
        meta = {"owner": {"name": "主播A"}, "tags": ["直播回放", "主播A", "游戏"], "title": "回放", "tid": 172}
        c = {"id": 3, "bvid": "BV1", "page": 2, "start": 65, "end": 120, "title": "x" * 100, "reason": "好笑", "output": "/tmp/a.mp4"}
        plan = bc.build_publish_plan(meta, c, ["额外"])
        self.assertEqual(plan["tags"], ["主播A", "直播回放", "游戏", "切片", "额外"])
        self.assertEqual(len(plan["title"]), 80)
        self.assertEqual(plan["source"], "https://www.bilibili.com/video/BV1?p=2&t=65")
        self.assertEqual(plan["tid"], 172)

    def test_publish_plan_adds_owner_and_mentioned_streamer_space_links(self):
        meta = {"owner": {"name": "丝茉茉i", "mid": 27564630}, "tags": [], "title": "回放", "tid": 17}
        c = {"id": 9, "bvid": "BV1", "page": 1, "start": 1956, "end": 2069,
             "title": "丝茉茉唱《默》", "reason": "听闻高矮巴老师出门约会",
             "mentioned_streamers": ["巴老师的小号"], "output": "/tmp/9.mp4"}
        registry = {
            "27564630": {"mid": 27564630, "name": "丝茉茉i", "aliases": ["茉茉"]},
            "1588646945": {"mid": 1588646945, "name": "巴老师的小号", "aliases": ["高矮巴老师"]},
            "1191162350": {"mid": 1191162350, "name": "巴老师的录播小号", "aliases": []},
        }

        plan = bc.build_publish_plan(meta, c, [], registry=registry)

        self.assertIn("丝茉茉i：https://space.bilibili.com/27564630", plan["desc"])
        self.assertIn("巴老师的小号：https://space.bilibili.com/1588646945", plan["desc"])
        self.assertNotIn("https://space.bilibili.com/1191162350", plan["desc"])

    def test_preview_html(self):
        meta = {"title": "回放<b>", "owner": {"name": "A"}, "parts": [{"duration": 3600}]}
        data = {"candidates": [{"id": 1, "part": 1, "bvid": "BV1", "cid": 11, "page": 1, "start": 600, "end": 660,
                                "type": "搞笑", "title": "标题", "reason": "理由", "stats": {"danmaku_percentile": 97}}]}
        page = bc.build_preview_html(meta, data, None)
        self.assertIn("player.bilibili.com/player.html?isOutside=true&bvid=BV1&cid=11&p=1&high_quality=1&danmaku=1&t=600&autoplay=0", page)
        self.assertIn('data-embed="https://player.bilibili.com/player.html?isOutside=true&bvid=BV1&cid=11&p=1&high_quality=1&danmaku=1"', page)
        self.assertIn("onclick=\"jump('1',600.000)\"", page)
        self.assertIn("onclick=\"jump('1',652.000)\"", page)  # 结尾前 8 秒
        self.assertIn("function jump(", page)
        self.assertIn("回放&lt;b&gt;", page)
        self.assertIn("前 3%", page)
        # 拖动滑块微调起止：窗口是原区间前后各扩 120 秒，且不超过分 P 时长
        self.assertIn('data-role="start" min="480.0" max="780.0" step="1" value="600.0" data-orig="600.0"', page)
        self.assertIn('data-role="end" min="480.0" max="780.0" step="1" value="660.0" data-orig="660.0"', page)
        self.assertIn('id="copyAllTrims"', page)
        local = bc.build_preview_html(meta, data, {"1": "preview/1.mp4"})
        self.assertIn('<video controls preload="metadata" data-base="600.000" src="preview/1.mp4">', local)
        # 本地预览段本身只有 [start, end] 这段视频，滑块窗口不能扩到范围外
        self.assertIn('data-role="start" min="600.0" max="660.0" step="1" value="600.0" data-orig="600.0"', local)
        # 播放/暂停按钮只在本地 <video> 模式下出现，iframe 模式没有可靠的播放状态
        self.assertIn('class="playpause" data-act="playpause"', local)
        self.assertNotIn('class="playpause"', page)
        # 封面预览+选择只在传了 cover_files 时出现（本地 <video> 模式才拿得到帧）
        with_cover = bc.build_preview_html(meta, data, {"1": "preview/1.mp4"}, cover_files={"1": "preview/1_cover.jpg"})
        self.assertIn('<img class="coverimg" data-role="coverimg" src="preview/1_cover.jpg">', with_cover)
        self.assertIn('data-act="setcover"', with_cover)
        self.assertNotIn('class="cover"', local)  # 没传 cover_files 时不出现

    def test_preview_html_with_subtitles(self):
        import tempfile
        meta = {"title": "回放", "owner": {"name": "A"}, "parts": [{"duration": 3600}]}
        data = {"candidates": [{"id": 1, "part": 1, "bvid": "BV1", "cid": 11, "page": 1, "start": 600, "end": 660,
                                "type": "搞笑", "title": "标题", "reason": "理由"}]}
        with tempfile.TemporaryDirectory() as d:
            session = Path(d)
            raw = session / "raw"
            raw.mkdir()
            subs = [{"from": 595, "to": 597, "content": "范围内但在片段前"},
                    {"from": 610, "to": 612, "content": "片段内的一句话"},
                    {"from": 9000, "to": 9001, "content": "太远了不该出现"}]
            (raw / "p01_subtitle.json").write_text(json.dumps(subs, ensure_ascii=False), "utf-8")
            page = bc.build_preview_html(meta, data, None, session)
        self.assertIn('AI 字幕', page)
        self.assertIn('sub-line out" data-t="595.00" data-t2="597.00" onclick="jumpPlay(\'1\',595.000)">[00:09:55] 范围内但在片段前', page)
        self.assertIn('sub-line in" data-t="610.00" data-t2="612.00" onclick="jumpPlay(\'1\',610.000)">[00:10:10] 片段内的一句话', page)
        self.assertNotIn('太远了不该出现', page)
        # 没传 session 或没有字幕文件时，不渲染字幕面板
        self.assertNotIn('AI 字幕', bc.build_preview_html(meta, data, None))


class StreamerTests(unittest.TestCase):
    reg = {"1": {"mid": 1, "name": "苏墨墨", "aliases": ["陌陌", "墨墨"], "sessions": []},
           "2": {"mid": 2, "name": "墨鱼", "aliases": [], "sessions": []}}

    def test_resolve(self):
        self.assertEqual(bc.resolve_streamer(self.reg, "1")[0]["name"], "苏墨墨")
        self.assertEqual(bc.resolve_streamer(self.reg, "陌陌")[0]["name"], "苏墨墨")
        self.assertEqual(bc.resolve_streamer(self.reg, "墨鱼")[0]["name"], "墨鱼")
        hit, cands = bc.resolve_streamer(self.reg, "墨")
        self.assertIsNone(hit)
        self.assertEqual(len(cands), 2)
        self.assertEqual(bc.resolve_streamer(self.reg, "不存在"), (None, []))

    def test_replay_pick(self):
        vids = [{"bvid": "a", "title": "看看新赛季", "created": 30, "duration": 600, "season_id": 0},
                {"bvid": "b", "title": "【直播回放】渔力全开", "created": 20, "duration": 30000, "season_id": 5},
                {"bvid": "c", "title": "唱歌回", "created": 10, "duration": 7200, "season_id": 5}]
        self.assertEqual(bc.replay_score(vids[1], 5), 6)
        self.assertEqual(bc.pick_latest_replay(vids, None)["bvid"], "b")
        self.assertEqual(bc.pick_latest_replay(vids, 5)["bvid"], "b")
        self.assertIsNone(bc.pick_latest_replay(vids[:1], None))

    def test_registry_upsert(self):
        reg = {}
        meta = {"owner": {"name": "A", "mid": 9}, "anchor_bvid": "BV1", "title": "9月5日直播回放",
                "season": {"id": 77, "title": "刺的回放"}}
        e = bc.registry_upsert(reg, meta, Path("/tmp/s1"))
        self.assertEqual(e["replay_list"]["id"], 77)
        self.assertEqual(e["sessions"][0]["bvid"], "BV1")
        meta2 = dict(meta, anchor_bvid="BV2", title="另一场")
        bc.registry_upsert(reg, meta2, Path("/tmp/s2"))
        self.assertEqual([x["bvid"] for x in reg["9"]["sessions"]], ["BV2", "BV1"])

    def test_wbi_sign(self):
        key = bc.wbi_mixin_key("7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45")
        self.assertEqual(key, "ea1db124af3c7062474693fa704f4ff8")
        q = bc.wbi_sign({"foo": "114", "bar": "514", "zab": 1919810}, key, now=1702204169)
        self.assertTrue(q.startswith("bar=514&foo=114&w_rid="), q)
        self.assertTrue(q.endswith("&wts=1702204169&zab=1919810"), q)
        self.assertEqual(len(q.split("w_rid=")[1].split("&")[0]), 32)
        self.assertEqual(bc.length_to_seconds("512:57"), 30777)
        self.assertEqual(bc.length_to_seconds("01:24"), 84)


class FindTests(unittest.TestCase):
    def test_find_clusters_and_ranks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            session = Path(d)
            raw = session / "raw"
            raw.mkdir()
            parts = {"parts": [{"index": 1, "bvid": "BV1", "cid": 11, "page": 1, "title": "t", "duration": 7200}]}
            (raw / "parts.json").write_text(json.dumps(parts), "utf-8")
            subs = [{"from": 100, "to": 101, "content": "你们真的要听我唱恋爱循环吗"},
                    {"from": 130, "to": 131, "content": "Say no dam"},
                    {"from": 5000, "to": 5001, "content": "恋爱循环 再唱一次"}]
            (raw / "p01_subtitle.json").write_text(json.dumps(subs, ensure_ascii=False), "utf-8")
            dms = [{"t": 95, "text": "恋爱循环[爱]"}, {"t": 140, "text": "叽里咕噜"}, {"t": 5010, "text": "循环来了"}]
            (raw / "danmaku_11.json").write_text(json.dumps(dms, ensure_ascii=False), "utf-8")
            hits = bc.find_in_session(session, ["恋爱循环", "循环"], None, context=30)
            self.assertEqual(len(hits), 2)
            self.assertEqual((hits[0]["first_hit"], hits[0]["last_hit"]), (95, 100))
            self.assertEqual((hits[0]["start"], hits[0]["end"]), (65, 130))
            self.assertEqual(hits[0]["score"], 3 + 1)
            self.assertEqual(hits[1]["subtitle_hits"], 1)
            self.assertEqual(bc.find_in_session(session, ["不存在"], None), [])
            self.assertEqual(bc.find_in_session(session, ["循环"], [2]), [])


if __name__ == "__main__":
    unittest.main()


class DanmakuAssTest(unittest.TestCase):
    def test_ffmpeg_has_libass(self):
        self.assertTrue(bc.ffmpeg_has_libass("configuration: --enable-gpl --enable-libass --enable-libx264"))
        self.assertFalse(bc.ffmpeg_has_libass("configuration: --enable-gpl --enable-libx264"))

    def test_ass_time_and_escape(self):
        self.assertEqual(bc.ass_time(0), "0:00:00.00")
        self.assertEqual(bc.ass_time(3725.5), "1:02:05.50")
        self.assertEqual(bc.ass_time(-3), "0:00:00.00")
        self.assertEqual(bc.ass_escape(" a{b}\\N  c\n d "), "a｛b｝＼N c d")

    def test_layout_scroll_rows_and_window(self):
        dms = [{"t": 90, "mode": 1, "text": "太早了"},
               {"t": 100.0, "mode": 1, "text": "第一条弹幕"},
               {"t": 100.5, "mode": 1, "text": "紧跟着的第二条"},
               {"t": 130.0, "mode": 1, "text": "很久之后的第三条"},
               {"t": 199.9, "mode": 7, "text": "高级弹幕跳过"},
               {"t": 200.0, "mode": 1, "text": "刚好在终点之外"}]
        ev = bc.layout_danmaku(dms, 100, 200, 1920, 1080)
        self.assertEqual([e["text"][-5:] for e in ev], ["第一条弹幕", "着的第二条", "后的第三条"])
        self.assertEqual(ev[0]["start"], 0.0)
        self.assertAlmostEqual(ev[1]["start"], 0.5)
        y = lambda e: int(e["text"].split(",")[1])
        self.assertEqual(y(ev[0]), 2)              # 第一轨
        self.assertGreater(y(ev[1]), y(ev[0]))    # 挤到下一轨
        self.assertEqual(y(ev[2]), 2)              # 30 秒后第一轨已空出
        self.assertTrue(ev[0]["text"].startswith("{\\move(1920,2,-"))

    def test_layout_fixed_modes(self):
        dms = [{"t": 10, "mode": 5, "text": "顶部"}, {"t": 11, "mode": 5, "text": "顶部二"},
               {"t": 10, "mode": 4, "text": "底部"}]
        ev = bc.layout_danmaku(dms, 0, 60, 1280, 720)
        tops = [e for e in ev if "\\an8" in e["text"]]
        bottoms = [e for e in ev if "\\an2" in e["text"]]
        self.assertEqual(len(tops), 2)
        self.assertEqual(len(bottoms), 1)
        self.assertNotEqual(tops[0]["text"], tops[1]["text"])
        self.assertEqual(ev[0]["end"] - ev[0]["start"], bc.DANMAKU_FIXED_SECONDS)

    def test_build_ass(self):
        ev = bc.layout_danmaku([{"t": 5.25, "mode": 1, "text": "hi"}], 0, 30, 1920, 1080)
        txt = bc.build_ass(ev, 1920, 1080, opacity=0.75)
        self.assertIn("PlayResX: 1920", txt)
        self.assertIn("&H40FFFFFF", txt)  # 0.75 不透明 -> alpha 0x40
        self.assertIn("Dialogue: 0,0:00:05.25,0:00:13.25,Danmaku,,0,0,0,,{\\move(", txt)
        self.assertTrue(txt.endswith("\n"))
