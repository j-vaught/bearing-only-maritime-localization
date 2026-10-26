from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path

from bearing_localization.search.proposals.geodesic_wedge import (
    generate_geographic_reverse_wedge_proposals_geodesic,
)
from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame

ROOT = Path('/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/a26-pilot-c76be7c/static-inputs')
MEAS = ROOT / 'measurements/67448756902a8c1c2ae3-ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1.json'
CAT = ROOT / 'catalog-2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704.json'
OUT = Path('/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/a43-geodesic-multiprocess')
OUT.mkdir(parents=True, exist_ok=True)

measurements = json.loads(MEAS.read_text())['measurements']
records = json.loads(CAT.read_text())['records']
observations = tuple(WedgeObservation(i, float(row['bearing_deg'])) for i, row in enumerate(measurements))
objects = tuple(
    GeographicWedgeCatalogObject(str(row['key']), float(row['longitude_deg']), float(row['latitude_deg']))
    for row in sorted(records, key=lambda row: str(row['key']))
)
frame = LocalFrame((-76.43, 39.32))


def signature(result):
    return [
        (p.candidate_id, p.vote_count, p.matching_count, p.witness_keys,
         p.witness_report_keys, p.normalized_angular_residual, p.evidence_score)
        for p in result.proposals
    ]


def run(source_count: int, workers: int):
    catalog = objects[:source_count]
    pair_budget = source_count * len(observations)
    started = time.perf_counter()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, frame=frame, max_range_m=10_000.0,
        cell_size_m=250.0, angular_tolerance_deg=0.5,
        max_source_pairs=pair_budget, max_candidate_cells=5_000_000,
        source_chunk_size=4096, source_workers=workers,
        solver_tolerance_deg=1e-7,
    )
    seconds = time.perf_counter() - started
    return result, seconds


def compare(source_count: int, repetitions: int):
    outputs = {}
    baseline, baseline_s = run(source_count, 1)
    base_sig = signature(baseline)
    entries = {1: [baseline_s]}
    for workers in (2, 4):
        result, seconds = run(source_count, workers)
        assert signature(result) == base_sig, f'categorical/numeric exact signature differs for {workers} workers'
        assert result.source_pairs_processed == baseline.source_pairs_processed
        assert result.source_pairs_failed == baseline.source_pairs_failed
        assert result.geodesic_samples_total == baseline.geodesic_samples_total
        assert result.geodesic_samples_failed == baseline.geodesic_samples_failed
        assert result.geodesic_samples_degenerate == baseline.geodesic_samples_degenerate
        assert result.generation_status == baseline.generation_status
        assert result.budget_reason == baseline.budget_reason
        assert result.source_pairs_computed >= result.source_pairs_processed
        assert result.source_pairs_discarded == result.source_pairs_computed - result.source_pairs_processed
        entries[workers] = [seconds]
    for _ in range(repetitions - 1):
        for workers in (1, 2, 4):
            result, seconds = run(source_count, workers)
            assert signature(result) == base_sig
            entries[workers].append(seconds)
    for workers, timings in entries.items():
        timings.sort()
        outputs[str(workers)] = {
            'seconds': timings,
            'median_seconds': statistics.median(timings),
            'source_pairs_processed': baseline.source_pairs_processed,
            'source_pairs_computed': baseline.source_pairs_computed,
            'source_pairs_discarded': baseline.source_pairs_discarded,
            'candidate_cells': baseline.candidate_cells_total,
            'source_pairs_failed': baseline.source_pairs_failed,
            'geodesic_samples_total': baseline.geodesic_samples_total,
            'geodesic_samples_failed': baseline.geodesic_samples_failed,
            'geodesic_samples_degenerate': baseline.geodesic_samples_degenerate,
            'generation_status': baseline.generation_status,
            'budget_reason': baseline.budget_reason,
        }
    return {
        'source_count': source_count,
        'observation_count': len(observations),
        'pair_budget': source_count * len(observations),
        'catalog_prefix_sha256': hashlib.sha256('\n'.join(o.key for o in objects[:source_count]).encode()).hexdigest(),
        'measurement_sha256': hashlib.sha256(MEAS.read_bytes()).hexdigest(),
        'catalog_file_sha256': hashlib.sha256(CAT.read_bytes()).hexdigest(),
        'settings': {'frame_origin_lonlat': list(frame.origin_lonlat), 'max_range_m': 10000.0, 'cell_size_m': 250.0, 'angular_tolerance_deg': 0.5, 'source_chunk_size': 4096, 'max_candidate_cells': 5000000, 'solver_tolerance_deg': 1e-7},
        'ordered_proposal_signature_sha256': hashlib.sha256(json.dumps(base_sig, separators=(',', ':')).encode()).hexdigest(),
        'worker_results': outputs,
        'exact_signature_parity': True,
    }


if __name__ == '__main__':
    payload = {
        'implementation_sha256': hashlib.sha256(Path('bearing_localization/search/proposals/geodesic_wedge.py').read_bytes()).hexdigest(),
        'measurement_path': str(MEAS),
        'catalog_path': str(CAT),
        'cases': [compare(4000, 1)],
    }
    dest = OUT / 'worker-comparison-20k-chunk4096.json'
    dest.write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps(payload, indent=2))
