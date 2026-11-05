#!/usr/bin/env node
/**
 * Generate the settings registry from settings panel source files.
 * Usage: node scripts/gen-settings-registry.mjs
 */
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'
import { runnerImport } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')

const { module: extractor } = await runnerImport(
  path.join(ROOT, 'scripts/settingsExtract.ts'),
  { configFile: false, logLevel: 'error', root: ROOT },
)

const { entries, skipped } = extractor.extractAll(extractor.panelRoots(path.resolve(ROOT, 'src')))

const FLOOR = 90
if (entries.length < FLOOR) {
  console.error(
    `Refusing to write registry: extracted only ${entries.length} entries ` +
    `(floor ${FLOOR}, ${skipped} skipped).`
  )
  process.exit(1)
}

const outPath = path.resolve(ROOT, 'src/components/commandPalette/settingsRegistry.gen.ts')
const source = extractor.generateRegistrySource(entries)
fs.writeFileSync(outPath, source)

const agentOutPath = path.resolve(
  ROOT,
  '../src/kiro_crew/docs/settings-registry.generated.json',
)
fs.writeFileSync(agentOutPath, extractor.generateAgentRegistryJson(entries))

console.log(`Generated ${entries.length} entries (${skipped} dynamic labels skipped) → settingsRegistry.gen.ts + settings-registry.generated.json`)
