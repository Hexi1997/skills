#!/usr/bin/env python3
"""bili-clip: B 站直播回放切片助手的确定性工具链。

子命令:
  cookies   导出 / 校验 B 站登录 cookie
  parts     列出视频的分 P（或合集各集），建立工作目录
  latest    找某个切过的主播最近一场回放，直接列分 P
  streamers 列出切过的主播 / 添加别名
  analyze   拉取 AI 字幕 + 弹幕，生成压缩稿供分析
  find      按关键词在字幕和弹幕里定位片段（用户点名某段时用）
  preview   根据 candidates.json 生成预览页
  cut       下载命中的分 P 并切出成品
  publish   用 biliup-rs 投稿（可选）
  clean     删除视频缓存

只依赖 python3 标准库 + yt-dlp + ffmpeg（烧弹幕需要带 libass 的 ffmpeg，publish 额外需要 biliup）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "0.1.0"

CONFIG_DIR = Path(os.environ.get("BILI_CLIP_CONFIG", Path.home() / ".config" / "bili-clip"))
COOKIES_FILE = CONFIG_DIR / "cookies.txt"
STREAMERS_DIR = CONFIG_DIR / "streamers"
REGISTRY_FILE = CONFIG_DIR / "streamers.json"
WORK_ROOT = Path(os.environ.get("BILI_CLIP_ROOT", Path.home() / "Movies" / "bili-clip"))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

BUCKET_SECONDS = 30
DANMAKU_FONT = {"darwin": "PingFang SC", "win32": "Microsoft YaHei"}.get(sys.platform, "Noto Sans CJK SC")
DANMAKU_FONT_SIZE = 57          # 1080p 下的字号，其它分辨率按高度等比缩放
DANMAKU_SCROLL_SECONDS = 8.0    # 滚动弹幕从右到左穿过画面的时间
DANMAKU_FIXED_SECONDS = 4.0     # 顶部 / 底部固定弹幕停留时间
DANMAKU_OPACITY = 0.75
DANMAKU_AREA = 0.5              # 弹幕占画面高度的比例（0.5 = 半屏）
FFMPEG_FULL_HINT = ("烧弹幕需要带 libass 的 ffmpeg：macOS 执行 brew install ffmpeg-full，"
                    "或用环境变量 BILI_CLIP_FFMPEG 指定一个带 libass 的 ffmpeg")
FILLER_RE = re.compile(r"^[\s哎哦嗯啊呃呀哈呵哟嘿嗨唉喔噢咦哇嗷呜哼嘛吧呢啦嘞诶欸哒咯哩嘻嘶噗么呗呦对是好行嗯呐哇！!。.，,、~～…？?\-—·]*$")
MUSIC_MARK = "♪"
REPLAY_WORDS = ("回放", "录播", "直播", "实况")
WBI_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
           12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
           57, 62, 11, 36, 20, 34, 44, 52]


# ---------------------------------------------------------------- utils

def die(msg: str, code: int = 1) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def fmt_ts(sec: float, srt: bool = False) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    if srt:
        return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"
    return f"{h:02d}:{m:02d}:{int(s):02d}"


def fmt_compact(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}h{sec % 3600 // 60:02d}m{sec % 60:02d}s"


def parse_ts(text: str) -> float:
    """接受 秒数 / MM:SS / HH:MM:SS / 1h23m10s。"""
    text = str(text).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s
    parts = text.split(":")
    if 2 <= len(parts) <= 3 and all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        parts = [float(p) for p in parts]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"无法解析时间: {text}")


def sanitize_filename(name: str, limit: int = 40) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return name[:limit] or "untitled"


def parse_selection(text: str, total: int) -> list[int]:
    """'1,3-4' -> [1,3,4]; 'all' -> 全部。返回 1-based 序号。"""
    text = (text or "all").strip().lower()
    if text in ("all", "*", "全部"):
        return list(range(1, total + 1))
    out: set[int] = set()
    for piece in re.split(r"[,\s，、]+", text):
        if not piece:
            continue
        if "-" in piece:
            a, b = piece.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(piece))
    bad = [i for i in out if i < 1 or i > total]
    if bad:
        raise ValueError(f"序号超出范围 1-{total}: {bad}")
    return sorted(out)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    info("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], **kw)


def need_tool(name: str, hint: str) -> str:
    path = shutil.which(name)
    if not path:
        die(f"找不到 {name}。{hint}")
    return path


# ---------------------------------------------------------------- ffmpeg

_ffmpeg_cache: dict[bool, str] = {}


def ffmpeg_candidates() -> list[str]:
    """按优先级列出可用的 ffmpeg：环境变量 > Homebrew ffmpeg-full（keg-only，不在 PATH）> PATH。"""
    raw = [os.environ.get("BILI_CLIP_FFMPEG"),
           "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
           shutil.which("ffmpeg")]
    out: list[str] = []
    for c in raw:
        if c and c not in out and os.path.isfile(c) and os.access(c, os.X_OK):
            out.append(c)
    return out


def ffmpeg_has_libass(version_output: str) -> bool:
    return "--enable-libass" in version_output


def find_ffmpeg(need_libass: bool = False) -> str:
    if need_libass in _ffmpeg_cache:
        return _ffmpeg_cache[need_libass]
    cands = ffmpeg_candidates()
    if not cands:
        die("找不到 ffmpeg。请先安装 ffmpeg（macOS: brew install ffmpeg-full）")
    chosen = None
    if need_libass:
        for c in cands:
            try:
                out = subprocess.run([c, "-version"], capture_output=True, text=True).stdout
            except OSError:
                continue
            if ffmpeg_has_libass(out):
                chosen = c
                break
        if not chosen:
            die(f"现有 ffmpeg 都没有编译 libass，没法把弹幕烧进画面。{FFMPEG_FULL_HINT}")
    else:
        chosen = cands[0]
    _ffmpeg_cache[need_libass] = chosen
    return chosen


def ffprobe_size(ffmpeg: str, src: Path) -> tuple[int, int]:
    probe = Path(ffmpeg).with_name("ffprobe")
    probe_path = str(probe) if probe.exists() else shutil.which("ffprobe")
    if probe_path:
        r = subprocess.run([probe_path, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(src)], capture_output=True, text=True)
        m = re.search(r"(\d+),(\d+)", r.stdout)
        if m:
            return int(m.group(1)), int(m.group(2))
    info("  读不到视频分辨率，按 1920x1080 排弹幕")
    return 1920, 1080


# ---------------------------------------------------------------- cookies / http

class Bili:
    def __init__(self, cookies_file: Path = COOKIES_FILE):
        self.cookies_file = cookies_file
        self.jar = http.cookiejar.MozillaCookieJar(str(cookies_file))
        if cookies_file.exists():
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def raw(self, url: str, retries: int = 3) -> bytes:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with self.opener.open(req, timeout=30) as r:
                    return r.read()
            except (urllib.error.URLError, TimeoutError) as e:  # noqa: PERF203
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"请求失败 {url}: {last}")

    def json(self, url: str) -> dict:
        data = json.loads(self.raw(url))
        if isinstance(data, dict) and data.get("code") not in (None, 0):
            raise RuntimeError(f"B 站接口返回 code={data.get('code')} {data.get('message')} ({url})")
        return data

    def is_logged_in(self) -> tuple[bool, str]:
        try:
            d = self.json("https://api.bilibili.com/x/web-interface/nav")
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        data = d.get("data") or {}
        self._wbi_img = data.get("wbi_img")
        return bool(data.get("isLogin")), data.get("uname") or ""

    _wbi_img: dict | None = None
    _mixin_key: str | None = None

    def wbi_mixin_key(self) -> str:
        if self._mixin_key:
            return self._mixin_key
        if not self._wbi_img:
            self._wbi_img = (self.json("https://api.bilibili.com/x/web-interface/nav").get("data") or {}).get("wbi_img")
        if not self._wbi_img:
            raise RuntimeError("拿不到 WBI 密钥（nav 接口无 wbi_img）")
        img = self._wbi_img["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub = self._wbi_img["sub_url"].rsplit("/", 1)[1].split(".")[0]
        self._mixin_key = wbi_mixin_key(img, sub)
        return self._mixin_key

    def wbi_json(self, url: str, params: dict) -> dict:
        return self.json(f"{url}?{wbi_sign(params, self.wbi_mixin_key())}")


def wbi_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in WBI_TAB)[:32]


def wbi_sign(params: dict, mixin_key: str, now: int | None = None) -> str:
    """B 站 WBI 签名：参数按 key 排序，去掉 !'()* 字符，加 wts，md5(query + mixin_key) 作为 w_rid。"""
    p = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in params.items()}
    p["wts"] = str(now or int(time.time()))
    query = urllib.parse.urlencode(sorted(p.items()))
    p["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(p.items()))


def export_cookies_from_browser(browser: str) -> bool:
    ytdlp = need_tool("yt-dlp", "请先安装: brew install yt-dlp 或 pip install yt-dlp")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # 任意一个公开视频即可触发 cookie 导出
    cmd = [ytdlp, "--no-update", "-q", "--skip-download", "--no-playlist",
           "--cookies-from-browser", browser, "--cookies", COOKIES_FILE,
           "https://www.bilibili.com/video/BV1GJ411x7h7"]
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        info(r.stderr.strip()[-800:])
    return COOKIES_FILE.exists()


def ensure_cookies(browser: str = "chrome", force: bool = False) -> Bili:
    if force or not COOKIES_FILE.exists():
        info(f"尝试从 {browser} 导出 B 站 cookie ...")
        export_cookies_from_browser(browser)
    if not COOKIES_FILE.exists():
        die("没有可用的 cookie。请任选其一：\n"
            f"  1) 确认 {browser} 已登录 B 站，再运行: bili_clip.py cookies --browser {browser}\n"
            f"  2) 用浏览器插件导出 Netscape 格式 cookies.txt 放到 {COOKIES_FILE}")
    bili = Bili()
    ok, uname = bili.is_logged_in()
    if not ok and not force:
        info("cookie 已过期，重新导出 ...")
        return ensure_cookies(browser, force=True)
    if not ok:
        die(f"cookie 无效（未登录）。请重新登录浏览器后重试，或手动更新 {COOKIES_FILE}")
    info(f"已登录: {uname}")
    return bili


# ---------------------------------------------------------------- bilibili api

def parse_bili_url(url: str) -> dict:
    """返回 {'bvid'} / {'aid'} / {'list': {'type': 'series'|'season', 'mid', 'id'}}。"""
    url = url.strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        page = re.search(r"[?&]p=(\d+)", url)
        return {"bvid": m.group(1), "page": int(page.group(1)) if page else None}
    m = re.search(r"av(\d+)", url)
    if m:
        return {"aid": int(m.group(1))}
    m = re.search(r"space\.bilibili\.com/(\d+)/lists/(\d+)", url)
    if m:
        t = re.search(r"[?&]type=(series|season)", url)
        return {"list": {"type": t.group(1) if t else "season", "mid": int(m.group(1)), "id": int(m.group(2))}}
    m = re.search(r"space\.bilibili\.com/(\d+)/channel/(collectiondetail|seriesdetail)\?sid=(\d+)", url)
    if m:
        return {"list": {"type": "season" if m.group(2) == "collectiondetail" else "series",
                         "mid": int(m.group(1)), "id": int(m.group(3))}}
    raise ValueError(f"无法识别的 B 站链接: {url}")


def list_url(lst: dict) -> str:
    return f"https://space.bilibili.com/{lst['mid']}/lists/{lst['id']}?type={lst['type']}"


def fetch_view(bili: Bili, bvid: str | None = None, aid: int | None = None) -> dict:
    q = f"bvid={bvid}" if bvid else f"aid={aid}"
    return bili.json(f"https://api.bilibili.com/x/web-interface/view?{q}")["data"]


def fetch_season_episodes(bili: Bili, mid: int, season_id: int) -> list[dict]:
    eps: list[dict] = []
    page = 1
    while True:
        d = bili.json("https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
                      f"?mid={mid}&season_id={season_id}&sort_reverse=false&page_num={page}&page_size=100")["data"]
        archives = d.get("archives") or []
        eps.extend(archives)
        total = (d.get("page") or {}).get("total", 0)
        if len(eps) >= total or not archives:
            break
        page += 1
    return eps


def fetch_list_meta(bili: Bili, lst: dict) -> dict:
    """列表名称、总数、所属 UP。"""
    if lst["type"] == "series":
        m = bili.json(f"https://api.bilibili.com/x/series/series?series_id={lst['id']}")["data"]["meta"]
        return {"title": m.get("name", ""), "total": m.get("total", 0), "mid": m.get("mid") or lst["mid"]}
    d = bili.json("https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
                  f"?mid={lst['mid']}&season_id={lst['id']}&sort_reverse=true&page_num=1&page_size=1")["data"]
    meta = d.get("meta") or {}
    return {"title": meta.get("name", ""), "total": (d.get("page") or {}).get("total", 0), "mid": meta.get("mid") or lst["mid"]}


def fetch_list_latest(bili: Bili, lst: dict, count: int = 5) -> list[dict]:
    """列表里最新的几集，按发布时间倒序。"""
    if lst["type"] == "series":
        d = bili.json(f"https://api.bilibili.com/x/series/archives?mid={lst['mid']}&series_id={lst['id']}"
                      f"&only_normal=true&sort=desc&pn=1&ps={count}")["data"]
        arcs = d.get("archives") or []
    else:
        d = bili.json("https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
                      f"?mid={lst['mid']}&season_id={lst['id']}&sort_reverse=true&page_num=1&page_size={count}")["data"]
        arcs = d.get("archives") or []
    out = [{"bvid": a["bvid"], "title": a["title"], "created": a.get("pubdate") or a.get("ctime") or 0,
            "duration": a.get("duration", 0), "length": fmt_ts(a.get("duration", 0))} for a in arcs]
    out.sort(key=lambda v: v["created"], reverse=True)
    return out


def fetch_subtitle_list(bili: Bili, bvid: str, cid: int) -> list[dict]:
    """字幕列表。必须走 WBI 签名的 player/wbi/v2：旧的 player/v2 对长视频只返回残缺片段。"""
    try:
        d = bili.wbi_json("https://api.bilibili.com/x/player/wbi/v2", {"bvid": bvid, "cid": cid})
    except Exception as e:  # noqa: BLE001
        info(f"wbi/v2 失败，回退旧接口（字幕可能不完整）: {e}")
        d = bili.json(f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}")
    return ((d.get("data") or {}).get("subtitle") or {}).get("subtitles") or []


def pick_subtitle(subs: list[dict]) -> dict | None:
    for s in subs:
        if s.get("lan") == "ai-zh":
            return s
    for s in subs:
        if str(s.get("lan", "")).startswith("ai-"):
            return s
    return subs[0] if subs else None


def fetch_subtitle_body(bili: Bili, sub: dict) -> list[dict]:
    url = sub["subtitle_url"]
    if url.startswith("//"):
        url = "https:" + url
    body = json.loads(bili.raw(url)).get("body") or []
    return [{"from": float(b.get("from", 0)), "to": float(b.get("to", 0)), "content": str(b.get("content", "")).strip()}
            for b in body]


def fetch_tags(bili: Bili, bvid: str) -> list[str]:
    try:
        d = bili.json(f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}")
        return [t["tag_name"] for t in (d.get("data") or [])]
    except Exception:  # noqa: BLE001
        return []


# --- 弹幕: 分段 protobuf，手写最小解析器，避免引入依赖

def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        c = b[i]
        i += 1
        result |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return result, i


def _skip(b: bytes, i: int, wt: int) -> int:
    if wt == 0:
        _, i = _varint(b, i)
    elif wt == 1:
        i += 8
    elif wt == 2:
        length, i = _varint(b, i)
        i += length
    elif wt == 5:
        i += 4
    else:
        raise ValueError(f"unknown wire type {wt}")
    return i


def parse_danmaku_elem(b: bytes) -> dict:
    i = 0
    e: dict = {}
    while i < len(b):
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 2:
            length, i = _varint(b, i)
            v = b[i:i + length]
            i += length
            if f == 7:
                e["text"] = v.decode("utf-8", "ignore")
        elif wt == 0:
            v, i = _varint(b, i)
            if f == 2:
                e["t"] = v / 1000.0
            elif f == 3:
                e["mode"] = v
        else:
            i = _skip(b, i, wt)
    return e


def parse_danmaku_segment(b: bytes) -> list[dict]:
    i = 0
    out = []
    while i < len(b):
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 2:
            length, i = _varint(b, i)
            v = b[i:i + length]
            i += length
            if f == 1:
                e = parse_danmaku_elem(v)
                if "t" in e and "text" in e:
                    out.append(e)
        else:
            i = _skip(b, i, wt)
    return out


def fetch_danmaku(bili: Bili, cid: int, duration: float) -> list[dict]:
    segments = int(duration // 360) + 1

    def one(idx: int) -> list[dict]:
        url = f"https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={idx}"
        try:
            return parse_danmaku_segment(bili.raw(url))
        except Exception as e:  # noqa: BLE001
            info(f"  弹幕分段 {idx} 失败: {e}")
            return []

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(one, range(1, segments + 1)))
    dms = [d for seg in results for d in seg]
    dms.sort(key=lambda d: d["t"])
    return dms


def fetch_space_videos(bili: Bili, mid: int, count: int = 30) -> list[dict]:
    """UP 主最近投稿，按发布时间倒序。"""
    d = bili.wbi_json("https://api.bilibili.com/x/space/wbi/arc/search",
                      {"mid": mid, "ps": min(count, 50), "pn": 1, "order": "pubdate",
                       "platform": "web", "web_location": 1550101})
    out = []
    for v in ((d.get("data") or {}).get("list") or {}).get("vlist") or []:
        out.append({"bvid": v["bvid"], "title": v["title"], "created": v["created"],
                    "length": v.get("length", ""), "duration": length_to_seconds(v.get("length", "")),
                    "season_id": v.get("season_id") or 0, "play": v.get("play")})
    return out


def length_to_seconds(length: str) -> int:
    parts = [int(x) for x in str(length).split(":") if x.strip().isdigit()]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def replay_score(video: dict, replay_season_id: int | None) -> int:
    """越大越像直播回放：标题关键词 +3，属于已知回放合集 +2，时长 ≥60 分钟 +1。"""
    score = 0
    if any(w in video["title"] for w in REPLAY_WORDS):
        score += 3
    if replay_season_id and video.get("season_id") == replay_season_id:
        score += 2
    if video.get("duration", 0) >= 3600:
        score += 1
    return score


def pick_latest_replay(videos: list[dict], replay_season_id: int | None) -> dict | None:
    scored = [(replay_score(v, replay_season_id), v) for v in videos]
    best = [v for sc, v in scored if sc >= 2]
    if not best:
        best = [v for sc, v in scored if sc >= 1]
    if not best:
        return None
    return max(best, key=lambda v: v["created"])


# --- 切过的主播登记表

def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        try:
            return json.loads(REGISTRY_FILE.read_text("utf-8"))
        except json.JSONDecodeError:
            info(f"登记表损坏，忽略: {REGISTRY_FILE}")
    return {}


def save_registry(reg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), "utf-8")


def registry_upsert(reg: dict, meta: dict, session: Path) -> dict:
    mid = str(meta["owner"]["mid"])
    entry = reg.get(mid) or {"name": meta["owner"]["name"], "mid": int(mid), "aliases": [], "sessions": []}
    entry["name"] = meta["owner"]["name"]
    season = meta.get("season")
    if (not entry.get("replay_list") and season
            and (any(w in season["title"] for w in REPLAY_WORDS) or any(w in meta["title"] for w in REPLAY_WORDS))):
        entry["replay_list"] = {"type": "season", "mid": int(mid), "id": season["id"], "title": season["title"]}
    entry["last_bvid"] = meta["anchor_bvid"]
    entry["last_title"] = meta["title"]
    entry["last_session"] = str(session)
    entry["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    sessions = [x for x in entry.get("sessions", []) if x.get("bvid") != meta["anchor_bvid"]]
    sessions.insert(0, {"bvid": meta["anchor_bvid"], "title": meta["title"], "session": str(session),
                        "at": entry["updated_at"]})
    entry["sessions"] = sessions[:20]
    reg[mid] = entry
    return entry


def registry_set_list(reg: dict, bili: Bili, lst: dict) -> dict:
    """把一个回放列表绑定到它所属的主播（不存在则新建条目）。"""
    lmeta = fetch_list_meta(bili, lst)
    mid = str(lmeta["mid"])
    entry = reg.get(mid)
    if not entry:
        latest = fetch_list_latest(bili, lst, 1)
        name = f"uid{mid}"
        if latest:
            name = fetch_view(bili, bvid=latest[0]["bvid"])["owner"]["name"]
        entry = {"name": name, "mid": int(mid), "aliases": [], "sessions": []}
    entry["replay_list"] = {"type": lst["type"], "mid": int(mid), "id": lst["id"], "title": lmeta["title"],
                            "total": lmeta["total"]}
    entry["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    reg[mid] = entry
    return entry


def resolve_streamer(reg: dict, query: str) -> tuple[dict | None, list[dict]]:
    """按 uid / 名字 / 别名精确匹配；否则子串匹配。返回 (唯一命中, 全部候选)。"""
    q = query.strip().lower()
    entries = list(reg.values())
    if q.isdigit():
        hit = [e for e in entries if str(e["mid"]) == q]
        return (hit[0] if hit else None), hit
    exact = [e for e in entries if e["name"].lower() == q or q in [a.lower() for a in e.get("aliases", [])]]
    if len(exact) == 1:
        return exact[0], exact
    fuzzy = [e for e in entries
             if q in e["name"].lower() or any(q in a.lower() or a.lower() in q for a in e.get("aliases", []))]
    return (fuzzy[0] if len(fuzzy) == 1 else None), (exact or fuzzy)


# ---------------------------------------------------------------- session dir

def session_dir_for(bvid: str, title: str) -> Path:
    return WORK_ROOT / f"{bvid}_{sanitize_filename(title, 20)}"


def resolve_session(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_dir() and (p / "raw" / "parts.json").exists():
        return p
    m = re.search(r"(BV[0-9A-Za-z]{10})", arg)
    if m and WORK_ROOT.exists():
        for d in sorted(WORK_ROOT.iterdir()):
            if d.name.startswith(m.group(1)) and (d / "raw" / "parts.json").exists():
                return d
    die(f"找不到工作目录: {arg}（先运行 parts）")
    raise AssertionError


def load_parts(session: Path) -> dict:
    return json.loads((session / "raw" / "parts.json").read_text("utf-8"))


def load_candidates(session: Path) -> dict:
    f = session / "candidates.json"
    if not f.exists():
        die(f"缺少 {f}，请先完成分析并写入候选")
    try:
        return json.loads(f.read_text("utf-8"))
    except json.JSONDecodeError as e:
        die(f"candidates.json 不是合法 JSON: {e}")
    raise AssertionError


def save_candidates(session: Path, data: dict) -> None:
    (session / "candidates.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- cmd: cookies

def cmd_cookies(args: argparse.Namespace) -> None:
    bili = ensure_cookies(args.browser, force=args.refresh)
    ok, uname = bili.is_logged_in()
    print(json.dumps({"cookies_file": str(COOKIES_FILE), "logged_in": ok, "uname": uname}, ensure_ascii=False))


# ---------------------------------------------------------------- cmd: parts

def build_parts_from_view(bili: Bili, view: dict) -> list[dict]:
    parts = []
    for p in view.get("pages") or []:
        parts.append({
            "bvid": view["bvid"], "aid": view["aid"], "cid": p["cid"], "page": p["page"],
            "title": p.get("part") or view["title"], "video_title": view["title"],
            "duration": p.get("duration", 0),
        })
    return parts


def run_parts(bili: Bili, url: str, whole_season: bool = False) -> tuple[Path, dict]:
    ref = parse_bili_url(url)
    if "list" in ref:
        die("run_parts 不接受列表链接")
    if True:
        view = fetch_view(bili, bvid=ref.get("bvid"), aid=ref.get("aid"))
        anchor_bvid, anchor_title = view["bvid"], view["title"]
        season = view.get("ugc_season")
        episode_views = None
        if whole_season:
            if not season:
                die("该视频不属于任何合集，去掉 --season 重试")
            eps = [e for sec in season.get("sections") or [] for e in sec.get("episodes") or []]
            anchor_title = season.get("title") or anchor_title
            episode_views = [view if e["bvid"] == view["bvid"] else fetch_view(bili, bvid=e["bvid"]) for e in eps]

    if episode_views is None:
        parts = build_parts_from_view(bili, view)
    else:
        parts = [p for v in episode_views for p in build_parts_from_view(bili, v)]

    session = session_dir_for(anchor_bvid, anchor_title)
    (session / "raw").mkdir(parents=True, exist_ok=True)
    (session / "clips").mkdir(exist_ok=True)

    info(f"检查 {len(parts)} 个分 P 的字幕与弹幕 ...")

    def enrich(p: dict) -> dict:
        subs = fetch_subtitle_list(bili, p["bvid"], p["cid"])
        chosen = pick_subtitle(subs)
        p["subtitle_langs"] = [s.get("lan") for s in subs]
        p["subtitle"] = {"lan": chosen.get("lan"), "url": chosen.get("subtitle_url")} if chosen else None
        cache = session / "raw" / f"danmaku_{p['cid']}.json"
        if cache.exists():
            dms = json.loads(cache.read_text("utf-8"))
        else:
            dms = fetch_danmaku(bili, p["cid"], p["duration"])
            cache.write_text(json.dumps(dms, ensure_ascii=False), "utf-8")
        p["danmaku_count"] = len(dms)
        return p

    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = list(ex.map(enrich, parts))
    for i, p in enumerate(parts, 1):
        p["index"] = i

    owner = view["owner"]
    meta = {
        "version": __version__,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_url": url,
        "anchor_bvid": anchor_bvid,
        "title": anchor_title,
        "owner": {"name": owner["name"], "mid": owner["mid"]},
        "tid": view.get("tid"), "tname": view.get("tname") or view.get("tname_v2") or "",
        "stat": {k: (view.get("stat") or {}).get(k) for k in ("view", "like", "danmaku", "reply")},
        "tags": fetch_tags(bili, view["bvid"]),
        "season": ({"id": view["ugc_season"]["id"], "title": view["ugc_season"]["title"],
                    "episodes": [{"bvid": e["bvid"], "title": e["title"]}
                                 for sec in view["ugc_season"].get("sections") or []
                                 for e in sec.get("episodes") or []]}
                   if view.get("ugc_season") else None),
        "parts": parts,
    }
    (session / "raw" / "parts.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    reg = load_registry()
    registry_upsert(reg, meta, session)
    save_registry(reg)
    return session, meta


def print_parts(session: Path, meta: dict, ref: dict, whole_season: bool) -> None:
    owner = meta["owner"]
    parts = meta["parts"]
    profile = STREAMERS_DIR / f"{sanitize_filename(owner['name'])}.md"
    print(f"工作目录: {session}")
    print(f"主播: {owner['name']} (uid {owner['mid']})  分区 tid: {meta['tid']} {meta['tname']}  "
          f"播放 {meta['stat'].get('view')}  标签: {'、'.join(meta['tags'][:6])}")
    print(f"主播档案: {profile} {'(已存在)' if profile.exists() else '(不存在，分析后需创建)'}")
    print()
    print(f"{'序号':<4} {'时长':<9} {'字幕':<6} {'弹幕':<7} 标题")
    for p in parts:
        sub = p["subtitle"]["lan"] if p["subtitle"] else "无"
        print(f"{p['index']:<4} {fmt_ts(p['duration']):<9} {sub:<6} {p['danmaku_count']:<7} [{p['bvid']} p{p['page']}] {p['title']}")
    missing = [p["index"] for p in parts if not p["subtitle"]]
    if missing:
        print(f"\n注意: 序号 {missing} 没有 AI 字幕，无法分析（B 站字幕生成有延迟，可稍后重试）。")
    if meta["season"] and not whole_season:
        print(f"\n该视频属于合集《{meta['season']['title']}》共 {len(meta['season']['episodes'])} 集；"
              f"要把整个合集当一场处理，加 --season 重新运行 parts。")


def cmd_parts(args: argparse.Namespace) -> None:
    bili = ensure_cookies(args.browser)
    ref = parse_bili_url(args.url)
    if "list" in ref:
        # 给的是系列/合集链接：登记为该主播的回放列表，并处理最新一集
        reg = load_registry()
        entry = registry_set_list(reg, bili, ref["list"])
        save_registry(reg)
        lst = entry["replay_list"]
        print(f"已登记 {entry['name']} 的回放列表《{lst['title']}》（{lst['type']} {lst['id']}，共 {lst.get('total')} 集），处理最新一集。")
        latest = fetch_list_latest(bili, lst, 1)
        if not latest:
            die("列表为空")
        args.url = f"https://www.bilibili.com/video/{latest[0]['bvid']}"
        ref = parse_bili_url(args.url)
    session, meta = run_parts(bili, args.url, args.season)
    print_parts(session, meta, ref, args.season)


# ---------------------------------------------------------------- cmd: latest / streamers

def cmd_streamers(args: argparse.Namespace) -> None:
    reg = load_registry()
    if args.set_list:
        bili = ensure_cookies(args.browser)
        ref = parse_bili_url(args.set_list)
        if "list" not in ref:
            die("--set-list 需要系列或合集链接，例如 https://space.bilibili.com/<uid>/lists/<id>?type=series")
        entry = registry_set_list(reg, bili, ref["list"])
        save_registry(reg)
        lst = entry["replay_list"]
        print(f"已绑定: {entry['name']} (uid {entry['mid']}) → 《{lst['title']}》 {lst['type']} {lst['id']}，共 {lst.get('total')} 集")
        return
    if args.alias:
        entry, cands = resolve_streamer(reg, args.alias[0])
        if not entry:
            die(f"找不到主播 {args.alias[0]}" + (f"，候选: {[c['name'] for c in cands]}" if cands else ""))
        for a in args.alias[1:]:
            if a and a not in entry["aliases"] and a != entry["name"]:
                entry["aliases"].append(a)
        save_registry(reg)
        print(f"{entry['name']} 的别名: {entry['aliases']}")
        return
    if not reg:
        print("还没有切过任何主播。运行 parts 后会自动登记。")
        return
    print(f"{'uid':<12} {'主播':<16} {'别名':<20} {'回放列表':<28} 最近一场")
    for e in sorted(reg.values(), key=lambda x: x.get("updated_at", ""), reverse=True):
        lst = e.get("replay_list")
        lst_txt = f"{lst['title']} ({lst['type']} {lst['id']})" if lst else "- (未绑定，latest 只能猜)"
        print(f"{e['mid']:<12} {e['name']:<16} {'、'.join(e.get('aliases', [])):<20} {lst_txt:<28} "
              f"{e.get('last_bvid', '')} {e.get('last_title', '')[:30]}")


def cmd_latest(args: argparse.Namespace) -> None:
    reg = load_registry()
    bili = ensure_cookies(args.browser)
    entry = None
    if "bilibili.com" in args.streamer:
        ref = parse_bili_url(args.streamer)
        if "list" not in ref:
            die("latest 只接受主播名 / uid / 系列或合集链接；普通视频链接请用 parts")
        entry = registry_set_list(reg, bili, ref["list"])
        save_registry(reg)
        print(f"已登记 {entry['name']} 的回放列表《{entry['replay_list']['title']}》")
    else:
        entry, cands = resolve_streamer(reg, args.streamer)
        if not entry:
            if cands:
                die("主播名不唯一，请用 uid 指定: " + "; ".join(f"{c['name']}={c['mid']}" for c in cands))
            if args.streamer.strip().isdigit():
                entry = {"mid": int(args.streamer), "name": f"uid{args.streamer}", "aliases": [], "sessions": []}
            else:
                hits = search_users(bili, args.streamer)
                if not hits:
                    die(f"登记表里没有「{args.streamer}」，B 站也搜不到同名用户。可以直接给 uid 或回放系列链接。")
                print(f"登记表里没有「{args.streamer}」，B 站搜到这些用户，确认后用 uid 重跑 latest，"
                      f"或用 streamers --set-list 绑定回放系列：")
                for h in hits[:8]:
                    print(f"  {h['mid']:<12} {h['uname']:<20} 粉丝 {h['fans']}  {h.get('usign', '')[:30]}")
                return

    lst = entry.get("replay_list")
    if lst:
        videos = fetch_list_latest(bili, lst, args.show)
        chosen = videos[0] if videos else None
        print(f"{entry['name']} (uid {entry['mid']}) 回放列表《{lst['title']}》最新：")
    else:
        videos = fetch_space_videos(bili, entry["mid"], 30)
        chosen = pick_latest_replay(videos, None)
        print(f"{entry['name']} (uid {entry['mid']}) 没有绑定回放列表，按标题关键词从最近投稿里猜：")
    if not videos:
        die(f"{entry['name']} 最近没有投稿")
    clipped = {s.get("bvid") for s in entry.get("sessions", [])}
    for v in videos[:args.show]:
        mark = "★" if chosen and v["bvid"] == chosen["bvid"] else " "
        when = dt.datetime.fromtimestamp(v["created"]).strftime("%m-%d %H:%M") if v.get("created") else "?"
        done = " (已切过)" if v["bvid"] in clipped else ""
        print(f" {mark} {v['bvid']} {when} {str(v.get('length', '')):>9}  {v['title'][:44]}{done}")
    if not chosen:
        die("最近投稿里没有像直播回放的视频。用 streamers --set-list 绑定回放系列链接后再试。")
    if chosen["bvid"] in clipped:
        print(f"\n最新一场 {chosen['bvid']} 上次已经切过（工作目录 {entry.get('last_session')}）。")
        if not args.force:
            print("没有更新的回放。要重新处理这一场加 --force。")
            return
    if args.list:
        return
    print()
    session, meta = run_parts(bili, f"https://www.bilibili.com/video/{chosen['bvid']}", args.season)
    print_parts(session, meta, {"bvid": chosen["bvid"]}, args.season)


def search_users(bili: Bili, keyword: str) -> list[dict]:
    try:
        d = bili.wbi_json("https://api.bilibili.com/x/web-interface/wbi/search/type",
                          {"search_type": "bili_user", "keyword": keyword, "page": 1})
        return (d.get("data") or {}).get("result") or []
    except Exception as e:  # noqa: BLE001
        info(f"搜索用户失败: {e}")
        return []


# ---------------------------------------------------------------- cmd: analyze

def normalize_danmaku_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)  # 哈哈哈哈哈 -> 哈哈
    text = re.sub(r"[\s!！?？。.,，~～…]+", "", text)
    return text[:12]


def bucket_danmaku(dms: list[dict], duration: float, bucket: int = BUCKET_SECONDS) -> list[dict]:
    n = int(duration // bucket) + 1
    buckets = [{"start": i * bucket, "count": 0, "texts": Counter()} for i in range(n)]
    for d in dms:
        idx = int(d["t"] // bucket)
        if 0 <= idx < n:
            buckets[idx]["count"] += 1
            norm = normalize_danmaku_text(d.get("text", ""))
            if norm:
                buckets[idx]["texts"][norm] += 1
    out = []
    for b in buckets:
        out.append({"start": b["start"], "count": b["count"],
                    "top": [t for t, _ in b["texts"].most_common(3)]})
    return out


def is_filler(text: str) -> bool:
    t = text.replace(MUSIC_MARK, "").strip()
    return len(t) <= 1 or bool(FILLER_RE.fullmatch(t))


def compress_transcript(lines: list[dict], buckets: list[dict], duration: float,
                        bucket: int = BUCKET_SECONDS, pct_threshold: float | None = None) -> str:
    """按 bucket 秒合并字幕，去语气词，附弹幕密度。"""
    n = int(duration // bucket) + 1
    texts: list[list[str]] = [[] for _ in range(n)]
    for ln in lines:
        c = ln["content"].replace("\n", " ").strip()
        if not c or is_filler(c):
            continue
        idx = int(ln["from"] // bucket)
        if 0 <= idx < n:
            texts[idx].append(c)
    out = []
    for i in range(n):
        b = buckets[i] if i < len(buckets) else {"count": 0, "top": []}
        if not texts[i] and b["count"] == 0:
            continue
        marker = " 🔥" if pct_threshold is not None and b["count"] >= pct_threshold and b["count"] > 0 else ""
        tops = " ".join(b["top"]) if b["top"] else ""
        dm = f"弹幕{b['count']}{marker}" + (f" [{tops}]" if tops else "")
        body = " / ".join(texts[i]) if texts[i] else "(无对话)"
        out.append(f"[{fmt_ts(i * bucket)}] ({dm}) {body}")
    return "\n".join(out)


def write_srt(lines: list[dict], path: Path) -> None:
    chunks = []
    for i, ln in enumerate(lines, 1):
        chunks.append(f"{i}\n{fmt_ts(ln['from'], srt=True)} --> {fmt_ts(ln['to'], srt=True)}\n{ln['content']}\n")
    path.write_text("\n".join(chunks), "utf-8")


def percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return float(s[k])


def cmd_analyze(args: argparse.Namespace) -> None:
    session = resolve_session(args.session)
    meta = load_parts(session)
    parts = meta["parts"]
    try:
        selected = parse_selection(args.parts, len(parts))
    except ValueError as e:
        die(str(e))
    bili = ensure_cookies(args.browser)
    raw = session / "raw"
    summary = []
    for idx in selected:
        p = parts[idx - 1]
        tag = f"p{idx:02d}"
        info(f"== 分 P {idx}: {p['title']} ({fmt_ts(p['duration'])})")
        sub_json = raw / f"{tag}_subtitle.json"
        if sub_json.exists() and not args.refresh:
            lines = json.loads(sub_json.read_text("utf-8"))
        else:
            # 字幕链接有时效，且可能刚生成，分析时总是重新查一次列表
            chosen = pick_subtitle(fetch_subtitle_list(bili, p["bvid"], p["cid"]))
            if not chosen:
                p["subtitle"] = None
                summary.append({"index": idx, "status": "no_subtitle", "title": p["title"]})
                info("  没有 AI 字幕，跳过")
                continue
            p["subtitle"] = {"lan": chosen.get("lan"), "url": chosen.get("subtitle_url")}
            lines = fetch_subtitle_body(bili, chosen)
            sub_json.write_text(json.dumps(lines, ensure_ascii=False), "utf-8")
        write_srt(lines, raw / f"{tag}.srt")
        dm_file = raw / f"danmaku_{p['cid']}.json"
        if dm_file.exists() and not args.refresh:
            dms = json.loads(dm_file.read_text("utf-8"))
        else:
            dms = fetch_danmaku(bili, p["cid"], p["duration"])
            dm_file.write_text(json.dumps(dms, ensure_ascii=False), "utf-8")
        buckets = bucket_danmaku(dms, p["duration"])
        counts = [b["count"] for b in buckets]
        p95 = percentile(counts, 0.95)
        compact = compress_transcript(lines, buckets, p["duration"], pct_threshold=p95)
        header = (f"# 分P {idx} | {p['title']} | {p['bvid']} p{p['page']} | 时长 {fmt_ts(p['duration'])} | "
                  f"字幕 {len(lines)} 条 | 弹幕 {len(dms)} 条 | 每 {BUCKET_SECONDS}s 一块，🔥=弹幕密度本P前5%\n")
        (raw / f"{tag}_compact.txt").write_text(header + compact, "utf-8")
        (raw / f"{tag}_danmaku_buckets.json").write_text(json.dumps(buckets, ensure_ascii=False), "utf-8")
        peaks = sorted(((b["count"], b["start"], b["top"]) for b in buckets), reverse=True)[:8]
        summary.append({
            "index": idx, "status": "ok", "title": p["title"], "bvid": p["bvid"], "page": p["page"],
            "duration": p["duration"], "subtitle_lines": len(lines), "danmaku": len(dms),
            "compact_file": str(raw / f"{tag}_compact.txt"), "srt_file": str(raw / f"{tag}.srt"),
            "compact_chars": len(compact),
            "danmaku_peaks": [{"at": fmt_ts(s), "count": c, "top": t} for c, s, t in peaks if c > 0],
        })
    meta["parts"] = parts
    (raw / "parts.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"session": str(session), "owner": meta["owner"], "parts": summary}, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- cmd: find

def find_in_session(session: Path, keywords: list[str], parts_sel: list[int] | None,
                    context: int = 45, merge_gap: int = 90) -> list[dict]:
    """在字幕和弹幕里搜关键词，按 P 聚成命中区间。"""
    meta = load_parts(session)
    raw = session / "raw"
    kws = [k.lower() for k in keywords if k.strip()]
    hits: list[dict] = []
    for p in meta["parts"]:
        if parts_sel and p["index"] not in parts_sel:
            continue
        idx = p["index"]
        events: list[tuple[float, str, str]] = []
        sub_file = raw / f"p{idx:02d}_subtitle.json"
        if sub_file.exists():
            for ln in json.loads(sub_file.read_text("utf-8")):
                text = ln["content"]
                if any(k in text.lower() for k in kws):
                    events.append((float(ln["from"]), "字幕", text))
        dm_file = raw / f"danmaku_{p['cid']}.json"
        if dm_file.exists():
            for d in json.loads(dm_file.read_text("utf-8")):
                if any(k in d.get("text", "").lower() for k in kws):
                    events.append((float(d["t"]), "弹幕", d["text"]))
        events.sort()
        cluster: list[tuple[float, str, str]] = []
        for ev in events:
            if cluster and ev[0] - cluster[-1][0] > merge_gap:
                hits.append(_cluster_hit(p, cluster, context))
                cluster = []
            cluster.append(ev)
        if cluster:
            hits.append(_cluster_hit(p, cluster, context))
    hits.sort(key=lambda h: (-h["score"], h["part"], h["start"]))
    return hits


def _cluster_hit(p: dict, cluster: list, context: int) -> dict:
    first, last = cluster[0][0], cluster[-1][0]
    subs = [c for c in cluster if c[1] == "字幕"]
    dms = [c for c in cluster if c[1] == "弹幕"]
    return {
        "part": p["index"], "bvid": p["bvid"], "page": p["page"], "title": p["title"],
        "start": max(0.0, first - context), "end": min(float(p["duration"]), last + context),
        "first_hit": first, "last_hit": last,
        "subtitle_hits": len(subs), "danmaku_hits": len(dms),
        "score": len(subs) * 3 + len(dms),
        "samples": [f"[{fmt_ts(t)}] {kind}: {text[:40]}" for t, kind, text in cluster[:6]],
    }


def cmd_find(args: argparse.Namespace) -> None:
    session = resolve_session(args.session)
    meta = load_parts(session)
    sel = parse_selection(args.parts, len(meta["parts"])) if args.parts else None
    hits = find_in_session(session, args.keywords, sel, context=args.context)
    if not hits:
        print("没有命中。换个写法试试（ASR 常把人名写成同音字），或用 analyze 先拉取该 P 的字幕。")
        return
    print(f"关键词 {args.keywords} 命中 {len(hits)} 处（按字幕 3 分 / 弹幕 1 分排序）：\n")
    for i, h in enumerate(hits[: args.limit], 1):
        print(f"{i}. P{h['part']} {fmt_ts(h['first_hit'])} → {fmt_ts(h['last_hit'])}  "
              f"字幕 {h['subtitle_hits']} 条 / 弹幕 {h['danmaku_hits']} 条  建议区间 {fmt_ts(h['start'])}–{fmt_ts(h['end'])}")
        for smp in h["samples"]:
            print(f"     {smp}")
    if args.json:
        print(json.dumps(hits[: args.limit], ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- candidates

REQUIRED_FIELDS = ("id", "part", "start", "end", "type", "title", "reason")


def validate_candidates(data: dict, parts: list[dict]) -> list[str]:
    errs = []
    seen = set()
    for c in data.get("candidates") or []:
        for f in REQUIRED_FIELDS:
            if f not in c:
                errs.append(f"候选 {c.get('id')} 缺少字段 {f}")
        cid = c.get("id")
        if cid in seen:
            errs.append(f"候选 id 重复: {cid}")
        seen.add(cid)
        try:
            pi = int(c["part"])
            p = parts[pi - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            errs.append(f"候选 {cid} 的 part 无效: {c.get('part')}")
            continue
        try:
            s, e = parse_ts(c["start"]), parse_ts(c["end"])
        except (ValueError, KeyError) as ex:
            errs.append(f"候选 {cid} 时间无法解析: {ex}")
            continue
        if not 0 <= s < e <= p["duration"] + 1:
            errs.append(f"候选 {cid} 时间越界: {c['start']}-{c['end']} (分P时长 {fmt_ts(p['duration'])})")
    return errs


def normalize_candidates(data: dict, parts: list[dict]) -> None:
    for c in data.get("candidates") or []:
        p = parts[int(c["part"]) - 1]
        c["start"] = round(parse_ts(c["start"]), 2)
        c["end"] = round(parse_ts(c["end"]), 2)
        if c.get("cover_at") is not None:
            c["cover_at"] = round(parse_ts(c["cover_at"]), 2)
        c["bvid"], c["cid"], c["page"] = p["bvid"], p["cid"], p["page"]
        c.setdefault("status", "proposed")


def danmaku_stats_for(session: Path, c: dict, parts: list[dict]) -> dict:
    """片段内的弹幕总数、峰值秒、在本 P 中的密度分位。"""
    p = parts[int(c["part"]) - 1]
    f = session / "raw" / f"danmaku_{p['cid']}.json"
    if not f.exists():
        return {}
    dms = json.loads(f.read_text("utf-8"))
    inside = [d for d in dms if c["start"] <= d["t"] <= c["end"]]
    per_sec = Counter(int(d["t"]) for d in inside)
    peak_sec = per_sec.most_common(1)[0][0] if per_sec else int((c["start"] + c["end"]) / 2)
    buckets = bucket_danmaku(dms, p["duration"])
    counts = sorted(b["count"] for b in buckets)
    seg_len = max(1.0, c["end"] - c["start"])
    rate = len(inside) / seg_len * BUCKET_SECONDS
    rank = sum(1 for x in counts if x <= rate) / max(1, len(counts))
    return {"danmaku_in_clip": len(inside), "danmaku_peak_at": peak_sec,
            "danmaku_percentile": round(rank * 100)}


# ---------------------------------------------------------------- cmd: preview

PREVIEW_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c1e21;--muted:#65676b;--accent:#fb7299;--line:#e4e6eb}
@media(prefers-color-scheme:dark){:root{--bg:#18191a;--card:#242526;--fg:#e4e6eb;--muted:#b0b3b8;--line:#3a3b3c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--line);background:var(--card)}header h1{margin:0 0 4px;font-size:20px}header p{margin:0;color:var(--muted)}
main{display:flex;flex-direction:column;gap:20px;padding:24px 28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:row;align-items:flex-start}
.card .player{flex:1 1 60%;min-width:0;position:relative;aspect-ratio:16/9;background:#000}.card iframe,.card video{position:absolute;inset:0;width:100%;height:100%;border:0}
.card .body{flex:1 1 40%;min-width:280px}
@media(max-width:860px){.card{flex-direction:column}.card .player,.card .body{flex:none;width:100%}}
.caption-overlay{position:absolute;left:0;right:0;bottom:6%;text-align:center;padding:0 5%;pointer-events:none;z-index:2}
.caption-overlay span{display:inline-block;background:rgba(0,0,0,.65);color:#fff;padding:4px 12px;border-radius:6px;font-size:clamp(13px,2.4vw,22px);line-height:1.4;white-space:pre-wrap}
.card:fullscreen .caption-overlay,.card:-webkit-full-screen .caption-overlay{right:min(360px,38vw)}
.card .body{padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:var(--accent);color:#fff}
.id{font-weight:700;font-size:16px}.time{font-variant-numeric:tabular-nums;color:var(--muted)}
.title{font-size:15px;font-weight:600}.reason{color:var(--fg)}.quote{color:var(--muted);border-left:3px solid var(--line);padding-left:10px;white-space:pre-wrap}
.meta{color:var(--muted);font-size:12px}a{color:var(--accent)}
.jump{display:flex;gap:8px;flex-wrap:wrap}.jump button{cursor:pointer;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:8px;padding:6px 12px;font:inherit;font-size:13px}
.jump button:hover{border-color:var(--accent);color:var(--accent)}
.trim{position:relative;display:flex;flex-direction:column;gap:6px;border-top:1px dashed var(--line);padding-top:8px;margin-top:2px}
.playpause{position:absolute;top:8px;right:0;cursor:pointer;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:8px;padding:4px 10px;font:inherit;font-size:12px}
.playpause:hover{border-color:var(--accent);color:var(--accent)}
.trim-row{display:flex;align-items:center;justify-content:center;gap:8px;color:var(--fg);font-size:13px;font-weight:600}
.trim-row .tval{font-variant-numeric:tabular-nums}
.trim-row .sep{color:var(--muted);font-weight:400}
.range-wrap{position:relative;height:24px}
.range-track{position:absolute;left:0;right:0;top:50%;height:4px;border-radius:2px;background:var(--line);transform:translateY(-50%)}
.range-fill{position:absolute;top:50%;height:4px;border-radius:2px;background:var(--accent);transform:translateY(-50%)}
.range-wrap input[type=range]{-webkit-appearance:none;appearance:none;position:absolute;left:0;top:0;width:100%;height:24px;margin:0;background:transparent;pointer-events:none}
.range-wrap input[type=range]::-webkit-slider-runnable-track{background:transparent;height:24px}
.range-wrap input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;pointer-events:auto;width:16px;height:16px;margin-top:4px;border-radius:50%;background:var(--accent);border:2px solid var(--card);box-shadow:0 0 0 1px var(--accent);cursor:pointer}
.range-wrap input[type=range]::-moz-range-track{background:transparent;height:24px;border:none}
.range-wrap input[type=range]::-moz-range-thumb{pointer-events:auto;width:16px;height:16px;border-radius:50%;background:var(--accent);border:2px solid var(--card);box-shadow:0 0 0 1px var(--accent);cursor:pointer}
.trim .jump button{font-size:12px;padding:4px 10px}
header .copyall{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:6px 12px;font:inherit;font-size:13px;margin-top:8px}
header .copyall:hover{border-color:var(--accent);color:var(--accent)}
.fsbtn{cursor:pointer;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:8px;padding:4px 10px;font:inherit;font-size:12px;margin-left:auto}
.fsbtn:hover{border-color:var(--accent);color:var(--accent)}
.cover{display:flex;flex-direction:column;gap:6px;border-top:1px dashed var(--line);padding-top:8px;margin-top:2px}
.cover-title{color:var(--muted);font-size:12px}
.coverimg{width:100%;border-radius:8px;display:block;background:#000}
.subs-title{color:var(--muted);font-size:12px}
.subs{max-height:180px;overflow-y:auto;border:1px solid var(--line);border-radius:8px;padding:4px}
.sub-line{padding:2px 6px;border-radius:4px;cursor:pointer;font-size:13px}
.sub-line:hover{background:var(--bg)}
.sub-line.out{color:var(--muted)}
.sub-line.in{color:var(--fg);font-weight:600}
.sub-line.current{background:var(--accent);color:#fff}
.card:fullscreen .subs,.card:-webkit-full-screen .subs{border-color:rgba(255,255,255,.25)}
.card:fullscreen .sub-line.out,.card:-webkit-full-screen .sub-line.out{color:#8a8d91}
.card:fullscreen .sub-line.in,.card:-webkit-full-screen .sub-line.in{color:#fff}
.card:fullscreen .sub-line:hover,.card:-webkit-full-screen .sub-line:hover{background:rgba(255,255,255,.12)}
.card:fullscreen,.card:-webkit-full-screen{width:100vw;height:100vh;background:#000;position:relative;flex-direction:column;align-items:stretch}
.card:fullscreen .player,.card:-webkit-full-screen .player{flex:1;aspect-ratio:auto}
.card:fullscreen .body,.card:-webkit-full-screen .body{position:absolute;top:0;right:0;bottom:0;width:360px;max-width:38vw;overflow:auto;
  background:rgba(20,20,22,.82);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);color:#e4e6eb}
.card:fullscreen .title,.card:-webkit-full-screen .title{color:#fff}
.card:fullscreen .reason,.card:fullscreen .quote,.card:fullscreen .meta,.card:fullscreen .time,.card:fullscreen .trim-row .sep,.card:fullscreen .cover-title,
.card:-webkit-full-screen .reason,.card:-webkit-full-screen .quote,.card:-webkit-full-screen .meta,.card:-webkit-full-screen .time,.card:-webkit-full-screen .trim-row .sep,.card:-webkit-full-screen .cover-title{color:#b0b3b8}
.card:fullscreen .jump button,.card:-webkit-full-screen .jump button,.card:fullscreen .fsbtn,.card:-webkit-full-screen .fsbtn,
.card:fullscreen .playpause,.card:-webkit-full-screen .playpause{background:rgba(255,255,255,.08);color:#e4e6eb;border-color:rgba(255,255,255,.25)}
"""

PREVIEW_JS = """
function fmtTime(sec){
  sec=Math.max(0,Math.round(Number(sec)||0));
  var h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;
  function p2(n){return String(n).padStart(2,'0');}
  return p2(h)+':'+p2(m)+':'+p2(s);
}
function applySeek(card, sec, play){
  var v=card.querySelector('video'), f=card.querySelector('iframe');
  if(v){v.currentTime=sec-parseFloat(v.dataset.base||'0'); if(play){v.play();} return;}
  if(f){f.src=f.dataset.embed+'&t='+Math.floor(sec)+'&autoplay='+(play?1:0);}
}
function jump(id, sec){ applySeek(document.getElementById('c'+id), sec, false); }
function jumpPlay(id, sec){ applySeek(document.getElementById('c'+id), sec, true); }
function toggleFullscreen(id){
  var el=document.getElementById('c'+id);
  var fsEl=document.fullscreenElement||document.webkitFullscreenElement;
  if(fsEl===el){ (document.exitFullscreen||document.webkitExitFullscreen).call(document); }
  else { (el.requestFullscreen||el.webkitRequestFullscreen).call(el); }
}
function syncFsButtons(){
  var fsEl=document.fullscreenElement||document.webkitFullscreenElement;
  document.querySelectorAll('.fsbtn').forEach(function(btn){
    btn.textContent = (fsEl===btn.closest('.card')) ? '✕ 退出全屏' : '⛶ 全屏';
  });
}
document.addEventListener('fullscreenchange', syncFsButtons);
document.addEventListener('webkitfullscreenchange', syncFsButtons);
function seekPreview(card, sec){
  // 拖动滑块连续触发：本地 video 直接改 currentTime 很轻，可以实时跟；
  // 内嵌播放器每次都要整个重新加载 iframe，节流到松手附近再刷，避免拖动卡顿
  if(card.querySelector('video')){ applySeek(card, sec); return; }
  clearTimeout(card._seekTimer);
  card._seekTimer=setTimeout(function(){ applySeek(card, sec); }, 250);
}
function copyText(text){
  function fallback(){
    var ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');}catch(e){}
    document.body.removeChild(ta);
  }
  if(navigator.clipboard && navigator.clipboard.writeText){navigator.clipboard.writeText(text).catch(fallback);}
  else{fallback();}
}
function flash(btn, msg){var old=btn.textContent;btn.textContent=msg;setTimeout(function(){btn.textContent=old;},1200);}
function copyAllTrims(){
  var lines=[];
  document.querySelectorAll('.card').forEach(function(card){
    var id=card.id.replace('c','');
    var sStart=card.querySelector('[data-role=start]'), sEnd=card.querySelector('[data-role=end]');
    if(!sStart||!sEnd) return;
    lines.push('#'+id+' '+fmtTime(sStart.value)+' → '+fmtTime(sEnd.value));
  });
  copyText(lines.join('\\n'));
}
document.addEventListener('DOMContentLoaded', function(){
  document.querySelectorAll('.trim').forEach(function(trim){
    var card=trim.closest('.card');
    var sStart=trim.querySelector('[data-role=start]'), sEnd=trim.querySelector('[data-role=end]');
    var lStart=trim.querySelector('[data-role=start-val]'), lEnd=trim.querySelector('[data-role=end-val]');
    var origStart=parseFloat(sStart.dataset.orig), origEnd=parseFloat(sEnd.dataset.orig);
    var fill=trim.querySelector('[data-role=fill]');
    var lo=parseFloat(sStart.min), hi=parseFloat(sStart.max), span=(hi-lo)||1;
    function sync(){
      if(parseFloat(sStart.value)>=parseFloat(sEnd.value)){
        sStart.value=Math.max(parseFloat(sStart.min), parseFloat(sEnd.value)-1);
      }
      lStart.textContent=fmtTime(sStart.value);
      lEnd.textContent=fmtTime(sEnd.value);
      var a=(parseFloat(sStart.value)-lo)/span*100, b=(parseFloat(sEnd.value)-lo)/span*100;
      fill.style.left=a+'%'; fill.style.width=Math.max(0,b-a)+'%';
    }
    sStart.addEventListener('input', function(){ sync(); seekPreview(card, parseFloat(sStart.value)); });
    sEnd.addEventListener('input', function(){ sync(); seekPreview(card, parseFloat(sEnd.value)); });
    sStart.addEventListener('pointerdown', function(){ sStart.style.zIndex=5; sEnd.style.zIndex=4; });
    sEnd.addEventListener('pointerdown', function(){ sEnd.style.zIndex=5; sStart.style.zIndex=4; });
    sync();
    var resetBtn=trim.querySelector('[data-act=reset]');
    if(resetBtn) resetBtn.addEventListener('click', function(){ sStart.value=origStart; sEnd.value=origEnd; sync(); });
    var copyBtn=trim.querySelector('[data-act=copy]');
    if(copyBtn) copyBtn.addEventListener('click', function(){
      copyText('#'+card.id.replace('c','')+' start='+fmtTime(sStart.value)+' end='+fmtTime(sEnd.value));
      flash(copyBtn, '已复制');
    });
    var v=card.querySelector('video'), ppBtn=trim.querySelector('[data-act=playpause]');
    if(v && ppBtn){
      var syncPP=function(){ ppBtn.textContent = v.paused ? '▶ 播放' : '⏸ 暂停'; };
      ppBtn.addEventListener('click', function(){ v.paused ? v.play() : v.pause(); });
      v.addEventListener('click', function(){ v.paused ? v.play() : v.pause(); });
      v.addEventListener('play', syncPP);
      v.addEventListener('pause', syncPP);
      syncPP();
    }
    var coverImg=card.querySelector('[data-role=coverimg]'), setCoverBtn=card.querySelector('[data-act=setcover]');
    if(v && coverImg && setCoverBtn){
      setCoverBtn.addEventListener('click', function(){
        var cv=document.createElement('canvas');
        cv.width=v.videoWidth; cv.height=v.videoHeight;
        cv.getContext('2d').drawImage(v, 0, 0, cv.width, cv.height);
        coverImg.src=cv.toDataURL('image/jpeg', 0.85);
        var abs=v.currentTime+parseFloat(v.dataset.base||'0');
        copyText('#'+card.id.replace('c','')+' cover='+fmtTime(abs));
        flash(setCoverBtn, '已复制');
      });
    }
  });
  var copyAllBtn=document.getElementById('copyAllTrims');
  if(copyAllBtn) copyAllBtn.addEventListener('click', function(){ copyAllTrims(); flash(copyAllBtn, '已复制全部'); });
  document.querySelectorAll('.card').forEach(function(card){
    var v=card.querySelector('video');
    var subLines=card.querySelectorAll('.sub-line');
    var caption=card.querySelector('[data-role=caption]');
    if(!v || !subLines.length) return;
    v.addEventListener('timeupdate', function(){
      var abs=v.currentTime+parseFloat(v.dataset.base||'0'), active=null;
      subLines.forEach(function(el){
        var t0=parseFloat(el.dataset.t), t1=parseFloat(el.dataset.t2);
        if(abs>=t0 && abs<t1) active=el;
      });
      subLines.forEach(function(el){ el.classList.toggle('current', el===active); });
      if(active) active.scrollIntoView({block:'nearest', behavior:'smooth'});
      if(caption) caption.innerHTML = active ? '<span>'+active.textContent.replace(/^\\[[\\d:]+\\]\\s*/,'')+'</span>' : '';
    });
  });
});
"""
PREVIEW_TAIL_LEAD = 8  # 「定位结尾」跳到终点前几秒，好听到收尾那句话


def load_subtitle_lines(session: Path, idx: int) -> list[dict] | None:
    f = session / "raw" / f"p{idx:02d}_subtitle.json"
    if not f.exists():
        return None
    return json.loads(f.read_text("utf-8"))


def build_preview_html(meta: dict, data: dict, local_files: dict[str, str] | None,
                       session: Path | None = None, cover_files: dict[str, str] | None = None) -> str:
    cards = []
    for c in data["candidates"]:
        cid = html.escape(str(c["id"]))
        start, end = float(c["start"]), float(c["end"])
        length = end - start
        url = f"https://www.bilibili.com/video/{c['bvid']}?p={c['page']}&t={int(start)}"
        tail = max(start, end - PREVIEW_TAIL_LEAD)
        url_end = f"https://www.bilibili.com/video/{c['bvid']}?p={c['page']}&t={int(tail)}"
        if local_files and str(c["id"]) in local_files:
            # 本地预览段本身就是从 start 切出来的，时间轴从 0 开始，data-base 记录偏移
            player = (f'<video controls preload="metadata" data-base="{start:.3f}" '
                      f'src="{html.escape(local_files[str(c["id"])])}"></video>')
        else:
            embed = (f"https://player.bilibili.com/player.html?isOutside=true&bvid={c['bvid']}&cid={c['cid']}"
                     f"&p={c['page']}&high_quality=1&danmaku=1")
            player = (f'<iframe src="{embed}&t={int(start)}&autoplay=0" data-embed="{embed}" allowfullscreen scrolling="no" '
                      f'sandbox="allow-scripts allow-same-origin allow-forms"></iframe>')
        stats = c.get("stats") or {}
        pct = stats.get("danmaku_percentile")
        pct_txt = f"弹幕密度本P前 {100 - pct}%" if pct is not None else ""
        quote = html.escape(c.get("quote") or "")
        part_duration = float(meta["parts"][int(c["part"]) - 1]["duration"])
        is_local = bool(local_files and str(c["id"]) in local_files)
        if is_local:
            win_min, win_max = start, end
        else:
            buf = 120.0
            win_min = max(0.0, start - buf)
            win_max = min(part_duration, end + buf)
        if win_max <= win_min:
            win_max = win_min + 1.0
        playpause_html = '<button type="button" class="playpause" data-act="playpause">⏸ 暂停</button>' if is_local else ""
        trim = f"""
    <div class="trim">
      {playpause_html}
      <div class="trim-row"><span class="tval" data-role="start-val">{fmt_ts(start)}</span><span class="sep">→</span><span class="tval" data-role="end-val">{fmt_ts(end)}</span></div>
      <div class="range-wrap">
        <div class="range-track"></div>
        <div class="range-fill" data-role="fill"></div>
        <input type="range" data-role="start" min="{win_min:.1f}" max="{win_max:.1f}" step="1" value="{start:.1f}" data-orig="{start:.1f}">
        <input type="range" data-role="end" min="{win_min:.1f}" max="{win_max:.1f}" step="1" value="{end:.1f}" data-orig="{end:.1f}">
      </div>
      <div class="jump"><button type="button" data-act="reset">↺ 重置</button>
        <button type="button" data-act="copy">📋 复制新时间</button></div>
    </div>"""
        cover_html = ""
        if cover_files and str(c["id"]) in cover_files:
            cover_html = f"""
    <div class="cover">
      <div class="cover-title">封面预览</div>
      <img class="coverimg" data-role="coverimg" src="{html.escape(cover_files[str(c['id'])])}">
      <div class="jump"><button type="button" data-act="setcover">🖼 用当前帧做封面 + 复制时间点</button></div>
    </div>"""
        subs_html = ""
        if session is not None:
            sub_lines = load_subtitle_lines(session, int(c["part"]))
            if sub_lines:
                items = []
                for ln in sub_lines:
                    t0 = float(ln.get("from", 0))
                    if not (win_min - 1 <= t0 <= win_max + 1):
                        continue
                    t1 = float(ln.get("to", t0))
                    text = html.escape(str(ln.get("content", "")))
                    if not text:
                        continue
                    cls = "in" if start <= t0 <= end else "out"
                    items.append(f'<div class="sub-line {cls}" data-t="{t0:.2f}" data-t2="{t1:.2f}" '
                                 f'onclick="jumpPlay(\'{cid}\',{t0:.3f})">[{fmt_ts(t0)}] {text}</div>')
                if items:
                    subs_html = (f'<div class="subs-title">AI 字幕（深色为片段内，点击跳转）</div>'
                                 f'<div class="subs" data-role="subs">{"".join(items)}</div>')
        cards.append(f"""
<section class="card" id="c{cid}">
  <div class="player">{player}<div class="caption-overlay" data-role="caption"></div></div>
  <div class="body">
    <div class="row"><span class="id">#{cid}</span><span class="badge">{html.escape(str(c.get('type','')))}</span>
      <span class="time">P{c['part']} · {fmt_ts(start)} → {fmt_ts(end)} · {int(length // 60)}分{int(length % 60)}秒</span>
      <button type="button" class="fsbtn" onclick="toggleFullscreen('{cid}')">⛶ 全屏</button></div>
    <div class="title">{html.escape(str(c.get('title','')))}</div>
    <div class="reason">{html.escape(str(c.get('reason','')))}</div>
    {f'<div class="quote">{quote}</div>' if quote else ''}
    <div class="jump"><button type="button" onclick="jump('{cid}',{start:.3f})">⏮ 定位开头 {fmt_ts(start)}</button>
      <button type="button" onclick="jump('{cid}',{tail:.3f})">⏭ 定位结尾 {fmt_ts(tail)}</button></div>
    {trim}
    {cover_html}
    {subs_html}
    <div class="meta">{pct_txt}{' · ' if pct_txt else ''}在 B 站打开：<a href="{url}" target="_blank">起点</a> / <a href="{url_end}" target="_blank">结尾前 {PREVIEW_TAIL_LEAD} 秒</a> · 播放器到 {fmt_ts(end)} 不会自动停，请手动停</div>
  </div>
</section>""")
    title = html.escape(meta["title"])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>切片预览 · {title}</title><style>{PREVIEW_CSS}</style><script>{PREVIEW_JS}</script></head><body>
<header><h1>{title}</h1><p>主播 {html.escape(meta['owner']['name'])} · {len(data['candidates'])} 个候选 · 生成于 {dt.datetime.now():%Y-%m-%d %H:%M}。拖滑块微调起止，边拖边预览；改完点「复制新时间」把结果贴回对话，我会更新 candidates.json。</p>
<button type="button" class="copyall" id="copyAllTrims">📋 复制全部当前时间</button></header>
<main>{''.join(cards)}</main></body></html>"""


def ytdlp_download(bvid: str, page: int, out: Path, fmt: str) -> None:
    ytdlp = need_tool("yt-dlp", "请先安装 yt-dlp")
    ffmpeg = find_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    cmd = [ytdlp, "--no-update", "--cookies", COOKIES_FILE, "--no-playlist", "-f", fmt,
           "--ffmpeg-location", str(Path(ffmpeg).parent),
           "--merge-output-format", "mp4", "-o", str(tmp), "--no-part", "--no-mtime",
           f"https://www.bilibili.com/video/{bvid}?p={page}"]
    r = run(cmd)
    if r.returncode != 0 or not tmp.exists():
        die(f"下载失败: {bvid} p{page}")
    tmp.rename(out)


def cached_video(session: Path, p: dict, quality: str) -> Path:
    fmt = {"best": "bv*+ba/b", "low": "bv*[height<=480]+ba/b[height<=480]/b"}[quality]
    out = session / "cache" / f"p{p['index']:02d}_{p['cid']}_{quality}.mp4"
    if not out.exists():
        info(f"下载分 P {p['index']} ({quality}) ...")
        ytdlp_download(p["bvid"], p["page"], out, fmt)
    return out


# ---------------------------------------------------------------- danmaku -> ass

def ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(int(sec), 3600)
    m, s_ = divmod(rem, 60)
    return f"{h}:{m:02d}:{s_:02d}.{int(round((sec - int(sec)) * 100)) % 100:02d}"


def ass_escape(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def text_width(text: str, fontsize: float) -> float:
    w = 0.0
    for ch in text:
        o = ord(ch)
        w += fontsize if (o >= 0x2E80 or 0xFF01 <= o <= 0xFF60) else fontsize * 0.56
    return w


def layout_danmaku(dms: list[dict], start: float, end: float, width: int, height: int,
                   fontsize: float | None = None, area: float = DANMAKU_AREA,
                   scroll_secs: float = DANMAKU_SCROLL_SECONDS, fixed_secs: float = DANMAKU_FIXED_SECONDS) -> list[dict]:
    """把片段内的弹幕排进轨道。返回按时间排序的事件，时间相对片段起点。

    滚动弹幕的碰撞规则：同一轨道上后一条出现时，前一条要已完全进入画面；
    后一条到达左边缘时，前一条要已完全离开。找不到满足的轨道时挑最早空出来的那条，允许轻微重叠。
    """
    fontsize = fontsize or max(16.0, DANMAKU_FONT_SIZE * height / 1080)
    row_h = fontsize * 1.25
    rows = max(1, int(height * area // row_h))
    scroll_rows: list[tuple[float, float, float] | None] = [None] * rows  # (t, len, speed)
    top_rows = [-1e9] * rows
    bottom_rows = [-1e9] * rows
    events = []
    for d in sorted(dms, key=lambda x: x["t"]):
        t = float(d["t"])
        if not (start <= t < end):
            continue
        text = ass_escape(str(d.get("text", "")))
        if not text:
            continue
        mode = int(d.get("mode", 1))
        rel = t - start
        if mode in (4, 5):
            table = bottom_rows if mode == 4 else top_rows
            row = next((i for i, free in enumerate(table) if free <= t), None)
            if row is None:
                row = min(range(rows), key=lambda i: table[i])
            table[row] = t + fixed_secs
            y = int(row * row_h + 2) if mode == 5 else int(height - row * row_h - 2)
            tag = f"{{\\an8\\pos({width // 2},{y})}}" if mode == 5 else f"{{\\an2\\pos({width // 2},{y})}}"
            events.append({"start": rel, "end": rel + fixed_secs, "text": tag + text})
            continue
        if mode not in (1, 2, 3, 6):
            continue
        length = text_width(text, fontsize)
        speed = (width + length) / scroll_secs
        best, best_need = None, None
        for i, prev in enumerate(scroll_rows):
            if prev is None:
                best = i
                break
            pt, plen, pspeed = prev
            need = max(pt + plen / pspeed, pt + scroll_secs - width / speed)
            if need <= t:
                best = i
                break
            if best_need is None or need < best_need:
                best, best_need = i, need
        scroll_rows[best] = (t, length, speed)
        y = int(best * row_h + 2)
        tag = f"{{\\move({width},{y},{-int(length)},{y})}}"
        events.append({"start": rel, "end": rel + scroll_secs, "text": tag + text})
    return events


def build_ass(events: list[dict], width: int, height: int, fontsize: float | None = None,
              opacity: float = DANMAKU_OPACITY, font: str = DANMAKU_FONT) -> str:
    fontsize = fontsize or max(16.0, DANMAKU_FONT_SIZE * height / 1080)
    alpha = f"{int(round((1 - max(0.0, min(1.0, opacity))) * 255)):02X}"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Danmaku,{font},{int(round(fontsize))},&H{alpha}FFFFFF,&H{alpha}FFFFFF,&H{alpha}000000,&H{alpha}000000,-1,0,0,0,100,100,0,0,1,1.2,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ass_time(e['start'])},{ass_time(e['end'])},Danmaku,,0,0,0,,{e['text']}" for e in events]
    return head + "\n".join(lines) + ("\n" if lines else "")


def load_danmaku(session: Path, p: dict) -> list[dict] | None:
    f = session / "raw" / f"danmaku_{p['cid']}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text("utf-8"))


def write_danmaku_ass(dms: list[dict], start: float, end: float, width: int, height: int, out: Path,
                      opacity: float = DANMAKU_OPACITY, area: float = DANMAKU_AREA,
                      fontsize: float | None = None) -> int:
    events = layout_danmaku(dms, start, end, width, height, fontsize=fontsize, area=area)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_ass(events, width, height, fontsize=fontsize, opacity=opacity), "utf-8")
    return len(events)


def ffmpeg_cut(src: Path, start: float, end: float, out: Path, fast: bool, ass: Path | None = None) -> None:
    if fast and ass:
        die("--fast 走流复制，不能同时烧弹幕")
    ffmpeg = find_ffmpeg(need_libass=ass is not None)
    out.parent.mkdir(parents=True, exist_ok=True)
    base = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src]
    cwd = None
    if fast:
        cmd = base + ["-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out]
    else:
        vf = []
        if ass:
            # 用相对文件名 + cwd，避开 ffmpeg 滤镜参数对中文 / 冒号 / 空格路径的转义问题
            vf = ["-vf", f"ass={ass.name}"]
            cwd = str(ass.parent)
        cmd = base + vf + ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    r = run(cmd, cwd=cwd)
    if r.returncode != 0 or not out.exists():
        die(f"ffmpeg 切片失败: {out}")


def ffmpeg_frame(src: Path, at: float, out: Path) -> None:
    ffmpeg = find_ffmpeg()
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", src,
         "-frames:v", "1", "-q:v", "2", out])


def cmd_preview(args: argparse.Namespace) -> None:
    session = resolve_session(args.session)
    meta = load_parts(session)
    data = load_candidates(session)
    errs = validate_candidates(data, meta["parts"])
    if errs:
        die("candidates.json 校验失败:\n  " + "\n  ".join(errs))
    normalize_candidates(data, meta["parts"])
    for c in data["candidates"]:
        c["stats"] = danmaku_stats_for(session, c, meta["parts"])
    save_candidates(session, data)

    local_files = None
    cover_files = None
    if args.local:
        quality = "best" if args.quality == "high" else "low"
        local_files = {}
        cover_files = {}
        pv_dir = session / "preview"
        pv_dir.mkdir(exist_ok=True)
        for c in data["candidates"]:
            p = meta["parts"][int(c["part"]) - 1]
            src = cached_video(session, p, quality)
            out = pv_dir / f"{c['id']}_{quality}_{fmt_compact(c['start'])}-{fmt_compact(c['end'])}.mp4"
            if not out.exists():
                ffmpeg_cut(src, c["start"], c["end"], out, fast=True)
            local_files[str(c["id"])] = f"preview/{out.name}"
            cover_at = c.get("cover_at")
            if cover_at is None:
                cover_at = c["stats"].get("danmaku_peak_at") or (c["start"] + c["end"]) / 2
            cover = pv_dir / f"{c['id']}_cover.jpg"
            if not cover.exists():
                ffmpeg_frame(out, float(cover_at) - c["start"], cover)
            cover_files[str(c["id"])] = f"preview/{cover.name}"
    page = build_preview_html(meta, data, local_files, session, cover_files)
    out = session / "preview.html"
    out.write_text(page, "utf-8")
    print(f"预览页: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


# ---------------------------------------------------------------- cmd: cut

def cmd_cut(args: argparse.Namespace) -> None:
    session = resolve_session(args.session)
    meta = load_parts(session)
    data = load_candidates(session)
    errs = validate_candidates(data, meta["parts"])
    if errs:
        die("candidates.json 校验失败:\n  " + "\n  ".join(errs))
    normalize_candidates(data, meta["parts"])
    by_id = {str(c["id"]): c for c in data["candidates"]}
    ids = [i.strip() for i in re.split(r"[,\s，、]+", args.ids) if i.strip()] if args.ids else list(by_id)
    missing = [i for i in ids if i not in by_id]
    if missing:
        die(f"候选不存在: {missing}")
    burn = args.danmaku
    if burn and args.fast:
        info("--fast 走流复制，改不了画面，本次不烧弹幕")
        burn = False
    if burn:
        find_ffmpeg(need_libass=True)  # 先检查，别下载完视频才发现没 libass
    results = []
    for i in ids:
        c = by_id[i]
        p = meta["parts"][int(c["part"]) - 1]
        src = cached_video(session, p, "best")
        seq = str(c["id"]).zfill(2) if str(c["id"]).isdigit() else str(c["id"])
        name = f"{seq}_{sanitize_filename(str(c.get('type', '')), 8)}_{sanitize_filename(str(c['title']), 30)}_p{c['page']}_{fmt_compact(c['start'])}-{fmt_compact(c['end'])}.mp4"
        out = session / "clips" / name
        info(f"切片 #{c['id']}: {fmt_ts(c['start'])} → {fmt_ts(c['end'])} -> {out.name}")
        ass, burned = None, 0
        if burn:
            dms = load_danmaku(session, p)
            if dms is None:
                info("  没有这个分 P 的弹幕文件，跳过烧弹幕（重跑 parts 可补）")
            else:
                w, h = ffprobe_size(find_ffmpeg(need_libass=True), src)
                ass = session / "cache" / "ass" / f"dm_{re.sub(r'[^A-Za-z0-9_-]', '_', seq)}.ass"
                burned = write_danmaku_ass(dms, c["start"], c["end"], w, h, ass,
                                           opacity=args.danmaku_opacity, area=args.danmaku_area,
                                           fontsize=args.danmaku_size * h / 1080 if args.danmaku_size else None)
                info(f"  弹幕 {burned} 条 -> {ass.name}（{w}x{h}）")
                if burned == 0:
                    ass = None
        ffmpeg_cut(src, c["start"], c["end"], out, fast=args.fast, ass=ass)
        c["danmaku_burned"] = burned
        c["stats"] = danmaku_stats_for(session, c, meta["parts"])
        cover_at = c.get("cover_at")
        if cover_at is None:
            cover_at = c["stats"].get("danmaku_peak_at") or (c["start"] + c["end"]) / 2
        cover = out.with_suffix(".jpg")
        ffmpeg_frame(src, float(cover_at), cover)
        c["status"] = "cut"
        c["output"] = str(out)
        c["cover"] = str(cover) if cover.exists() else None
        results.append({"id": c["id"], "output": str(out), "cover": c["cover"],
                        "duration": round(c["end"] - c["start"], 1), "danmaku_burned": burned})
    save_candidates(session, data)
    print(json.dumps({"session": str(session), "clips": results}, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- cmd: publish

def publish_streamer_links(meta: dict, c: dict, registry: dict | None) -> list[tuple[str, int]]:
    """投稿简介需要带上主播和片段中提到的已登记主播空间链接。"""
    registry = registry or {}
    links: list[tuple[str, int]] = []
    seen: set[int] = set()

    def add(entry: dict | None) -> None:
        if not entry or entry.get("mid") is None:
            return
        mid = int(entry["mid"])
        if mid not in seen:
            seen.add(mid)
            links.append((str(entry.get("name") or mid), mid))

    owner = meta.get("owner") or {}
    owner_mid = owner.get("mid")
    if owner_mid is not None:
        add(registry.get(str(owner_mid)) or owner)

    for name in c.get("mentioned_streamers") or []:
        hit, _ = resolve_streamer(registry, str(name))
        add(hit)

    haystack = "\n".join(str(c.get(k) or "") for k in ("title", "publish_title", "reason", "quote", "desc"))
    for entry in registry.values():
        terms = [entry.get("name"), *(entry.get("aliases") or [])]
        if any(str(term) in haystack for term in terms if term):
            add(entry)
    return links


def build_publish_plan(meta: dict, c: dict, extra_tags: list[str], registry: dict | None = None) -> dict:
    owner = meta["owner"]["name"]
    tags = [owner] + [t for t in meta.get("tags") or [] if t != owner][:3] + ["切片"] + extra_tags
    dedup: list[str] = []
    for t in tags:
        if t and t not in dedup:
            dedup.append(t)
    source = f"https://www.bilibili.com/video/{c['bvid']}?p={c['page']}&t={int(c['start'])}"
    desc = c.get("desc") or (f"切自 {owner} 直播回放《{meta['title']}》 P{c['page']} {fmt_ts(c['start'])}-{fmt_ts(c['end'])}\n"
                             f"原视频: {source}\n{c.get('reason', '')}")
    missing_links = [(name, mid) for name, mid in publish_streamer_links(meta, c, registry)
                     if f"https://space.bilibili.com/{mid}" not in desc]
    if missing_links:
        desc += "\n\n相关主播：\n" + "\n".join(
            f"{name}：https://space.bilibili.com/{mid}" for name, mid in missing_links
        )
    return {"id": c["id"], "file": c["output"], "cover": c.get("cover"),
            "title": (c.get("publish_title") or c["title"])[:80],
            "desc": desc[:2000], "tags": dedup[:10], "tid": meta.get("tid") or 171,
            "copyright": 2, "source": source}


def cmd_publish(args: argparse.Namespace) -> None:
    session = resolve_session(args.session)
    meta = load_parts(session)
    data = load_candidates(session)
    by_id = {str(c["id"]): c for c in data["candidates"]}
    registry = load_registry()
    ids = [i.strip() for i in re.split(r"[,\s，、]+", args.ids) if i.strip()]
    plans = []
    for i in ids:
        c = by_id.get(i)
        if not c:
            die(f"候选不存在: {i}")
        if c.get("status") not in ("cut", "published") or not c.get("output") or not Path(c["output"]).exists():
            die(f"候选 {i} 还没有切出成品，先运行 cut")
        plans.append(build_publish_plan(meta, c, args.tag or [], registry=registry))
    dtime = None
    if args.at:
        when = dt.datetime.strptime(args.at, "%Y-%m-%d %H:%M")
        if when - dt.datetime.now() < dt.timedelta(hours=4):
            die("定时发布必须距现在 4 小时以上（B 站限制）")
        dtime = int(when.timestamp())
    print(json.dumps({"mode": "as_parts" if args.as_parts else "separate", "dtime": args.at, "plans": plans},
                     ensure_ascii=False, indent=2))
    if not args.yes:
        print("\n以上是投稿计划（未提交）。确认无误后加 --yes 再次运行以真正上传。", file=sys.stderr)
        return
    biliup = need_tool("biliup", "请从 https://github.com/biliup/biliup-rs/releases 下载 biliup 并放入 PATH，然后运行 `biliup login` 扫码登录")
    cookie_json = Path.cwd() / "cookies.json"
    if not cookie_json.exists() and not (CONFIG_DIR / "biliup-cookies.json").exists():
        die("biliup 未登录：请在任意目录运行 `biliup login` 扫码，然后把生成的 cookies.json 移到 "
            f"{CONFIG_DIR / 'biliup-cookies.json'}")
    user_cookie = cookie_json if cookie_json.exists() else CONFIG_DIR / "biliup-cookies.json"

    def upload(plan: dict, files: list[str]) -> None:
        cmd = [biliup, "-u", user_cookie, "upload", "--copyright", "2", "--source", plan["source"],
               "--tid", str(plan["tid"]), "--title", plan["title"], "--desc", plan["desc"],
               "--tag", ",".join(plan["tags"])]
        if plan.get("cover"):
            cmd += ["--cover", plan["cover"]]
        if dtime:
            cmd += ["--dtime", str(dtime)]
        cmd += files
        r = run(cmd)
        if r.returncode != 0:
            die(f"上传失败: {plan['title']}")

    if args.as_parts:
        first = dict(plans[0])
        first["title"] = args.title or first["title"]
        upload(first, [p["file"] for p in plans])
        for i in ids:
            by_id[i]["status"] = "published"
    else:
        for plan, i in zip(plans, ids):
            upload(plan, [plan["file"]])
            by_id[i]["status"] = "published"
            by_id[i]["published_at"] = dt.datetime.now().isoformat(timespec="seconds")
            save_candidates(session, data)
    save_candidates(session, data)
    print("上传完成。B 站审核通过后会在创作中心显示。")


# ---------------------------------------------------------------- cmd: clean

def cmd_clean(args: argparse.Namespace) -> None:
    session = resolve_session(args.session)
    freed = 0
    for d in ("cache", "preview"):
        p = session / d
        if p.exists():
            freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            shutil.rmtree(p)
    print(f"已清理 {session} 的缓存，释放约 {freed / 1e9:.2f} GB")


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="bili_clip.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("cookies", help="导出/校验 B 站 cookie")
    s.add_argument("--browser", default="chrome")
    s.add_argument("--refresh", action="store_true", help="强制重新从浏览器导出")
    s.set_defaults(fn=cmd_cookies)

    s = sub.add_parser("parts", help="列出分 P，建立工作目录")
    s.add_argument("url", help="视频链接 / BV 号 / 回放系列链接（取最新一集）")
    s.add_argument("--season", action="store_true", help="把视频所属合集的所有集都列出来")
    s.add_argument("--browser", default="chrome")
    s.set_defaults(fn=cmd_parts)

    s = sub.add_parser("latest", help="找某个切过的主播最近一场回放并列分 P")
    s.add_argument("streamer", help="主播名 / 别名 / uid / 回放系列链接")
    s.add_argument("--list", action="store_true", help="只列最近投稿，不建工作目录")
    s.add_argument("--show", type=int, default=8, help="列出最近几条投稿")
    s.add_argument("--force", action="store_true", help="即使最近一场已切过也重新处理")
    s.add_argument("--season", action="store_true")
    s.add_argument("--browser", default="chrome")
    s.set_defaults(fn=cmd_latest)

    s = sub.add_parser("streamers", help="列出切过的主播；--set-list 绑定回放系列；--alias 添加别名")
    s.add_argument("--set-list", metavar="URL", help="绑定回放系列/合集链接到它的主播")
    s.add_argument("--alias", nargs="+", metavar="X", help="<主播名或uid> <别名>...")
    s.add_argument("--browser", default="chrome")
    s.set_defaults(fn=cmd_streamers)

    s = sub.add_parser("analyze", help="拉字幕与弹幕，生成压缩稿")
    s.add_argument("session", help="工作目录或 BV 号")
    s.add_argument("--parts", default="all", help="分 P 序号，如 1,3-4 或 all")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--browser", default="chrome")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("find", help="按关键词在字幕和弹幕里定位片段")
    s.add_argument("session")
    s.add_argument("keywords", nargs="+", help="一个或多个关键词，任一命中即算")
    s.add_argument("--parts", default="", help="只搜这些 P，如 2,4")
    s.add_argument("--context", type=int, default=45, help="建议区间在首末命中外各留多少秒")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("preview", help="根据 candidates.json 生成预览页")
    s.add_argument("session")
    s.add_argument("--local", action="store_true", help="下载画质做离线精确预览")
    s.add_argument("--quality", choices=["low", "high"], default="low",
                   help="--local 时的下载画质：low 480P（默认，快），high 登录态下的最高画质（慢很多）")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_preview)

    s = sub.add_parser("cut", help="切出成品")
    s.add_argument("session")
    s.add_argument("--ids", default="", help="候选 id，逗号分隔；留空为全部")
    s.add_argument("--fast", action="store_true", help="不重编码（关键帧对齐，起止有 1-2 秒误差；不能烧弹幕）")
    s.add_argument("--danmaku", action="store_true", help="把片段内的弹幕烧进画面（默认不烧）")
    s.add_argument("--danmaku-opacity", type=float, default=DANMAKU_OPACITY, help="弹幕不透明度 0-1，默认 0.75")
    s.add_argument("--danmaku-area", type=float, default=DANMAKU_AREA, help="弹幕占画面高度比例，默认 0.5 半屏")
    s.add_argument("--danmaku-size", type=float, default=None, help="1080p 下的弹幕字号，默认 57")
    s.set_defaults(fn=cmd_cut)

    s = sub.add_parser("publish", help="用 biliup 投稿（可选）")
    s.add_argument("session")
    s.add_argument("--ids", required=True)
    s.add_argument("--yes", action="store_true", help="真正上传；不加只打印计划")
    s.add_argument("--at", help='定时发布，格式 "2026-09-07 20:00"')
    s.add_argument("--as-parts", action="store_true", help="合并为一个稿件的多个分 P")
    s.add_argument("--title", help="--as-parts 时的稿件标题")
    s.add_argument("--tag", action="append", help="额外标签，可重复")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("clean", help="删除视频缓存")
    s.add_argument("session")
    s.set_defaults(fn=cmd_clean)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
