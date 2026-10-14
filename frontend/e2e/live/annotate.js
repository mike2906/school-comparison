/**
 * A node:test reporter that turns each failed flow into a GitHub Actions error
 * annotation, so the run's page names the broken journey and why without opening the log.
 * Used beside the usual reporter: `--test-reporter=spec --test-reporter=./e2e/live/annotate.js`.
 */
// Workflow commands decode %XX, and a title also ends at "," or ":" (actions/toolkit escapeData, escapeProperty).
const clean = text => String(text).replace(/\s+/g, ' ').trim().slice(0, 900).replace(/%/g, '%25')
const property = text => clean(text).replace(/:/g, '%3A').replace(/,/g, '%2C')

export default async function* annotate(source) {
  for await (const event of source) {
    if (event.type !== 'test:fail' || event.data.details?.type === 'suite') continue
    const error = event.data.details?.error
    const cause = error?.cause ?? error
    yield `::error title=${property(event.data.name)}::${clean(cause?.message ?? cause)}\n`
  }
}
