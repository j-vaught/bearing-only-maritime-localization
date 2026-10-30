"""Frozen sensor-frame generation for the eligible coastal sites. J.C. Vaught."""

from maritime.coast_data import save_json


def simulate(row, layers, scene, simulation_settings, directory):
    from dataclasses import asdict

    import numpy as np
    from pyproj import Geod, Transformer
    from shapely import clip_by_rect, make_valid

    from maritime.geometry.vector_map import VectorMap
    from maritime.serialization import write_contract
    from maritime.simulator.mismatch import KnownWaterObserver, SensorConfigurationV2
    from maritime.simulator.runner import _offset_position, run_simulator_v2

    sensor = SensorConfigurationV2(**simulation_settings["sensor"])
    observer = KnownWaterObserver(float(row["longitude_deg"]), float(row["latitude_deg"]), 0.0)
    # Simulator-only crop follows the saved corrected Charleston generation.
    # All physical rays lie strictly inside these bounds; search remains global.
    forward = Transformer.from_crs("EPSG:4326", layers["crs"], always_xy=True)
    physical = [
        _offset_position(record.longitude_deg, record.latitude_deg, dict(record.metadata))
        for record in scene.catalog.records
    ]
    longitude, latitude = np.asarray(physical).T
    _, _, distances = Geod(ellps="WGS84").inv(
        np.full(len(longitude), observer.longitude_deg),
        np.full(len(latitude), observer.latitude_deg),
        longitude,
        latitude,
    )
    in_range = (distances > 0) & (distances <= sensor.visibility_radius_m)
    x, y = forward.transform(longitude[in_range], latitude[in_range])
    observer_xy = forward.transform(observer.longitude_deg, observer.latitude_deg)
    points = np.vstack((np.column_stack((x, y)), observer_xy))
    bounds = (*points.min(axis=0) - 1.0, *points.max(axis=0) + 1.0)
    clipped = {}
    for name in ("land", "coverage", "unknown"):
        source = layers.get(name)
        geometry = clip_by_rect(source, *bounds) if source is not None else None
        if geometry is not None and not geometry.is_valid:
            geometry = make_valid(geometry)
        clipped[name] = geometry if geometry is not None and not geometry.is_empty else None
    vector_map = VectorMap(
        clipped["land"],
        crs=layers["crs"],
        coverage_geometry=clipped["coverage"],
        unknown_geometry=clipped["unknown"],
    )
    save_json(
        directory / "simulation-map-crop.json",
        {
            "bounds_en_m": list(bounds),
            "positive_margin_m": 1.0,
            "physical_targets_in_range": int(np.count_nonzero(in_range)),
            "scope": "Simulator LOS only. Full search world and bounds unchanged",
        },
    )
    run = run_simulator_v2(
        scene,
        observer,
        vector_map,
        sensor,
        seed=simulation_settings["seed"],
        trial_id=f"{row['site_id']}-seed11",
    )
    write_contract(directory / "measurements.json", run.measurements)
    save_json(directory / "simulation-diagnostics.json", asdict(run.diagnostics))
    save_json(
        directory / "truth.json",
        {"latitude_deg": observer.latitude_deg, "longitude_deg": observer.longitude_deg},
    )
    markers = [
        m.bearing_deg for m in run.measurements.measurements if m.object_type == "physical_aid"
    ]
    return markers, len(run.measurements.measurements)
