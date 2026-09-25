# bili-clip

> An AI-assisted clipping workflow for Bilibili live-stream replays: pull AI subtitles + danmaku, let Claude pick the highlights, preview, cut, optionally publish.

面向 B 站切片员的 Claude Code / Codex skill。给它一个直播回放链接，它会：

1. 记住你切过的主播和她的回放系列；之后一句「切 XX 最近的回放」就自动取最新一场
2. 列出回放的分 P（或整个合集），你选要分析哪几个
3. 拉取 B 站 AI 字幕和全量弹幕，压缩成「主播在说什么 + 观众在哪炸」对齐的稿子
4. 你点名了片段（「她唱恋爱循环那段」）就只定位那一处；没给方向就通读推荐 6 到 8 个候选：类型、起止、标题、理由、原句摘录
5. 生成本地预览页，每个候选内嵌 B 站播放器定位到起点
6. 你在对话里回复序号和微调，它只下载命中的分 P，用 ffmpeg 精确切出 mp4 和封面，加 `--danmaku` 可把弹幕烧进画面
7. 可选：用 biliup 投稿，简介自动带上主播和片段中提到的已登记主播 space 链接，发布前把标题、标签、简介列给你确认

## 安装

```bash
# 依赖
brew install yt-dlp ffmpeg-full   # 要烧弹幕时 ffmpeg 需带 libass；Windows: scoop / choco / 官网 full 版二进制
# 可选，只有要自动投稿时需要
# https://github.com/biliup/biliup-rs/releases 下载 biliup 放进 PATH，然后 biliup login

# 装 skill（所有 agent）
npx skills add Hexi1997/skills --skill bili-clip -a '*' -g -y
```

登录态：脚本首次运行会用 yt-dlp 从 Chrome 导出 B 站 cookie 到 `~/.config/bili-clip/cookies.txt`。读不到时（Windows 新版 Chrome 常见）用浏览器插件导出 Netscape 格式 `cookies.txt` 放到该路径即可。AI 字幕必须登录才能拉，这一步绕不开。

## 使用

在 Claude Code 里贴链接：

```
/bili-clip https://space.bilibili.com/27564630/lists/217666?type=series   # 第一次：贴回放系列链接
帮我找昨晚纯黑迟到和唱雨爱那两段，再推荐几个

/bili-clip 切一下丝茉茉最近的回放                                       # 之后：只说主播名
```

或者直接用脚本：

```bash
S=scripts/bili_clip.py
python3 $S streamers --set-list "https://space.bilibili.com/27564630/lists/217666?type=series"  # 绑定回放系列
python3 $S latest  丝茉茉                                            # 取该主播最新一场回放并列分 P
python3 $S parts   "https://www.bilibili.com/video/BV13AbH6QEak"   # 或直接给视频链接列分 P
python3 $S analyze BV13AbH6QEak --parts 4-7                          # 拉字幕弹幕，出压缩稿
python3 $S find    BV13AbH6QEak 恋爱循环 --parts 2                    # 按关键词在字幕/弹幕里定位片段
# …… 由 Claude 读 raw/pNN_compact.txt 写 candidates.json ……
python3 $S preview BV13AbH6QEak                                      # 预览页
python3 $S cut     BV13AbH6QEak --ids 1,3,5                          # 切成品，--danmaku 把弹幕烧进画面
python3 $S publish BV13AbH6QEak --ids 1,3 --yes                      # 可选：投稿
python3 $S clean   BV13AbH6QEak                                      # 删视频缓存
```

工作目录默认 `~/Movies/bili-clip/<BV>_<标题>/`：

```
raw/            parts.json、pNN.srt、pNN_compact.txt、danmaku_<cid>.json
cache/          下载的整 P 视频（clean 可删）
candidates.json 候选列表，Claude 写、你改
preview.html    预览页
clips/          成品 mp4 + 封面 jpg
```

## 设计要点

- **只依赖标准库 + yt-dlp + ffmpeg。** 弹幕走 B 站分段 protobuf 接口，自带最小解析器，不装 pip 包；弹幕转 ASS 也是自己排轨道，不依赖 danmaku2ass。
- **视频按需下载。** 分析只用字幕和弹幕；预览用 B 站在线播放器；只有确认后的分 P 才下载。8 小时回放不会全下。
- **记住主播。** `~/.config/bili-clip/streamers.json` 记每个主播的 uid、别名、回放系列和切过的场次，`latest` 靠它一句话取最新回放；`streamers/<主播>.md` 记常驻嘉宾、梗和投稿模板给分析用，越用越准。
- **B 站接口自带 WBI 签名**，UP 主投稿列表和用户搜索都能用，不依赖第三方库。
- **发布可选且有确认。** `publish` 不加 `--yes` 只打印计划；每个切片独立稿件，转载类型附原视频定位链接。

## 已知边界

- 没有 AI 字幕的分 P 不能分析（B 站生成有延迟，稍后重试）；不做本地转写。
- 不做竖屏、片头片尾、烧字幕、实时切片。
- 代码按跨平台写法编写，只在 macOS 实测过；Windows 未验证。

## 开发

```bash
python3 -m unittest discover -s tests
```

## License

MIT
