from pathlib import Path
p = Path("tests/accuracy/test_a43_geodesic.py")
s = p.read_text()
needle = "\ndef test_deterministic_frozen_catalog_subset_matches_scalar_oracle():\n"
insert = '''
@pytest.mark.parametrize("workers", [2, 4])
def test_spawned_source_workers_match_serial_complete_fixture(workers):
    frame, observations, catalog = _small_complete_fixture()
    kwargs = dict(
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=100_000,
        source_chunk_size=3,
    )
    serial = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, **kwargs
    )
    parallel = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, source_workers=workers, **kwargs
    )

    assert _categorical_signature(parallel) == _categorical_signature(serial)
    assert parallel.source_pairs_processed == serial.source_pairs_processed == 30
    assert parallel.source_pairs_computed == 30
    assert parallel.source_pairs_discarded == 0


def test_spawned_source_workers_preserve_candidate_budget_prefix_and_report_prefetch():
    frame, observations, catalog = _small_complete_fixture()
    kwargs = dict(
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=1,
        source_chunk_size=3,
    )
    serial = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, **kwargs
    )
    parallel = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, source_workers=2, **kwargs
    )

    assert _categorical_signature(parallel) == _categorical_signature(serial)
    assert parallel.generation_status == "budget_exhausted"
    assert parallel.budget_reason == "candidate_cells"
    assert parallel.source_pairs_processed == serial.source_pairs_processed
    assert parallel.source_pairs_computed >= parallel.source_pairs_processed
    assert parallel.source_pairs_discarded == (
        parallel.source_pairs_computed - parallel.source_pairs_processed
    )
    assert parallel.source_pairs_computed >= serial.source_pairs_computed


@pytest.mark.parametrize("workers", [0, -1, 5, True, 1.5])
def test_source_workers_must_be_an_integer_between_one_and_four(workers):
    frame, observations, catalog = _small_fixture()
    with pytest.raises(ValueError, match="source_workers"):
        generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            source_workers=workers,
        )

'''
if needle not in s:
    raise SystemExit("insertion point not found")
p.write_text(s.replace(needle, insert + needle))
