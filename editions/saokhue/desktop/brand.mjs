#!/usr/bin/env node
/**
 * Sao Khuê branding for the desktop build, applied for the length of one build.
 *
 * The Electron shell and the icon files in this repository stay as upstream
 * ships them. packaging/build-saokhue.sh runs `apply` before it builds and
 * `restore` when it exits:
 *
 *   node editions/saokhue/desktop/brand.mjs apply
 *   node editions/saokhue/desktop/brand.mjs restore
 *
 * `apply` does two things, and keeps the original of every file it changes
 * under editions/saokhue/.brand-backup so `restore` can put it back:
 *
 *   1. copies each file under ./assets over the file at the same path from the
 *      repository root (icons and logos);
 *   2. respells the product name in the shell's own JavaScript and HTML.
 *
 * Only display text is handled here. What the app needs to run correctly from a
 * source checkout (its data home, its app id, package.json) is in the sources.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../..')
const ASSETS = path.join(HERE, 'assets')
const BACKUP = path.resolve(HERE, '../.brand-backup')
const MANIFEST = path.join(BACKUP, 'manifest.json')

const SHELL = 'website/electron'
const TEXT_EXTENSIONS = new Set(['.js', '.html'])
/** Directories of the shell whose files are never respelled. */
const SKIPPED_DIRS = new Set(['node_modules', 'dist', 'test', 'build', 'scripts'])
/**
 * Files left as they are: the Linux desktop entry is matched against the name
 * electron-builder writes, which packaging/build-desktop.sh sets.
 */
const SKIPPED_FILES = new Set(['website/electron/linux-desktop-integration.js'])

const REPLACEMENTS = [['Kiro Crew', 'Sao Khuê']]

function brandText(text) {
  let out = text
  for (const [from, to] of REPLACEMENTS) out = out.split(from).join(to)
  return out
}

function walk(dir, visit, skipDirs = new Set()) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (!skipDirs.has(entry.name)) walk(full, visit, skipDirs)
    } else if (entry.isFile()) {
      visit(full)
    }
  }
}

const rel = (file) => path.relative(ROOT, file).split(path.sep).join('/')

function apply() {
  if (fs.existsSync(MANIFEST)) {
    throw new Error(`${rel(BACKUP)} holds an earlier run; run "brand.mjs restore" first`)
  }
  fs.mkdirSync(BACKUP, { recursive: true })
  const changed = []
  // Written before the first change, and again after each, so a run that stops
  // halfway still names every file it touched.
  const record = (target) => {
    changed.push(target)
    fs.writeFileSync(MANIFEST, JSON.stringify(changed, null, 2))
  }
  const keep = (target) => {
    const copy = path.join(BACKUP, target)
    fs.mkdirSync(path.dirname(copy), { recursive: true })
    fs.copyFileSync(path.join(ROOT, target), copy)
    record(target)
  }

  let assets = 0
  walk(ASSETS, (file) => {
    const target = path.relative(ASSETS, file).split(path.sep).join('/')
    if (!fs.existsSync(path.join(ROOT, target))) {
      throw new Error(`asset ${target} has no file to replace in the repository`)
    }
    keep(target)
    fs.copyFileSync(file, path.join(ROOT, target))
    assets += 1
  })

  let texts = 0
  walk(path.join(ROOT, SHELL), (file) => {
    const target = rel(file)
    if (!TEXT_EXTENSIONS.has(path.extname(file)) || SKIPPED_FILES.has(target)) return
    const before = fs.readFileSync(file, 'utf8')
    const after = brandText(before)
    if (after === before) return
    keep(target)
    fs.writeFileSync(file, after)
    texts += 1
  }, SKIPPED_DIRS)

  if (assets === 0 || texts === 0) {
    restore()
    throw new Error(`nothing to brand (assets: ${assets}, shell files: ${texts}); the layout has changed`)
  }
  console.log(`brand: replaced ${assets} asset(s), respelled ${texts} shell file(s)`)
}

function restore() {
  if (!fs.existsSync(MANIFEST)) return
  const changed = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  for (const target of changed) {
    const copy = path.join(BACKUP, target)
    if (fs.existsSync(copy)) fs.copyFileSync(copy, path.join(ROOT, target))
  }
  fs.rmSync(BACKUP, { recursive: true, force: true })
  console.log(`brand: restored ${changed.length} file(s)`)
}

const command = process.argv[2]
if (command === 'apply') apply()
else if (command === 'restore') restore()
else {
  console.error('usage: brand.mjs apply|restore')
  process.exit(2)
}
