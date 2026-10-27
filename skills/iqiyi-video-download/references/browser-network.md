# 浏览器播放请求定位

以下形态于 2026-10-27 的爱奇艺短剧播放器中实际观察过，是线索，不是固定 API 合约。先读本次浏览器工具文档，发现网络调试能力后再调用；不要硬编码 browser ID、tab ID、requestId、tvid 或 CDN 地址。

## 捕获窗口

1. 打开作品页，正常展开选集。
2. 启用网络事件并记录游标，再点击指定集号。
3. 收集该动作后的响应，核对页面已选集号、时长。
4. 有分集分页时按实际可见范围切换，不假设所有页面都采用同一分组。

Codex 提供 `cua_repl` 时，用已文档化的 `cua` API；从 tab capabilities 发现 `cdp`，先读其 `documentation()`。支持时使用 `Network.enable`、事件游标和 `Network.getResponseBody`。其他客户端使用其获准的等价网络工具。不要读取浏览器凭据或站点隐藏状态来拼接请求。

```javascript
// tab 已按本次工具文档绑定；首次使用先读取 cdp.documentation()。
const cdp = await tab.capabilities.get('cdp');
await cdp.send('Network.enable');
let cursor = (await cdp.readEvents()).cursor;
// 通过可见选集控件点击目标集，核对页面。
const page = await cdp.readEvents({
  afterSequence: cursor,
  methods: ['Network.responseReceived'],
  limit: 500,
});
cursor = page.cursor;
// hasMore 时继续分页；truncated 时不能认定记录完整。
// 仅输出相关 requestId、tvid 和响应类型；签名 URL 留在内存或私有临时文件。
```

## 观察到的响应形态

### 播放清单

`https://cache.video.iqiyi.com/dash?...tvid=...`

回包中曾有 `data.program.video[]`，其中 `_selected` 为选中格式，`duration` 为时长，`bid` 为清晰度标识，`vid` 为视频版本标识，`m3u8` / `m3u8Url` 为内嵌或外部清单（可能为空）。`drmType` 不能单独证明实际媒体是否可解码。

也可能通过播放器 accelerator 请求获得播放信息。只读取正常播放实际观察到的响应，不生成鉴权参数。

### CDN 调度

`https://pcw-data.video.iqiyi.com/.../*.ts?...qd_tvid=...`

后缀虽然是 `.ts`，本次观察到返回 JSON：

```json
{"d":[{"URL":"实际 CDN 视频地址"}]}
```

读取对应 requestId 的 body，解析 JSON，再取得 `d[].URL`。实际 CDN 曾为 `*.inter.71edge.com`；域名不是永久白名单，须来自正常播放的真实回包。

内嵌 m3u8 曾列出同一 TS 对象的 `start` / `end` 范围，实际 CDN 对象下载后是完整本集。不能推广到所有视频：必须核对页面时长并完整解码；多对象分片按清单处理。

## 分集误抓防范

- 以当前选集对应的播放清单 tvid 为锚点，匹配调度请求 `qd_tvid`；可用已观察的分集元数据交叉核对。
- 同一 tvid 的多个 CDN 是候选副本；不同 tvid 可能是后续集预加载，不混在一个文件里。
- 事件窗口丢失时重新选择目标集捕获，不凭顺序猜测；缓存响应也可查看其网络记录与回包。
- URL 与播放器内容可能不一致。本次精确第 24 集 URL 初始仍播放第 1 集，点击选集后才正确。
- 网页能播不代表复制出的对象可用，最终以完整本地文件验证结果为准。

## 下载示例

将实际 CDN URL 写入权限 0600 的临时文件，用 curl 的 stdin 配置读取，避免 shell 拼接 `&` 等字符及在命令行暴露签名 URL：

```python
import json
import pathlib
import subprocess

url = pathlib.Path('/private/temp/episode-url.txt').read_text().strip()
config = 'url = ' + json.dumps(url, ensure_ascii=True) + '\n'
subprocess.run([
    'curl', '--config', '-', '--fail', '--location', '--silent', '--show-error',
    '--max-time', '60', '--referer', 'https://www.iqiyi.com/',
    '--output', '/private/temp/episode.ts',
], input=config, text=True, check=True)
```

登录、会员、地域或 DRM 限制按实际情况报告，不尝试绕过；拿不到可用媒体时保留精确原因，不承诺未验证的替代 MP4。
