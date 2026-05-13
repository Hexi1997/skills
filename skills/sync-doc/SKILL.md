---
name: sync-doc
description: 用于将 Git 代码变更（git diff）同步更新到项目文档的工具。通过结构化分析代码改动，并生成最小化文档 patch，保持文档与代码一致性。
version: 1.2.0
auto_trigger: false        # 是否在检测到 diff 时自动执行
trigger_events:
  - manual                 # 仅手动调用（/sync-doc）
---


## 1. 概述

`sync-doc` 是一个用于将 **Git 代码变更自动同步到项目文档**的技能（skill）。

它基于：

* Git diff（作为唯一事实来源，支持 committed/staged/unstaged）
* 结构化变更解析
* LLM 生成文档 patch
* checkpoint 机制保证增量一致性

* * *

## 2. 使用方式

```Bash
/sync-doc --since HEAD~4
```

或：

```Bash
/sync-doc --since <commit>
```

如果当前代码已经 `git add`，但还没有 `git commit`，则直接执行：

```Bash
/sync-doc
```

此时 skill 应读取 staged diff，并把这些尚未提交的改动同步到文档。

* * *

## 3. 核心目标

该 skill 用于解决以下问题：

* 代码修改后文档不同步
* 手动维护文档成本高
* 多次同步导致文档漂移
* 无法追踪“上一次同步到哪里”

* * *

## 4. 输入参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| since | string | git commit 或 ref（如 HEAD~4）；传入后显式指定同步范围，并覆盖 checkpoint 自动推导 |
| dry-run | boolean | 只输出 diff 不写入文档 |
| force | boolean | 忽略 checkpoint 的去重保护并强制执行，但成功后仍需写入新的 checkpoint |

* * *

## 5. 执行流程

### Step 1：获取 Git diff

`sync-doc` 必须先判断当前仓库中的代码变更状态，再决定 diff 来源。

支持三类输入源：

1. 已提交但未同步的 commit range
2. 已 `stage` 但未 `commit` 的索引区改动
3. 未 `stage` 的工作区改动（unstaged）

推荐判断顺序：

1. 如果显式传入 `--since`，忽略 checkpoint，直接读取提交区间 diff
2. 否则如果存在 checkpoint，先基于 checkpoint 推导待同步范围
3. 如果没有可用 checkpoint，再检查是否存在 staged 改动
4. 如果不存在 staged 改动，再检查是否存在 unstaged 改动
5. 否则退出，并提示“没有可同步的代码变更”

对应命令：

```Bash
git diff <since>..HEAD
git diff --cached
git diff
```

状态探测建议：

```Bash
git diff --cached --quiet
git diff --quiet
```

说明：

* `git diff <since>..HEAD`：用于同步某个已提交区间
* `git diff --cached`：用于同步已经 `git add` 但尚未提交的改动
* `git diff`：用于同步尚未 stage 的 unstaged 改动

输出作为唯一变更源，且一次执行默认只选择其中一种来源，避免重复计算和重复写文档。

当 staged 与 unstaged 同时存在时，默认行为必须是：

* 本次仅同步 staged diff
* 明确提示存在未纳入本次同步的 unstaged 改动
* 不得静默把 unstaged 改动混入 staged 同步结果

这样可以保持“用户已经准备提交的改动”和“仍在编辑中的改动”边界清晰。

* * *

### Step 2：结构化变更解析

将 raw diff 转换为结构化事件：

```JSON
{
  "file": "src/api/user.ts",
  "type": "modify",
  "hunks": [
    {
      "before": "...",
      "after": "..."
    }
  ]
}
```

* * *

### Step 3：影响范围分析

识别影响的文档模块：

* API 文档
* 组件文档
* 架构说明
* Feature 文档

并且必须显式执行“代码路径 -> 文档路径”的目标发现规则，避免把正确内容写入错误文档。

推荐映射规则：

* `src/api/**`、`server/api/**` -> `/docs/api.md`
* `src/components/**`、`ui/**` -> `/docs/components.md`
* `src/features/**`、`app/**/feature/**` -> `/docs/features.md`
* `src/core/**`、`src/lib/**`、`infrastructure/**` -> `/docs/architecture.md`

如果一个代码文件同时命中多个规则：

* 优先选择最具体的路径规则
* 若仍无法唯一确定，则输出 manual review，而不是猜测写入

如果仓库中存在更明确的项目约定（如 `README.md`、`CONTRIBUTING.md`、`docs/INDEX.md` 中定义了文档映射关系），应优先使用项目约定覆盖默认映射。

* * *

### Step 4：生成文档 Patch（LLM）

LLM 输入：

* diff events
* 当前 doc 内容（局部）
* 更新规则
* 目标文档路径

生成规则：

* 仅更新被本次 diff 影响的文档段落
* 不得重排未受影响章节
* 不得删除无关内容
* 若无法定位应修改的 section，则输出建议 patch 并标记 manual review，而不是直接写入

输出：

```Diff
- old section
+ updated section
```

* * *

### Step 5：应用 Patch

* 精确更新 markdown 文件
* 不重写整篇文档
* 保持局部修改最小化
* 保留现有标题层级、列表结构和周边未修改内容

应用前建议校验：

* patch 的目标文件是否存在
* patch 是否仍能匹配生成时读取的原始 section
* 若文档已发生漂移，是否应降级为 manual review

* * *

### Step 6：写入 checkpoint

记录同步状态：

```JSON
{
  "source_type": "commit_range",
  "last_synced_commit": "abc123",
  "patch_hash": "sha256:xxx",
  "affected_docs": ["docs/api.md"],
  "timestamp": 1710000000
}
```

如果同步来源不是 commit range，则应记录来源快照，而不是伪造 commit：

```JSON
{
  "source_type": "staged",
  "base_commit": "abc123",
  "tree_hash": "def456",
  "patch_hash": "sha256:xxx",
  "affected_docs": ["docs/components.md"],
  "timestamp": 1710000000
}
```

* * *

## 6. checkpoint 机制（关键）

每次成功 sync 后记录：

* source type（`commit_range` / `staged` / `unstaged`）
* commit hash 或 base commit
* staged/unstaged 场景下的 diff 指纹（如 tree hash、patch hash）
* sync 时间
* affected docs

用于：

* 防止重复同步
* 支持恢复
* 支持增量 diff

特殊要求：

* 当来源是 staged diff 时，checkpoint 不能简单依赖 `HEAD`，因为代码尚未提交
* 同一份 staged patch 重复执行时，应被识别为已同步，避免二次写入文档

* * *

## 7. 幂等性要求

同一输入必须保证：

> 多次执行结果一致（idempotent）

规则：

* 同一 commit range 不重复应用
* 同一份 staged diff 不重复应用
* 同一份 unstaged diff 不重复应用
* patch 可重复执行不改变最终结果

* * *

## 8. 参数与优先级规则

为了避免行为歧义，参数优先级必须固定如下：

1. `--since`
2. `checkpoint`
3. `staged diff`
4. `unstaged diff`

具体规则：

* 传入 `--since` 时，不读取 checkpoint 决定范围，只可将 checkpoint 作为历史记录参考
* 未传入 `--since` 且 checkpoint 可用时，应优先尝试增量同步
* checkpoint 不可用或无法解析时，回退到 staged / unstaged 检测
* `--force` 只跳过去重保护，不改变 diff 来源优先级

* * *

## 9. 错误处理

### Git diff 失败

* 中止执行
* 输出错误信息

### 没有可同步变更

* 当 `git diff --cached --quiet` 且 `git diff --quiet` 都为成功，且未传入 `--since` 时
* 输出“当前没有已提交、已 stage 或工作区改动可用于同步文档”
* 不写入 checkpoint

### LLM 生成失败

* 不写入任何文档
* 保持原状态

### Patch 冲突

* fallback：标记为 manual review

* * *

## 10. 标准输出契约

`/sync-doc` 执行结束后，应该输出结构化摘要，至少包含：

* `source_type`
* `base_ref` 或 `since`
* 受影响代码文件数量
* 更新的文档文件列表
* `checkpoint` 是否写入成功
* 是否存在 skipped files
* 是否存在 manual review 项

推荐输出示例：

```Text
sync-doc completed
source_type: staged
base_ref: HEAD
changed_code_files: 3
updated_docs:
  - docs/api.md
  - docs/components.md
checkpoint_written: true
manual_review: 1
skipped_files: 0
note: unstaged changes detected but not included in this run
```

当开启 `--dry-run` 时：

* 输出建议 patch 和摘要
* 不写入文档
* 不写入 checkpoint

* * *

## 11. 推荐目录结构

```
/docs
  api.md
  components.md
  architecture.md

/.sync
  checkpoint.json
  events/
    001.json
```

* * *

## 12. 非目标（明确边界）

本 skill 不负责：

* 代码重构
* 自动提交 commit
* CI/CD 发布
* 运行测试
* AST-level code rewrite

* * *

## 13. 工具依赖

必须依赖：

* git CLI
* Node.js runtime
* markdown parser（可选）
* LLM API（用于 patch generation）

* * *

## 14. 推荐执行语义

为了覆盖最常见的使用习惯，推荐 `/sync-doc` 默认行为如下：

* `/sync-doc --since <ref>`：同步指定提交范围
* `/sync-doc` 且存在 staged 改动：同步 staged 改动
* `/sync-doc` 且无 staged 但有 unstaged 改动：同步 unstaged 改动
* `/sync-doc` 且两者都没有：直接退出并提示

这意味着在“用户改完代码，已经 stage，但还没 commit”的场景下，不需要额外参数，直接执行 `/sync-doc` 即可。
