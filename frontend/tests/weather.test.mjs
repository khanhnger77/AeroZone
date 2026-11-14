import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/services/weather.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText
const { parseWeather, kelvinToCelsius, loadWeather } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
const snapshot = JSON.parse(await readFile(new URL('../public/data/climate/meda_sols.json', import.meta.url), 'utf8'))

test('bundled MEDA snapshot contains observed, bounded and ordered historical sols', () => {
  const data = parseWeather(snapshot)
  assert.equal(data.category, 'observation')
  assert.equal(data.sols.length, 30)
  assert.deepEqual(data.requested_range, [1186, 1215])
  assert.equal(data.sols.at(-1).p_mean_pa, 744.72)
  assert(Math.abs(kelvinToCelsius(data.sols.at(-1).t_air_mean_k) + 51.62) < 1e-8)
  for (const row of data.sols) {
    assert(row.p_mean_pa > 100 && row.p_mean_pa < 2000)
    assert.equal(row.hourly.reduce((n, h) => n + h.n_temperature, 0), row.n_samples)
    assert.equal(row.hourly.reduce((n, h) => n + h.n_pressure, 0), row.n_pressure_samples)
    assert(row.temperature_source.startsWith('https://atmos.nmsu.edu/'))
    assert(row.location.waypoint_sol <= row.sol)
  }
})

test('missing observations remain absent and hourly gaps remain null', () => {
  const value = structuredClone(snapshot)
  value.sols.splice(1, 1)
  value.sols[0].hourly[0].t_air_mean_k = null
  value.sols[0].hourly[0].n_temperature = 0
  const result = parseWeather(value)
  assert.equal(result.sols.find(s => s.sol === 1187), undefined)
  assert.equal(result.sols[0].hourly[0].t_air_mean_k, null)
})

test('invalid units, statistics, ordering and unbounded ranges fail visibly', () => {
  for (const mutate of [
    data => { data.sols[0].t_air_mean_k = -50 },
    data => { data.sols[0].p_mean_pa = NaN },
    data => { data.sols[0].t_air_min_k = 1000 },
    data => { data.sols[1].sol = data.sols[0].sol },
    data => { data.requested_range[1] = 1e9 },
    data => { data.sols[0].hourly[0].hour = 99 },
    data => { data.category = 'forecast' },
  ]) {
    const value = structuredClone(snapshot); mutate(value)
    assert.throws(() => parseWeather(value), /invalid/)
  }
})

test('missing or HTML fallback responses produce a recoverable error', async t => {
  const mock = t.mock.method(globalThis, 'fetch', async () => new Response('<html>app</html>', { status: 200 }))
  await assert.rejects(loadWeather(new AbortController().signal), /not available/)
  mock.mock.mockImplementation(async () => new Response('', { status: 404 }))
  await assert.rejects(loadWeather(new AbortController().signal), /not available/)
})
