import { haversineM } from '../map/geo'
import type { LonLat } from '../types'

export interface PanoramaSite extends LonLat {
  id: string; name: string; sols: string; image: string; waypointSol: number
  locationNote: string; source: string
}
export const PANORAMA_RADIUS_M = 500
export const PANORAMAS: PanoramaSite[] = [
  { id: 'butler-landing', name: 'Octavia E. Butler Landing', sols: '3–11', image: 'panoramas/butler-landing.jpg',
    lon: 77.45088572, lat: 18.44462715, waypointSol: 13,
    locationNote: 'Approximate location: nearby rover fix on sol 13; panorama spans sols 3–11.',
    source: 'https://mastcamz.asu.edu/mastcam-zs-360-panorama-collection/' },
  { id: 'van-zyl', name: 'Van Zyl Overlook', sols: '53–64', image: 'panoramas/van-zyl.jpg',
    lon: 77.45213981, lat: 18.44475241, waypointSol: 52,
    locationNote: 'Approximate location: last rover fix before imaging, sol 52.',
    source: 'https://mastcamz.asu.edu/mastcam-zs-360-panorama-collection/' },
  { id: 'bright-angel', name: 'Bright Angel Outcrop', sols: '1178', image: 'panoramas/bright-angel.jpg',
    lon: 77.30383571, lat: 18.49611249, waypointSol: 1177,
    locationNote: 'Approximate location: last rover fix before imaging, sol 1177.',
    source: 'https://www.jpl.nasa.gov/images/pia26369-perseverance-captures-bright-angel-in-360-degrees/' },
]
export function nearbyPanoramas(point: LonLat) {
  return PANORAMAS.map(site => ({ ...site, distance_m: haversineM(point.lon, point.lat, site.lon, site.lat) }))
    .filter(site => site.distance_m <= PANORAMA_RADIUS_M).sort((a, b) => a.distance_m - b.distance_m)
}
