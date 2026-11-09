/**
 * After a deploy, decide whether the failed flows are new or were already failing.
 *
 * The live flows run after every deploy and daily. A problem that waits on a data step
 * (a re-read, a publish) used to fail every deploy's run until it was fixed: one cause,
 * many red runs and emails. This script lets a deploy's run go red only for a flow that
 * did not fail in the previous run; the flows still failing become a warning. The daily
 * run and manual runs always go red on any failure, so a known problem still alerts once
 * a day until it is fixed.
 *
 * Exits 1 (red) for any new failure, and whenever it cannot tell: no list of failed
 * flows, or the GitHub API not answering.
 *
 * Usage: node e2e/live/still-failing.js <file listing this run's failed flows, one a line>
 * Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_EVENT_NAME.
 */
import fs from 'node:fs'
import { flowName } from './annotate.js'

const api = async path => {
  const response = await fetch(`https://api.github.com/repos/${process.env.GITHUB_REPOSITORY}/${path}`, {
    headers: { Authorization: `Bearer ${process.env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json' },
  })
  if (!response.ok) throw new Error(`GitHub API ${path}: HTTP ${response.status}`)
  return response.json()
}

// Flows that failed in the last finished run before this one (from its error annotations).
async function failedLastTime() {
  const { workflow_runs: runs } = await api('actions/workflows/live-flows.yml/runs?status=completed&per_page=20')
  const previous = runs.find(run => String(run.id) !== process.env.GITHUB_RUN_ID && ['success', 'failure'].includes(run.conclusion))
  if (!previous) return { run: null, names: new Set() }
  const { jobs } = await api(`actions/runs/${previous.id}/jobs`)
  const names = new Set()
  for (const job of jobs) {
    for (const note of await api(`check-runs/${job.id}/annotations?per_page=100`)) {
      if (note.annotation_level === 'failure' && note.title) names.add(flowName(note.title))
    }
  }
  return { run: previous, names }
}

async function main() {
  const file = process.argv[2]
  const failed = fs.existsSync(file)
    ? [...new Set(fs.readFileSync(file, 'utf8').split('\n').map(flowName).filter(Boolean))]
    : []
  if (failed.length === 0) {
    console.log('The flows failed, but no failed flow was listed (a crash?). Failing the run.')
    return 1
  }
  if (process.env.GITHUB_EVENT_NAME !== 'workflow_run') return 1

  const { run, names } = await failedLastTime()
  const fresh = failed.filter(name => !names.has(name))
  if (fresh.length > 0) {
    console.log(`New since the last run:\n${fresh.map(name => `- ${name}`).join('\n')}`)
    return 1
  }
  console.log(`::warning title=Still failing::${failed.length} flow(s) already failed in run ${run.html_url}: ` +
    `${failed.join('; ')}. This deploy's run stays green; the daily run fails until they pass.`)
  return 0
}

main().then(code => process.exit(code), error => {
  console.log(`Could not compare with the last run (${error.message}). Failing the run.`)
  process.exit(1)
})
