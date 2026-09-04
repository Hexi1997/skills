#!/usr/bin/env node
// 把一个目录（默认：当前 git worktree 根）加进 VS Code 的 .code-workspace 文件。
// 幂等：按绝对路径去重，已存在则不改文件。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

const DEFAULT_WS = path.join(os.homedir(), 'Desktop', 'novel.code-workspace')

const args = process.argv.slice(2)
let wsFile = DEFAULT_WS
const positional = []
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--workspace' || args[i] === '-w') wsFile = args[++i]
  else if (args[i] === '--help' || args[i] === '-h') {
    console.log('用法: add-folder.mjs [目录] [--workspace <file.code-workspace>]')
    console.log(`默认目录 = 当前 git worktree 根；默认 workspace = ${DEFAULT_WS}`)
    process.exit(0)
  } else positional.push(args[i])
}

const target = path.resolve(
  positional[0] ??
    execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim(),
)
wsFile = path.resolve(wsFile)

if (!fs.existsSync(target)) {
  console.error(`✗ 目录不存在: ${target}`)
  process.exit(1)
}
if (!fs.existsSync(wsFile)) {
  console.error(`✗ workspace 文件不存在: ${wsFile}`)
  process.exit(1)
}

const raw = fs.readFileSync(wsFile, 'utf8')
let data
try {
  data = JSON.parse(raw)
} catch (err) {
  console.error(`✗ ${wsFile} 不是纯 JSON（可能带注释/尾逗号），脚本会丢掉这些内容，拒绝改写。`)
  console.error(`  解析错误: ${err.message}`)
  console.error('  请手动在 folders 里加一条，或先把注释去掉。')
  process.exit(1)
}
if (!Array.isArray(data.folders)) {
  console.error('✗ workspace 文件里没有 folders 数组')
  process.exit(1)
}

const wsDir = path.dirname(wsFile)
const abs = (p) => path.resolve(wsDir, p)
const existing = data.folders.find((f) => f && typeof f.path === 'string' && abs(f.path) === target)
if (existing) {
  console.log(`= 已存在，未改动: ${existing.path}  →  ${target}`)
  process.exit(0)
}

// 相对路径更好读；但跨盘/跨顶层目录时 ../../../.. 会失控，那种情况用绝对路径。
const rel = path.relative(wsDir, target).split(path.sep).join('/')
const upLevels = rel.split('/').filter((seg) => seg === '..').length
const entryPath = upLevels > 4 ? target : rel
data.folders.push({ path: entryPath })
fs.writeFileSync(wsFile, JSON.stringify(data, null, '\t') + '\n')
console.log(`✓ 已加入 ${wsFile}`)
console.log(`  path: ${entryPath}`)
console.log(`  → ${target}`)
console.log(`  当前共 ${data.folders.length} 个 folder`)
