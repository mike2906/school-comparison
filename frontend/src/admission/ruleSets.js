/**
 * Every rule file, bundled with the calculator page. The rules ship to the browser so a
 * parent's answers never have to leave it (docs/ADMISSION_POINTS.md, decision 7).
 * Vite-only: tests read the files from disk instead.
 */
const modules = import.meta.glob('./rules/**/*.json', { eager: true, import: 'default' })

export const RULE_SETS = Object.values(modules)
