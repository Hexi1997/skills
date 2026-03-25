#!/usr/bin/env node
'use strict';

/**
 * Syncs this repo's skills/ directory to Claude Code CLI global skills (~/.claude/skills).
 * Each immediate subdirectory of skills/ that contains SKILL.md is copied as one skill.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const REPO_ROOT = path.resolve(__dirname, '..');
const SOURCE_SKILLS_DIR = path.join(REPO_ROOT, 'skills');

function parseArgs(argv) {
  const args = { dryRun: false, dest: null, help: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run' || a === '-n') args.dryRun = true;
    else if (a === '--help' || a === '-h') args.help = true;
    else if (a === '--dest' && argv[i + 1]) {
      args.dest = argv[++i];
    }
  }
  return args;
}

function printHelp() {
  console.log(`Usage: node scripts/sync-claude-skills.js [options]

Sync repository skills/ to Claude Code global skills (default: ~/.claude/skills).

Options:
  --dest <dir>   Override destination (also: CLAUDE_SKILLS_DIR env)
  --dry-run, -n  Print actions without copying
  --help, -h     Show this help

Environment:
  CLAUDE_SKILLS_DIR   Global skills directory (default: ~/.claude/skills)
`);
}

function isSkillDir(dirPath) {
  try {
    const stat = fs.statSync(dirPath);
    if (!stat.isDirectory()) return false;
    return fs.existsSync(path.join(dirPath, 'SKILL.md'));
  } catch {
    return false;
  }
}

function listSkillDirs(sourceDir) {
  if (!fs.existsSync(sourceDir)) {
    throw new Error(`Source directory does not exist: ${sourceDir}`);
  }
  const names = fs.readdirSync(sourceDir);
  const valid = [];
  for (const name of names) {
    if (name.startsWith('.')) continue;
    const full = path.join(sourceDir, name);
    if (isSkillDir(full)) valid.push(name);
  }
  valid.sort();
  return valid;
}

function syncSkill(name, sourceBase, destBase, dryRun) {
  const src = path.join(sourceBase, name);
  const dest = path.join(destBase, name);

  if (dryRun) {
    console.log(`[dry-run] would replace: ${dest}  <=  ${src}`);
    return;
  }

  fs.mkdirSync(destBase, { recursive: true });
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(src, dest, { recursive: true });
  console.log(`synced: ${name}  ->  ${dest}`);
}

function main() {
  const args = parseArgs(process.argv);
  if (args.help) {
    printHelp();
    process.exit(0);
  }

  const destBase =
    args.dest ||
    process.env.CLAUDE_SKILLS_DIR ||
    path.join(os.homedir(), '.claude', 'skills');

  let names;
  try {
    names = listSkillDirs(SOURCE_SKILLS_DIR);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }

  if (names.length === 0) {
    console.warn(`No skill directories with SKILL.md under: ${SOURCE_SKILLS_DIR}`);
    process.exit(0);
  }

  console.log(
    `Source: ${SOURCE_SKILLS_DIR}\nDestination: ${destBase}\nSkills: ${names.join(', ')}\n`
  );

  for (const name of names) {
    syncSkill(name, SOURCE_SKILLS_DIR, destBase, args.dryRun);
  }

  if (args.dryRun) {
    console.log('\nDry run only; no files were written.');
  } else {
    console.log('\nDone. Restart Claude Code if it is running so it picks up changes.');
  }
}

main();
