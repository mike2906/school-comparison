import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { decodeGraph, snapToRoad, searchFrom, secondsTo } from './roadGraph.js'

// Built from tools/road-graph/tiny.osm (map in that file). Residential roads at 25 km/h:
// a grid step east is ~82 m (~11.8 s), a step north ~111 m (~16 s).
const file = readFileSync(new URL('./testdata/tiny.graph', import.meta.url))
const graph = decodeGraph(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength))

const node = {
  1: [23.300, 42.700], 2: [23.301, 42.700], 3: [23.302, 42.700],
  4: [23.300, 42.701], 5: [23.301, 42.701], 6: [23.302, 42.701],
  7: [23.300, 42.702], 8: [23.301, 42.702], 9: [23.302, 42.702], 20: [23.302, 42.7005],
  40: [23.304, 42.700], 41: [23.304, 42.701], 42: [23.304, 42.702], 43: [23.305, 42.701],
}
const between = (a, b, f = 0.5) => [node[a][0] + (node[b][0] - node[a][0]) * f, node[a][1] + (node[b][1] - node[a][1]) * f]
const seconds = (from, to) => {
  const origin = snapToRoad(graph, ...from)
  return secondsTo(graph, searchFrom(graph, origin), snapToRoad(graph, ...to))
}

test('rejects a file that is not a road graph', () => {
  assert.throws(() => decodeGraph(new ArrayBuffer(64)), /Unknown road graph format/)
})

test('snaps to the nearest road and reports the distance', () => {
  const snapped = snapToRoad(graph, 23.3005, 42.70027)
  assert.ok(Math.abs(snapped.metres - 30) < 3, `snapped ${snapped.metres} m away`)
  assert.ok(Math.abs(snapped.frac - 0.5) < 0.05)
})

test('drives straight along a road', () => {
  const s = seconds(between(1, 2), between(2, 3))
  assert.ok(s > 11 && s < 13, `${s} s`)
})

test('a destination further along the same road is reached directly', () => {
  const s = seconds(between(1, 2, 0.2), between(1, 2, 0.8))
  assert.ok(s > 6.5 && s < 7.5, `${s} s`)
})

test('one-way roads are driven only one way', () => {
  const withFlow = seconds(between(7, 8), between(8, 9))
  const against = seconds(between(8, 9), between(7, 8))
  assert.ok(withFlow < 13, `with the flow ${withFlow} s`)
  assert.ok(against > 40, `against the flow ${against} s`)
})

test('a turn restriction via a node applies only to its from road', () => {
  const banned = seconds(between(1, 4), between(4, 5))
  const allowed = seconds(between(4, 7), between(4, 5))
  assert.ok(allowed < 25, `left turn from the north ${allowed} s`)
  assert.ok(banned > 30, `right turn from the south should be banned, got ${banned} s`)
})

test('a turn restriction via a way applies only to traffic that entered from its from road', () => {
  const banned = seconds(between(1, 2), between(5, 6))
  const allowed = seconds(between(2, 5), between(5, 6))
  assert.ok(allowed < 25, `starting on the via road ${allowed} s`)
  assert.ok(banned > 60 && Number.isFinite(banned), `from the bottom row ${banned} s`)
})

test('an only-restriction via a way forbids leaving the via way at a junction on it', () => {
  const banned = seconds(between(3, 40), between(41, 43))
  const allowed = seconds(between(40, 41), between(41, 43))
  assert.ok(allowed < 20, `starting on the via road ${allowed} s`)
  assert.ok(banned > allowed + 30 && Number.isFinite(banned), `from 3-40 ${banned} s`)
})

test('a bollard stops through-traffic but its road stays reachable from both sides', () => {
  const across = seconds(between(3, 20), between(20, 6))
  assert.ok(across > 30 && Number.isFinite(across), `across the bollard ${across} s`)
  assert.ok(Number.isFinite(seconds(between(1, 2), between(3, 20))))
  assert.ok(Number.isFinite(seconds(between(1, 2), between(20, 6))))
})
