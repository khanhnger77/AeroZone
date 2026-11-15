import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
const compile = text => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText).toString('base64')}`
const geo = compile(await readFile(new URL('../src/map/geo.ts', import.meta.url), 'utf8'))
const api = compile('export function getPoint(...args) { return globalThis.__previewPoint(...args) }')
const layerModule = compile(await readFile(new URL('../src/terrain/layers.ts', import.meta.url), 'utf8'))
const source = (await readFile(new URL('../src/services/terrainPreview.ts', import.meta.url), 'utf8'))
  .replace("from '../terrain/layers'", `from '${layerModule}'`).replace("from './api'", `from '${api}'`).replace("from '../map/geo'", `from '${geo}'`)
const { loadTerrainPreview } = await import(compile(source))
const { MARS_RADIUS_M } = await import(geo)
globalThis.__previewPoint = () => { throw new Error('Mock required') }

test('measured mesh uses Mars ground spacing, bounded concurrency and a reusable cache', async t => {
  const point = { lon: 77.4, lat: 18.46 }, progress = []
  let calls = 0, active = 0, peak = 0
  const seen = []
  t.mock.method(globalThis, '__previewPoint', async p => {
    calls++; active++; peak = Math.max(peak, active); seen.push(p)
    await new Promise(resolve => setTimeout(resolve, 1)); active--
    const x = (p.lon - point.lon) * Math.PI / 180 * MARS_RADIUS_M * Math.cos(point.lat * Math.PI / 180)
    const y = (p.lat - point.lat) * Math.PI / 180 * MARS_RADIUS_M
    return { inside: true, elevation_m: -2500 + x * .1 + y * .2 }
  })
  const controller = new AbortController()
  const result = await loadTerrainPreview(point, controller.signal, v => progress.push(v))
  assert.equal(calls, 289); assert(peak <= 8)
  assert.equal(result.grid, 17); assert.equal(result.span_m, 160)
  assert.equal(result.center.elevation_m, -2500)
  assert(Math.abs(result.heights[0] - (-2492)) < 1e-6)
  assert(Math.abs(result.max_m - result.min_m - 48) < 1e-6)
  assert.equal(progress.at(-1), 100)
  const cached = await loadTerrainPreview(point, controller.signal, () => {})
  assert.equal(cached, result); assert.equal(calls, 289)
  controller.abort()
  await assert.rejects(loadTerrainPreview(point, controller.signal, () => {}), { name: 'AbortError' })
})

test('outside/NoData center stops immediately instead of inventing an elevation', async t => {
  let calls = 0
  const mock = t.mock.method(globalThis, '__previewPoint', async () => { calls++; return { inside: false } })
  await assert.rejects(loadTerrainPreview({ lon: 10, lat: 10 }, new AbortController().signal, () => {}), /outside/)
  assert.equal(calls, 1)
  mock.mock.mockImplementation(async () => ({ inside: true, elevation_m: null }))
  await assert.rejects(loadTerrainPreview({ lon: 11, lat: 10 }, new AbortController().signal, () => {}), /No elevation/)
})

test('partial coverage keeps holes; cancelled loading stops outstanding workers', async t => {
  const point = { lon: 77.41, lat: 18.47 }
  const mock = t.mock.method(globalThis, '__previewPoint', async p => ({ inside: p.lon <= point.lon, elevation_m: -2500 }))
  const data = await loadTerrainPreview(point, new AbortController().signal, () => {})
  assert.equal(data.valid, 153)
  assert.equal(data.heights.filter(v => v === null).length, 136)
  const controller = new AbortController(); let calls = 0
  mock.mock.mockImplementation((p, signal) => {
    calls++
    if (calls === 1) return Promise.resolve({ inside: true, elevation_m: -2500 })
    return new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true })
    })
  })
  const pending = loadTerrainPreview({ lon: 77.42, lat: 18.48 }, controller.signal, () => {})
  await new Promise(resolve => setTimeout(resolve, 5))
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })
  assert(calls <= 9)
})

test('curated panoramas use a distance cutoff and reference nearby rover positions', async () => {
  const panoramaSource = (await readFile(new URL('../src/data/panoramas.ts', import.meta.url), 'utf8')).replace("from '../map/geo'", `from '${geo}'`)
  const { PANORAMAS, nearbyPanoramas } = await import(compile(panoramaSource))
  assert.equal(PANORAMAS.length, 3)
  assert.equal(nearbyPanoramas({ lon: 10, lat: 10 }).length, 0)
  const landing = nearbyPanoramas({ lon: 77.45088572, lat: 18.44462715 })
  assert.equal(landing[0].id, 'butler-landing'); assert.equal(landing[0].distance_m, 0)
  assert(landing.every(p => p.distance_m <= 500 && p.locationNote.includes('Approximate')))
  const provenance = JSON.parse(await readFile(new URL('../public/panoramas/sources.json', import.meta.url), 'utf8'))
  for (const site of PANORAMAS) {
    const bytes = await readFile(new URL(`../public/${site.image}`, import.meta.url))
    const { createHash } = await import('node:crypto')
    const record = provenance.find(p => p.id === site.id)
    assert.equal(createHash('sha256').update(bytes).digest('hex'), record.sha256)
    assert.equal(bytes.length, record.bytes)
  }
})

test('isolated valid pixels cannot masquerade as a connected terrain surface', async t => {
  const point = { lon: 77.43, lat: 18.49 }
  let index = 0
  t.mock.method(globalThis, '__previewPoint', async p => {
    index++
    if (index === 1) return { inside: true, elevation_m: -2500 }
    const x = (p.lon - point.lon) * Math.PI / 180 * MARS_RADIUS_M * Math.cos(point.lat * Math.PI / 180)
    const y = (p.lat - point.lat) * Math.PI / 180 * MARS_RADIUS_M
    return { inside: Math.abs(x) > 79 && Math.abs(y) > 79, elevation_m: -2500 }
  })
  await assert.rejects(loadTerrainPreview(point, new AbortController().signal, () => {}), /Not enough terrain coverage/)
})


test('preview retains measured analysis values, valid zeros and missing data without extra requests', async t => {
  const point = { lon: 77.44, lat: 18.485 }
  let calls = 0
  t.mock.method(globalThis, '__previewPoint', async p => {
    calls++
    return { inside: true, elevation_m: -2500, slope_deg: p.lon < point.lon ? null : 0, roughness_m: .12, hazard: 42 }
  })
  const data = await loadTerrainPreview(point, new AbortController().signal, () => {})
  assert.equal(calls, 289)
  assert.equal(data.layers.elevation, data.heights)
  assert(data.layers.slope.includes(0))
  assert(data.layers.slope.includes(null))
  assert(data.layers.roughness.every(value => value === .12))
  assert(data.layers.hazard.every(value => value === 42))
  const cached = await loadTerrainPreview(point, new AbortController().signal, () => {})
  assert.equal(cached, data)
  assert.equal(calls, 289)
})

test('analysis color scales use physical units, clamp extremes and distinguish missing values', async () => {
  const { layerRange, layerColor } = await import(layerModule)
  assert.deepEqual(layerRange('elevation', -2600, -2500), [-2600, -2500])
  assert.deepEqual(layerRange('slope', 0, 90), [0, 30])
  assert.deepEqual(layerRange('roughness', 0, 5), [0, .5])
  assert.deepEqual(layerRange('hazard', 0, 999), [0, 100])
  assert.deepEqual(layerColor('hazard', 150, [0, 100]), layerColor('hazard', 100, [0, 100]))
  assert.notDeepEqual(layerColor('slope', null, [0, 30]), layerColor('slope', 0, [0, 30]))
  assert(layerColor('elevation', -2500, [-2500, -2500]).every(Number.isFinite))
})
