/**
 * A node:test reporter that turns each failed flow into a GitHub Actions error
 * annotation, so the run's page names the broken journey and why without opening the log.
 * Used beside the usual reporter: `--test-reporter=spec --test-reporter=./e2e/live/annotate.js`.
 */
const clean = text => String(text).replace(/\r?\n/g, ' ').replace(/\s+/g, ' ').slice(0, 900)

export default async function* annotate(source) {
  for await (const event of source) {
    if (event.type !== 'test:fail' || event.data.details?.type === 'suite') continue
    const error = event.data.details?.error
    const cause = error?.cause ?? error
    yield `::error title=${clean(event.data.name).replace(/[:,]/g, ' ')}::${clean(cause?.message ?? cause)}\n`
  }
}
