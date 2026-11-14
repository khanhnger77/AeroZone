export interface MedaHour { hour: number; t_air_mean_k: number | null; p_mean_pa: number | null; n_temperature: number; n_pressure: number }
export interface MedaSol {
  sol: number; t_air_mean_k: number; t_air_min_k: number; t_air_max_k: number; p_mean_pa: number
  n_samples: number; n_pressure_samples: number; start_utc: string; end_utc: string; hourly: MedaHour[]
  temperature_source: string; pressure_source: string
  location?: { lon: number; lat: number; waypoint_sol: number; method: string }
}
export interface MedaWeather {
  category: 'observation'; source: string; pds_release: string; retrieved_utc: string
  requested_range: [number, number]; sols: MedaSol[]; skipped: string[]
  method: { temperature: string; pressure: string; aggregation: string; quality: string; coverage: string }
}
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

export function parseWeather(value: unknown): MedaWeather {
  const invalid = () => { throw new Error('Weather snapshot is invalid. Rebuild it from the MEDA archive.') }
  if (!object(value) || value.category !== 'observation' || !Array.isArray(value.sols) || !value.sols.length || typeof value.source !== 'string' || typeof value.retrieved_utc !== 'string' || !object(value.method) || !Array.isArray(value.requested_range) || value.requested_range.length !== 2 || !value.requested_range.every(finite)) return invalid()
  const [start, end] = value.requested_range
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end - start > 365 || !Number.isFinite(Date.parse(value.retrieved_utc)) || !Array.isArray(value.skipped) || !value.skipped.every(item => typeof item === 'string') || !Object.values(value.method).every(item => typeof item === 'string')) return invalid()
  let previous = -1
  for (const row of value.sols) {
    if (!object(row) || !Number.isInteger(row.sol) || !finite(row.sol) || row.sol <= previous || row.sol < value.requested_range[0] || row.sol > value.requested_range[1]) return invalid()
    for (const key of ['t_air_mean_k', 't_air_min_k', 't_air_max_k', 'p_mean_pa', 'n_samples', 'n_pressure_samples']) if (!finite(row[key]) || row[key] <= 0) return invalid()
    if ((row.t_air_min_k as number) > (row.t_air_mean_k as number) || (row.t_air_mean_k as number) > (row.t_air_max_k as number) || typeof row.start_utc !== 'string' || !Number.isFinite(Date.parse(row.start_utc)) || typeof row.end_utc !== 'string' || !Number.isFinite(Date.parse(row.end_utc)) || !Array.isArray(row.hourly) || row.hourly.length !== 24) return invalid()
    for (let i = 0; i < 24; i++) {
      const hour = row.hourly[i]
      if (!object(hour) || hour.hour !== i || !['t_air_mean_k', 'p_mean_pa'].every(key => hour[key] === null || (finite(hour[key]) && hour[key] > 0))) return invalid()
    }
    previous = row.sol
  }
  return value as unknown as MedaWeather
}

export async function loadWeather(signal: AbortSignal): Promise<MedaWeather> {
  const response = await fetch('data/climate/meda_sols.json', { signal })
  if (!response.ok) throw new Error('Weather data is not available yet. Run the MEDA data task and retry.')
  let value: unknown
  try { value = await response.json() } catch { throw new Error('Weather data is not available yet. Run the MEDA data task and retry.') }
  return parseWeather(value)
}
export const kelvinToCelsius = (value: number) => value - 273.15
