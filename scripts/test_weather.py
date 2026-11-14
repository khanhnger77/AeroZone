"""Weather quality checks independent of the Planner API and network."""
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('weather_builder', Path(__file__).with_name('build_weather.py'))
weather = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weather)


def test_samples_filter_missing_wrong_sol_duplicates_and_nonfinite():
    csv = '''SCLK,LMST,ATS_LOCAL_TEMP1
1,01215M00:30:00.000,220
1,01215M00:30:00.000,999
2,01214M23:59:00.000,200
3,01215M01:00:00.000,
4,01215M02:00:00.000,NaN
5,01215M03:00:00.000,-9999
6,01215M04:00:00.000,230
7,01215M24:00:00.000,240
8,01215M05:00:00.000,999999999
'''
    assert weather.accepted_samples(csv, 'ATS_LOCAL_TEMP1', 1215) == [(0.5, 220), (4.0, 230)]


def test_aggregation_preserves_hourly_gaps_and_sample_counts():
    source = {'start_utc': '2024-07-20T00:00:00Z', 'end_utc': '2024-07-21T00:00:00Z', 'url': 'https://example.test/data.csv'}
    with patch.object(weather, 'fetch_product', side_effect=[([(0.5, 200), (0.8, 220), (2.0, 240)], source), ([(0.2, 700), (2.5, 800)], source)]):
        row, _, warning = weather.build_sol(1215, {'products': {'temperature': {}, 'pressure': {}}, 'base_url': ''})
    assert warning is None
    assert row['t_air_mean_k'] == 220
    assert row['p_mean_pa'] == 750
    assert row['n_samples'] == 3
    assert row['hourly'][0]['t_air_mean_k'] == 210
    assert row['hourly'][1]['t_air_mean_k'] is None
    assert row['hourly'][1]['p_mean_pa'] is None
    assert row['hourly'][1]['n_temperature'] == 0


def test_no_valid_measurements_are_not_replaced():
    with patch.object(weather, 'fetch_product', return_value=([], {})):
        row, _, warning = weather.build_sol(1215, {'products': {'temperature': {}, 'pressure': {}}, 'base_url': ''})
    assert row is None
    assert 'no joint valid' in warning


def test_published_source_metadata_has_one_pair_per_sol():
    data = json.loads(weather.PUBLIC.read_text())
    sources = json.loads((weather.ROOT / 'data/metadata/meda_sources.json').read_text())['products']
    assert len(sources) == len(data['sols']) * 2 == 60
    by_url = {source['url']: source for source in sources}
    for row in data['sols']:
        for field in ['temperature_source', 'pressure_source']:
            source = by_url[row[field]]
            assert len(source['sha256']) == 64
            raw = weather.ROOT / source['file']
            if raw.exists():
                assert hashlib.sha256(raw.read_bytes()).hexdigest() == source['sha256']
                assert raw.stat().st_size == source['bytes']


def test_published_values_exclude_pds_missing_code():
    data = json.loads(weather.PUBLIC.read_text())
    for row in data['sols']:
        # Broad physical sanity bounds catch leaked archive sentinel values.
        assert 100 < row['t_air_min_k'] <= row['t_air_mean_k'] <= row['t_air_max_k'] < 400
        assert 100 < row['p_mean_pa'] < 2000
        assert all(h['p_mean_pa'] is None or 100 < h['p_mean_pa'] < 2000 for h in row['hourly'])
