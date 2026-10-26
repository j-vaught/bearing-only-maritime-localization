from pathlib import Path
p=Path('tests/accuracy/test_a43_geodesic.py')
s=p.read_text()
old='''    kwargs = dict(
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=100_000,
        source_chunk_size=3,
    )
    serial = generate_geographic_reverse_wedge_proposals_geodesic(observations, catalog, **kwargs)
    parallel = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, source_workers=workers, **kwargs
    )
'''
new='''    def run(source_workers: int = 1):
        return generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            angular_tolerance_deg=0.5,
            max_source_pairs=30,
            max_candidate_cells=100_000,
            source_chunk_size=3,
            source_workers=source_workers,
        )

    serial = run()
    parallel = run(workers)
'''
if old not in s: raise SystemExit('first block absent')
s=s.replace(old,new)
old='''    kwargs = dict(
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=1,
        source_chunk_size=3,
    )
    serial = generate_geographic_reverse_wedge_proposals_geodesic(observations, catalog, **kwargs)
    parallel = generate_geographic_reverse_wedge_proposals_geodesic(
        observations, catalog, source_workers=2, **kwargs
    )
'''
new='''    def run(source_workers: int = 1):
        return generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            angular_tolerance_deg=0.5,
            max_source_pairs=30,
            max_candidate_cells=1,
            source_chunk_size=3,
            source_workers=source_workers,
        )

    serial = run()
    parallel = run(2)
'''
if old not in s: raise SystemExit('second block absent')
p.write_text(s.replace(old,new))
