"""Download a bounded MEDA snapshot and build observed per-sol weather for M3.

Uses calibrated local ATS1 temperatures and derived pressure. No forecast, spatial
weather raster, ambient-temperature reconstruction or missing-value substitution.
Raw products stay in the ignored media-backed data directory; published summaries
include source URLs, SHA-256, timestamps, accepted sample counts and method notes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'scripts/config/meda.yaml'
RAW = ROOT / 'data/raw/weather/meda'
PUBLIC = ROOT / 'frontend/public/data/climate/meda_sols.json'


def sol_group(sol: int) -> str:
    start = 0 if sol < 90 else 90 if sol < 180 else 180 + (sol - 180) // 120 * 120
    end = 89 if start == 0 else 179 if start == 90 else start + 119
    return f'sol_{start:04d}_{end:04d}'


def download(url: str, destination: Path) -> bytes:
    if destination.is_file() and destination.stat().st_size:
        return destination.read_bytes()
    response = requests.get(url, timeout=60, headers={'User-Agent': 'mars-odyssey-hackathon/0.1'})
    response.raise_for_status()
    content = response.content
    if not content or b'<html' in content[:200].lower():
        raise ValueError(f'Unexpected content at {url}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.part')
    temporary.write_bytes(content); temporary.replace(destination)
    return content


def accepted_samples(text: str, column: str, sol: int) -> list[tuple[float, float]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or column not in reader.fieldnames or 'LMST' not in reader.fieldnames:
        raise ValueError(f'MEDA CSV lacks {column}/LMST')
    values = []
    seen = set()
    for row in reader:
        try:
            value = float(row[column])
            match = re.fullmatch(r'(\d+)M(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)', row['LMST'])
            if not math.isfinite(value) or value <= 0 or value == 999999999 or not match or int(match[1]) != sol:
                continue
            clock = row.get('SCLK', row['LMST'])
            if clock in seen:
                continue
            seen.add(clock)
            hour = int(match[2]) + int(match[3]) / 60 + float(match[4]) / 3600
            if 0 <= hour < 24:
                values.append((hour, value))
        except (ValueError, TypeError, KeyError):
            continue
    return values


def fetch_product(sol: int, product: dict, base: str) -> tuple[list[tuple[float, float]], dict]:
    url = urljoin(base, f"{product['collection']}/{sol_group(sol)}/sol_{sol:04d}/")
    folder = RAW / product['collection'] / f'sol_{sol:04d}'
    listing = folder / 'listing.html'
    if listing.exists():
        html = listing.read_text()
    else:
        response = requests.get(url, timeout=60); response.raise_for_status(); html = response.text
        folder.mkdir(parents=True, exist_ok=True); listing.write_text(html)
    files = re.findall(r'href="([^"/]+\.CSV)"', html, flags=re.I)
    token = product['token'].lower()
    files = [name for name in files if token in name.lower() and re.search(r'P\d+\.CSV$', name, re.I)]
    if not files:
        raise ValueError(f'No {token} product for sol {sol}')
    filename = max(files, key=lambda name: int(re.search(r'P(\d+)\.CSV$', name, re.I)[1]))
    csv_url = urljoin(url, filename)
    raw = download(csv_url, folder / filename)
    label_name = filename[:-4] + '.xml'
    xml_url = urljoin(url, label_name)
    label = download(xml_url, folder / label_name)
    tree = ET.fromstring(label)
    fields = [field for field in tree.findall('.//{*}Field_Delimited') if field.findtext('{*}name') == product['column']]
    if len(fields) != 1 or fields[0].findtext('{*}unit') != product['unit']:
        raise ValueError(f"Missing field or unexpected unit for {product['column']}")
    record = {'url': csv_url, 'xml_url': xml_url, 'file': str((folder / filename).relative_to(ROOT)),
              'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'column': product['column'],
              'start_utc': tree.findtext('.//{*}start_date_time'), 'end_utc': tree.findtext('.//{*}stop_date_time')}
    return accepted_samples(raw.decode('utf-8-sig'), product['column'], sol), record


def build_sol(sol: int, config: dict) -> tuple[dict | None, list[dict], str | None]:
    try:
        temperature, temp_source = fetch_product(sol, config['products']['temperature'], config['base_url'])
        pressure, p_source = fetch_product(sol, config['products']['pressure'], config['base_url'])
        if not temperature or not pressure:
            return None, [temp_source, p_source], f'Sol {sol}: no joint valid temperature/pressure coverage'
        tv, pv = [v for _, v in temperature], [v for _, v in pressure]
        hourly = []
        for hour in range(24):
            t = [v for h, v in temperature if hour <= h < hour + 1]
            p = [v for h, v in pressure if hour <= h < hour + 1]
            hourly.append({'hour': hour, 't_air_mean_k': round(statistics.fmean(t), 2) if t else None,
                           'p_mean_pa': round(statistics.fmean(p), 2) if p else None,
                           'n_temperature': len(t), 'n_pressure': len(p)})
        record = {'sol': sol, 't_air_mean_k': round(statistics.fmean(tv), 2), 't_air_min_k': round(min(tv), 2),
                  't_air_max_k': round(max(tv), 2), 'p_mean_pa': round(statistics.fmean(pv), 2),
                  'n_samples': len(tv), 'n_pressure_samples': len(pv), 'hourly': hourly,
                  'start_utc': min(temp_source['start_utc'], p_source['start_utc']),
                  'end_utc': max(temp_source['end_utc'], p_source['end_utc']),
                  'temperature_source': temp_source['url'], 'pressure_source': p_source['url']}
        return record, [temp_source, p_source], None
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return None, [], f'Sol {sol}: archive product not available'
        raise


def main() -> int:
    config = yaml.safe_load(CONFIG.read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-sol', type=int, default=config['default_start_sol'])
    parser.add_argument('--end-sol', type=int, default=config['default_end_sol'])
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.start_sol < 0 or args.end_sol < args.start_sol or args.end_sol - args.start_sol > 365:
        parser.error('Choose an ordered range of at most 366 non-negative sols')
    sols, sources, skipped = [], [], []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        for record, provenance, warning in pool.map(lambda sol: build_sol(sol, config), range(args.start_sol, args.end_sol + 1)):
            sources.extend(provenance)
            if warning:
                skipped.append(warning); print('[skip]', warning, flush=True)
            if record:
                sols.append(record); print(f"[sol {record['sol']}] {record['n_samples']} temperature / {record['n_pressure_samples']} pressure samples", flush=True)
    if not sols:
        raise RuntimeError('No usable MEDA observations. Existing published snapshot is unchanged.')
    waypoints = json.loads((ROOT / 'frontend/public/data/waypoints.geojson').read_text())['features']
    for record in sols:
        before = [f for f in waypoints if f['properties']['sol'] <= record['sol']]
        if before:
            waypoint = max(before, key=lambda f: f['properties']['sol'])
            record['location'] = {'lon': waypoint['properties']['lon'], 'lat': waypoint['properties']['lat'],
                                  'waypoint_sol': waypoint['properties']['sol'], 'method': 'last rover fix on/before measurement sol (approximate)'}
    retrieved = datetime.now(timezone.utc).isoformat(timespec='seconds')
    result = {'category': 'observation', 'source': config['source'], 'pds_release': 'MEDA calibrated ATS / derived PS archive',
              'retrieved_utc': retrieved, 'requested_range': [args.start_sol, args.end_sol], 'sols': sols, 'skipped': skipped,
              'method': {'temperature': 'Calibrated local air temperature at ATS1 (ATS_LOCAL_TEMP1), kelvin.',
                         'pressure': 'Archive derived PRESSURE column, pascals.',
                         'aggregation': 'Arithmetic mean of accepted samples; min/max of accepted ATS1 observations. Not a full-sol time-weighted mean.',
                         'quality': 'PDS missing-value code 999999999, blank, non-finite and non-positive values omitted; LMST sol checked and repeated SCLK records deduplicated. Selected ATS/PS products have no per-sample quality-flag columns.',
                         'coverage': 'Historical observations at the rover, not a forecast or a weather map. Gaps are not filled.'}}
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    temporary = PUBLIC.with_suffix('.json.part'); temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))); temporary.replace(PUBLIC)
    metadata = ROOT / 'data/metadata/meda_sources.json'
    metadata.write_text(json.dumps({'source': config['source'], 'document': config['document'], 'retrieved_utc': retrieved, 'products': sources}, indent=2))
    print(f'Published {len(sols)} sols -> {PUBLIC}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
