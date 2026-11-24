/**
 * Turns failed live flows into GitHub issues instead of red runs and emails.
 *
 * A red run emails the repository owner, and one cause used to email every day until it
 * was fixed (the required-extras flow, 14-23 Nov 2026). Now the run stays green and:
 * - a flow that failed in this daily run and in the daily run before it opens one issue,
 *   labelled `live-check`, which a scheduled session picks up and fixes;
 * - a flow still failing on an open issue adds nothing (no daily comment);
 * - an open issue whose flow passed in a daily run is closed with a link to that run.
 * One bad day (a slow edge, OpenStreetMap refusing a runner) opens nothing.
 * Runs after a deploy only annotate: the thread that deployed reads its own run, and a
 * failure that lasts reaches the daily runs.
 *
 * A run that cannot list its failed flows (a crash) counts as the flow CRASHED, so a crash
 * that lasts opens an issue too. It exits 1 (red) only when GitHub's API does not answer.
 *
 * It always writes this run's failed flows as one notice titled "Failed flows" (JSON), so
 * the next run reads the whole list even when GitHub keeps only the first 10 errors.
 *
 * Usage: node e2e/live/issues.js <file listing this run's failed flows, one a line>
 *   (no file, or no LIVE_FAILED_LIST, when every flow passed the first try)
 * Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_EVENT_NAME, LIVE_FLOWS_OUTCOME.
 */
import fs from 'node:fs'
import { END_OF_RUN, flowName } from './annotate.js'

const LIST_TITLE = 'Failed flows'
export const LABEL = 'live-check'
export const CRASHED = 'the live flows did not finish (a crash or a hook failed)'
const TITLE_PREFIX = 'Live check failing: '

const repo = process.env.GITHUB_REPOSITORY
const runUrl = `https://github.com/${repo}/actions/runs/${process.env.GITHUB_RUN_ID}`

async function api(path, { method = 'GET', body } = {}) {
  const response = await fetch(`https://api.github.com/repos/${repo}/${path}`, {
    method,
    headers: { Authorization: `Bearer ${process.env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json' },
    body: body && JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`GitHub API ${method} ${path}: HTTP ${response.status}`)
  return response.json()
}

/** This run's failed flows, from the reporter's list (see annotate.js). */
export function failedFlows(text, outcome) {
  if (outcome === 'success') return []
  const lines = (text || '').split('\n').map(flowName).filter(Boolean)
  const failed = [...new Set(lines.filter(line => line !== END_OF_RUN))]
  // A failed step with no complete list: the run crashed before naming what broke.
  if (!lines.includes(END_OF_RUN) || failed.length === 0) return [...failed, CRASHED]
  return failed
}

/** The flows a finished run failed, from its "Failed flows" notice or its error titles. */
async function failedIn(run) {
  const names = new Set()
  if (run.conclusion === 'success' || run.conclusion === 'failure') {
    for (const job of (await api(`actions/runs/${run.id}/jobs`)).jobs) {
      for (const note of await api(`check-runs/${job.id}/annotations?per_page=100`)) {
        if (note.title === LIST_TITLE) JSON.parse(note.message).forEach(name => names.add(flowName(name)))
        else if (note.annotation_level === 'failure' && note.title) names.add(flowName(note.title))
      }
    }
  }
  // A red run naming no flow (an install step, a crash before the list) was a crash.
  if (run.conclusion === 'failure' && names.size === 0) names.add(CRASHED)
  return names
}

/** The daily run before this one, or null. Manual runs (a branch, a preview) never count. */
async function previousDaily() {
  const { workflow_runs: runs } = await api('actions/workflows/live-flows.yml/runs?status=completed&branch=main&event=schedule&per_page=10')
  return runs.find(run => String(run.id) !== process.env.GITHUB_RUN_ID) || null
}

const openIssues = async () => (await api(`issues?state=open&labels=${LABEL}&per_page=100`)).filter(issue => !issue.pull_request)
export const flowOf = issue => (issue.title.startsWith(TITLE_PREFIX) ? flowName(issue.title.slice(TITLE_PREFIX.length)) : null)

/** What to open and close, from this run's failures, the last daily run's and the open issues. */
export function plan(failed, failedBefore, issues) {
  const open = new Map(issues.map(issue => [flowOf(issue), issue]))
  return {
    toOpen: failed.filter(name => failedBefore.has(name) && !open.has(name)),
    toClose: issues.filter(issue => flowOf(issue) && !failed.includes(flowOf(issue))),
  }
}

const issueBody = (name, previous) => `The live flow **${name}** failed in two daily runs in a row:
- ${previous.html_url}
- ${runUrl}

The failing check's message is an annotation on each run's page (Summary, "Annotations"). Find the root cause: a real site bug, a data change, or a test that reads the page or the data too narrowly. Fix it with a PR, and say which in the PR.

This issue closes itself when the flow passes in a daily run. Opened by \`frontend/e2e/live/issues.js\`.`

async function main() {
  const file = process.argv[2]
  const failed = failedFlows(file && fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '', process.env.LIVE_FLOWS_OUTCOME)
  console.log(`::notice title=${LIST_TITLE}::${JSON.stringify(failed).replace(/%/g, '%25')}`)
  if (failed.length > 0) console.log(`::warning title=Live flows failed::${failed.join('; ').replace(/%/g, '%25')}`)
  // After a deploy, the annotations are the report; issues follow the daily runs only.
  if (process.env.GITHUB_EVENT_NAME !== 'schedule') return 0

  const previous = await previousDaily()
  const failedBefore = previous ? await failedIn(previous) : new Set()
  const { toOpen, toClose } = plan(failed, failedBefore, await openIssues())
  for (const name of toOpen) {
    const issue = await api('issues', { method: 'POST', body: { title: `${TITLE_PREFIX}${name}`, body: issueBody(name, previous), labels: [LABEL] } })
    console.log(`Opened ${issue.html_url}`)
  }
  for (const issue of toClose) {
    await api(`issues/${issue.number}/comments`, { method: 'POST', body: { body: `The flow passed in the daily run ${runUrl}. Closing.` } })
    await api(`issues/${issue.number}`, { method: 'PATCH', body: { state: 'closed', state_reason: 'completed' } })
    console.log(`Closed ${issue.html_url}`)
  }
  return 0
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().then(code => process.exit(code), error => {
    console.log(`::error title=Live flow issues::Could not read or write issues (${error.message}).`)
    process.exit(1)
  })
}
