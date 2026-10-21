"""Lossless metadata projection; the full pinned-input proof lives with the handoff."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from otc_pipeline import original_metadata
from otc_pipeline.original_metadata import load_original_metadata
from otc_pipeline.source_inventory import SourceInventory


def inventory(tmp_path, payloads, identities):
    descriptors = []
    for name, doc in payloads.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(doc).encode()
        path.write_bytes(raw)
        descriptors.append({'path': str(path), 'size': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest()})
    return SourceInventory({'input_files': descriptors, 'identities': identities}, {})


def identity(rid, version='v1'):
    return {'source': 'dmi', 'version': version, 'upstream_record_id': rid,
            'granularity': 'gauge_station', 'input_sha256': ['a' * 64]}


@pytest.fixture
def noaa_raw_datums(tmp_path):
    # Transport/preservation fixture only; actual agency-cache proof is separate.
    url = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/8660754/datums.json'
    body = {'self': url, 'units': 'meters', 'LAT': 1.0, 'HAT': 2.0,
            'datums': [{'name': 'STND', 'value': 0.0}]}
    path = tmp_path / '8660754.json'
    path.write_text(json.dumps(body))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = Path(str(path) + '.meta.json')
    receipt.write_text(json.dumps({'url': url + '?units=metric', 'sha256': sha}))
    record = {'source': 'noaa', 'source_record_id': '8660754', 'source_version': '20261008',
              'provenance': {'input_sha256': ['a' * 64], 'inventory_sha256': 'b' * 64},
              'original_metadata': {'raw_metadata_rows_json': '[{"id":"8660754"}]',
                                    'metadata_inputs_json': '[]'}}
    station = {'station_id': 'OTC-d14a171f', 'aliases': {'noaa': '8660754'},
               'name': 'Carolina Forest, ICWW', 'lat': 33.734901428222656, 'lon': -78.88269805908203,
               'constant_sets': [], 'recommended_set_id': None, 'source_records': [record]}
    return {'stations': [station]}, path, sha, receipt


def test_cached_noaa_datums_preserve_original_record_and_bind_later_response(noaa_raw_datums):
    release, path, sha, receipt = noaa_raw_datums
    before = copy.deepcopy(release)
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    original_metadata.append_noaa_8660754_datums(release, path, sha, receipt_sha)
    record = release['stations'][0]['source_records'][0]
    metadata = record['original_metadata']
    assert json.loads(metadata['raw_metadata_rows_json']) == [
        {'id': '8660754'}, json.loads(path.read_text())]
    assert json.loads(metadata['metadata_inputs_json']) == [
        {'path': str(p.resolve()), 'size': p.stat().st_size, 'sha256': digest}
        for p, digest in ((path, sha), (receipt, receipt_sha))]
    restored = copy.deepcopy(release)
    restored['stations'][0]['source_records'][0]['original_metadata'] = before['stations'][0]['source_records'][0]['original_metadata']
    assert restored == before
    once = copy.deepcopy(release)
    original_metadata.append_noaa_8660754_datums(release, path, sha, receipt_sha)
    assert release == once


@pytest.mark.parametrize('case', ['body_hash', 'receipt_hash', 'endpoint', 'identity', 'predictor'])
def test_cached_noaa_datums_refuse_unbound_or_wrong_target_without_mutation(noaa_raw_datums, case):
    release, path, sha, receipt = noaa_raw_datums
    if case == 'body_hash':
        sha = '0' * 64
    elif case == 'endpoint':
        data = json.loads(receipt.read_text())
        data['url'] = data['url'].replace('8660754', '8443970')
        receipt.write_text(json.dumps(data))
    elif case == 'identity':
        release['stations'][0]['source_records'][0]['source_record_id'] = '8443970'
    elif case == 'predictor':
        release['stations'][0]['constant_sets'] = [{'set_id': 'unexpected'}]
    receipt_sha = '0' * 64 if case == 'receipt_hash' else hashlib.sha256(receipt.read_bytes()).hexdigest()
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        original_metadata.append_noaa_8660754_datums(release, path, sha, receipt_sha)
    assert release == before


def test_duplicate_captures_and_windows_are_retained(tmp_path):
    rows = [{'id': 'dmi:42', 'name': 'first', 'lat': 55, 'lon': 10, 'country': 'DNK'},
            {'id': 'dmi:42', 'name': 'second', 'lat': 55.1, 'lon': 10.1, 'country': 'DNK'}]
    inv = inventory(tmp_path, {'gauge_dmi.json': {'source': 'dmi', 'rows': rows}},
                    [identity('42'), identity('42', 'v2')])
    result = load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')
    row = result['dmi', '42']
    assert json.loads(row['original_metadata']['captured_rows_json']) == rows
    assert json.loads(row['original_metadata']['inventory_rows_json']) == inv.document['identities']
    assert row['name'] is None
    assert 'conflicting' in row['detail']


def test_missing_metadata_is_explicit_and_nonspatial_units_are_excluded(tmp_path):
    inv = inventory(tmp_path, {}, [identity('missing'),
                    {**identity('artifact'), 'granularity': 'reference_response_file'}])
    row = load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')['dmi', 'missing']
    assert row['lat'] is None and row['name'] is None
    assert 'missing' in row['detail']
    assert json.loads(row['original_metadata']['inventory_rows_json']) == [identity('missing')]


def test_changed_consumed_capture_is_refused(tmp_path):
    inv = inventory(tmp_path, {'gauge_dmi.json': {'source': 'dmi', 'rows': []}}, [identity('42')])
    (tmp_path / 'gauge_dmi.json').write_text('{}')
    with pytest.raises(ValueError, match='integrity mismatch'):
        load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')


def test_bom_unsigned_angle_does_not_claim_verified_physical_position(tmp_path):
    # Actual frozen INT_TP054 metadata, not a guessed corrected Vaitupu location.
    feature = {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [188.6833, -7.4833]},
               'properties': {'AAC': 'INT_TP054', 'PORT_NAME': 'Vaitupu',
                              'LAT': -7.4833, 'LON': 188.6833, 'TIME_ZONE': 'Pacific/Vaitupu'}}
    row = {**identity('INT_TP054'), 'source': 'bom', 'granularity': 'comparator_station_metadata'}
    inv = inventory(tmp_path, {'bom/sites.json': {'type': 'FeatureCollection', 'features': [feature]}}, [row])
    result = load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')['bom', 'INT_TP054']
    assert result['lon'] == pytest.approx(-171.3167)
    assert result['lat'] == -7.4833
    assert 'position_unreliable' in result['detail']
    assert 'physical location has not been verified' in result['detail']
    assert json.loads(result['original_metadata']['raw_metadata_rows_json']) == [feature]
    interpretation = json.loads(result['original_metadata']['OTC_position_interpretation_json'])
    assert interpretation['physical_location_verified'] is False
    assert interpretation['original_longitude'] == 188.6833
    assert result['licence'] is None


def test_malibu_current_4375_retains_independent_historical_facts_without_capability():
    root = Path(__file__).resolve().parents[3] / 'data/otc/supplements/malibu4375'
    path = root / 'gauge_chs-table4375.json'
    raw = path.read_bytes()
    captured = json.loads(raw)
    source = captured['source']
    inv = SourceInventory({'input_files': [{'path': str(path), 'size': len(raw),
                                           'sha256': hashlib.sha256(raw).hexdigest()}],
                           'identities': [{'source': source, 'version': '2021-volume5-table4',
                                           'upstream_record_id': '4375', 'granularity': 'gauge_station',
                                           'input_sha256': [hashlib.sha256(raw).hexdigest()]}]}, {})
    results = load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')
    assert set(results) == {(source, '4375')}
    result = results[source, '4375']
    row = json.loads(result['original_metadata']['captured_rows_json'])[0]
    assert row == captured['rows'][0]
    assert row['original_identity']['not_water_level_station_07860'] is True
    assert row['payload_scope'] == 'identity_and_provenance_only'
    assert 'original_table_facts' not in row
    assert row['publication_tier'] == 'fallback'
    assert row['quality_status'] == 'unvalidated_historical_metadata'
    assert row['rights_notice']['permission_granted'] is False
    assert row['rights_notice']['ogl_grant_established'] is False
    assert row['prediction_capability'] == 'none_implemented'
    assert row['upstream_harmonic_constants_acquired'] is False
    assert row['slackwater_used_as_input'] is False
    assert result['country'] == 'CAN'
    assert row['country_interpretation']['coordinate_substitution'] is False
    assert result['licence'] is None
    assert 'original_constant_set_json' not in result
