/**
 * Sao Khuê wording for the dashboard catalogs.
 *
 * The core catalogs under website/src/i18n/locales stay byte-identical to
 * upstream. What this edition says differently is applied here, to each
 * catalog as i18next registers it:
 *
 *   - `{{productName}}` renders "Sao Khuê" (the core's own setter);
 *   - the values that still spell the product name, the data home or its
 *     environment variable literally are respelled;
 *   - a short list of keys that name the bot "Kiro" name it "Sao Khuê";
 *   - a few keys carry this edition's own text.
 */
import { i18next, setProductName } from '@/i18n'

export const PRODUCT_NAME = 'Sao Khuê'

const NAMESPACE = 'translation'

/** Literal spellings replaced in every catalog value. */
const LITERALS: ReadonlyArray<readonly [string, string]> = [
  ['Kiro Crew', PRODUCT_NAME],
  ['~/.kiro/crew', '~/.vpbank/saokhue'],
  ['$KIROCREW_HOME', '$SAOKHUE_HOME'],
]

/** Keys whose "Kiro" is the bot's name, not the Kiro CLI or a Kiro account. */
const BOT_NAME_KEYS: ReadonlySet<string> = new Set([
  'components.onboardingFlow.answers_set_how_kiro_explains_things_plain_langu',
  'components.onboardingFlow.tell_kiro_about_you',
  'hooks.useSceneInteraction.kiro_is_working',
  'pages.settings.chatPanel.kiro_matches_vocabulary_and_examples_to_your_pro',
  'pages.settings.chatPanel.kiro_quotes_this_back_to_itself_when_calibrating',
])

// "Kiro" not followed by a Latin letter, so "Kiro가" matches and "KiroCrew" does not.
const BOT_NAME = /Kiro(?![A-Za-z])/g

/** Keys this edition words itself, the same in every language. */
const OVERRIDES: Readonly<Record<string, string>> = {
  'pages.settings.aboutPanel.autonomous_agent_management_runs_locally_open_so':
    'Nền tảng quản trị AI Agent tự hành của VPBank — phát triển kế thừa (fork) từ KiroCrew theo giấy phép Apache 2.0',
  'pages.settings.aboutPanel.open_source_under_the_apache_2_0_license':
    'Bản fork từ KiroCrew theo giấy phép bản quyền Apache 2.0',
}

type Tree = { [key: string]: unknown }

/** This edition's wording of one catalog value. */
export function rebrandValue(key: string, value: string): string {
  let out = value
  for (const [from, to] of LITERALS) out = out.split(from).join(to)
  if (BOT_NAME_KEYS.has(key)) out = out.replace(BOT_NAME, PRODUCT_NAME)
  return out
}

/** The leaves of `tree` this edition words differently, as a nested patch. */
function patchFor(tree: Tree, prefix = ''): Tree | undefined {
  let patch: Tree | undefined
  for (const [name, value] of Object.entries(tree)) {
    const key = prefix ? `${prefix}.${name}` : name
    let changed: unknown
    if (typeof value === 'string') {
      const next = rebrandValue(key, value)
      if (next !== value) changed = next
    } else if (value && typeof value === 'object') {
      changed = patchFor(value as Tree, key)
    }
    if (changed !== undefined) (patch ??= {})[name] = changed
  }
  return patch
}

function setPath(tree: Tree, key: string, value: string): void {
  const names = key.split('.')
  let node = tree
  for (const name of names.slice(0, -1)) {
    node = (node[name] ??= {}) as Tree
  }
  node[names[names.length - 1]] = value
}

function rebrandCatalog(lng: string): void {
  const bundle = i18next.getResourceBundle(lng, NAMESPACE) as Tree | undefined
  if (!bundle) return
  const patch = patchFor(bundle) ?? {}
  for (const [key, value] of Object.entries(OVERRIDES)) setPath(patch, key, value)
  // Silent: the store's own "added" event is what calls this.
  i18next.addResourceBundle(lng, NAMESPACE, patch, true, true, { silent: true })
}

let installed = false

/** Apply this edition's wording to every catalog, now and as each one loads. */
export function installBranding(): void {
  if (installed) return
  installed = true
  if (!i18next.isInitialized) setProductName(PRODUCT_NAME)
  const start = (): void => {
    for (const lng of Object.keys(i18next.store.data)) rebrandCatalog(lng)
    i18next.store.on('added', (lng: string) => rebrandCatalog(lng))
  }
  if (i18next.isInitialized) start()
  else i18next.on('initialized', start)
}
