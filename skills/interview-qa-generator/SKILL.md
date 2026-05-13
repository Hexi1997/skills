---
name: interview-qa-generator
description: 当用户提出“生成面试题”、“生成面试问答”、“根据我的简历生成面试题”、“帮我准备面试”、“模拟面试”，或希望基于其简历和项目描述生成面试问题与答案时，应使用此 skill。
version: 1.0.0
---

# Interview Q&A Generator

此 skill 会基于用户简历中的项目描述以及对代码库的理解，自动生成面试问题与答案，并将结果保存为一个包含**岗位名称**的文件（见下方“保存文件”）。

## 适用场景

当用户需要：
- 基于简历内容生成面试题
- 模拟面试官提问
- 准备技术面试
- 生成与项目相关的面试问答

## 使用方式

### Step 1：收集信息

使用 `AskUserQuestion` 工具向用户收集以下信息：

1. **项目描述**
   问题："Please provide the project description from your resume"
   - 这是用户写在简历中的项目摘要
   - 示例："A Git-driven, local-first blog system where articles are Markdown files and publishing is done via Git Push"

2. **题目数量**
   问题："How many interview questions would you like to generate?"
   可选项：5、10、15、20
   - 默认推荐 10 题
   - 可选：5 / 10 / 15 / 20

3. **岗位名称**
   问题："What is your job title?"
   可选项：Frontend Engineer、Senior Frontend Engineer、Full-Stack Engineer、Other
   - 用于调整题目的关注重点
   - 也会用于输出文件名；如果选择 **Other**，则继续询问具体岗位名称，并将其用于文件名

### Step 2：理解代码库

1. 探索项目结构：
   - 读取 `package.json` 了解技术栈
   - 阅读核心业务逻辑文件
   - 阅读页面组件和 API 实现

2. 提取关键技术点：
   - 架构设计模式
   - 使用的技术栈和库
   - 核心功能的实现方式
   - 性能优化策略
   - 部署与运维方案

### Step 3：生成问题

从以下维度生成面试题：

1. **架构设计**：整体项目架构、技术选型原因
2. **核心功能**：主要功能的实现原理
3. **技术深度**：对关键技术的深入理解
4. **性能优化**：SSG、静态生成、缓存策略
5. **SEO**：搜索引擎优化的实现方式
6. **数据管理**：Git 驱动的内容管理、版本控制
7. **部署与运维**：部署平台选择、CI/CD
8. **工程化思维**：局限性分析、未来规划

### Step 4：生成答案

每个答案都应：
- 结合代码中的真实实现
- 解释底层技术原理
- 提供相关代码片段
- 说明业务背景以及技术决策的原因

### Step 5：保存文件

**文件名**（必须包含岗位名称）：

- 格式：`interview-qa-{job-title}.md`
- `{job-title}`：必须与 Step 1 中收集到的岗位名称一致；避免使用非法文件名字符，如 `/ \ : * ? " < > |`；空格可以保留，也可以替换为 `-`

示例：`interview-qa-frontend-engineer.md`

将生成结果按以下格式写入上述路径：

```markdown
# {Project Name} Interview Questions & Answers

> Based on resume description: {user-provided project description}

---

## 1. {Question Title}

**Answer:**
{Detailed answer including code examples}

---
... (more questions)
```

## 输出要求

- 问题和答案使用英文编写
- 代码示例使用带语言标识的 fenced code block
- 答案应引用具体代码位置，例如 `filename:line number`
- 包含面试提示，以及每道题考察点的简要总结
- 输出文件名必须为 `interview-qa-{job-title}.md`；不要使用固定名称如 `interview-qa.md`，岗位名称是必需的
