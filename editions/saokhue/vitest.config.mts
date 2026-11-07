/**
 * Runs the edition's tests with the core's test setup (happy-dom, the i18n
 * init, msw) by reusing website/vite.config.ts and pointing it at this folder.
 *
 *   cd website && npx vitest run --config ../editions/saokhue/vitest.config.mts
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { UserConfig } from 'vite'
import base from '../../website/vite.config.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const website = path.resolve(here, '../../website')
const core = base as UserConfig & { test?: Record<string, unknown> }

export default {
  ...core,
  root: website,
  server: { ...core.server, fs: { ...core.server?.fs, allow: [website, here] } },
  test: {
    ...core.test,
    dir: here,
    include: ['**/*.test.{ts,tsx}'],
    exclude: ['**/node_modules/**'],
    coverage: { enabled: false },
  },
}
