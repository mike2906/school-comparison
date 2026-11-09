/**
 * A node:test reporter that turns each failed flow into a GitHub Actions error
 * annotation, so the run's page names the broken journey and why without opening the log.
 * A flow's own diagnostics (`testContext.diagnostic`, such as timings) become notices.
 * Failures are annotated only on the last try (LIVE_LAST_TRY=1), so one a retry fixes
 * does not mark a green run.
 * On the last try it also lists each failed flow's name in LIVE_FAILED_LIST, if set, for
 * still-failing.js, and ends the list with END_OF_RUN once the run finishes, so a list cut
 * short by a crash is not mistaken for the whole story. A suite that fails for its own
 * reason (a hook), not because a flow in it failed, is listed and annotated too.
 * Used beside the usual reporter: `--test-reporter=spec --test-reporter=./e2e/live/annotate.js`.
 */
import fs from 'node:fs'

// A flow's name as an annotation title shows it, so this run's list compares with an earlier run's titles.
export const flowName = text => String(text).replace(/\s+/g, ' ').trim().slice(0, 900)
export const END_OF_RUN = '-- end of run --'

// Workflow commands decode %XX, and a title also ends at "," or ":" (actions/toolkit escapeData, escapeProperty).
const clean = text => flowName(text).replace(/%/g, '%25')
const property = text => clean(text).replace(/:/g, '%3A').replace(/,/g, '%2C')
const list = line => process.env.LIVE_FAILED_LIST && fs.appendFileSync(process.env.LIVE_FAILED_LIST, `${line}\n`)

const RUN_COUNTS = /^(tests|suites|pass|fail|cancelled|skipped|todo|duration_ms) [\d.]+$/

export default async function* annotate(source) {
  for await (const event of source) {
    if (event.type === 'test:diagnostic') {
      // The run's closing counts ("tests 12", "duration_ms 3.2") are diagnostics too.
      if (!RUN_COUNTS.test(event.data.message)) yield `::notice title=Live flow::${clean(event.data.message)}\n`
      continue
    }
    if (!process.env.LIVE_LAST_TRY || event.type !== 'test:fail') continue
    const error = event.data.details?.error
    // A suite fails whenever one of its flows does; that flow has its own line already.
    if (event.data.details?.type === 'suite' && error?.failureType === 'subtestsFailed') continue
    const cause = error?.cause ?? error
    list(flowName(event.data.name))
    yield `::error title=${property(event.data.name)}::${clean(cause?.message ?? cause)}\n`
  }
  if (process.env.LIVE_LAST_TRY) list(END_OF_RUN)
}
