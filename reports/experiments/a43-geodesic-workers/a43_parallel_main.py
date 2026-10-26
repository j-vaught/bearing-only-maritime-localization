from pathlib import Path
p=Path('bearing_localization/search/proposals/geodesic_wedge.py')
s=p.read_text()
s=s.replace('    source_chunk_size: int = 256,\n    range_step_m:', '    source_chunk_size: int = 256,\n    source_workers: int = 1,\n    range_step_m:', 1)
s=s.replace('    if type(source_chunk_size) is not int or source_chunk_size < 1:\n        raise ValueError("source_chunk_size must be a positive integer.")\n', '    if type(source_chunk_size) is not int or source_chunk_size < 1:\n        raise ValueError("source_chunk_size must be a positive integer.")\n    if type(source_workers) is not int or not 1 <= source_workers <= 4:\n        raise ValueError("source_workers must be an integer in [1, 4].")\n', 1)
s=s.replace('    sample_degenerate = 0\n    max_solver_residual', '    sample_degenerate = 0\n    source_pairs_computed = 0\n    max_solver_residual', 1)
start=s.index('    for observation in obs:\n',s.index('def generate_geographic_reverse_wedge_proposals_geodesic'))
end=s.index('    if budget_reason == "none" and processed_pairs < source_total:\n',start)
new='''    def make_source_tasks(observation: WedgeObservation, sigma_deg: float):
        remaining = effective_source_limit - processed_pairs
        for chunk_start in range(0, len(objects), source_chunk_size):
            if remaining <= 0:
                return
            chunk_size = min(source_chunk_size, remaining, len(objects) - chunk_start)
            source_chunk = source_lonlat[chunk_start : chunk_start + chunk_size]
            yield _SourceChunkTask(
                source_start=chunk_start,
                source_chunk=source_chunk,
                bearing_deg=observation.bearing_deg,
                sigma_deg=sigma_deg,
                sample_distances=sample_distances,
                angular_offsets_deg=angular_offsets_deg,
                solver_tolerance_deg=solver_tolerance_deg,
                cell_size_m=cell_size_m,
                angular_tolerance_deg=angular_tolerance_deg,
                final_distance_m=final_distance_m,
            )
            remaining -= chunk_size

    def consume_source_chunk(result: _SourceChunkResult) -> None:
        nonlocal processed_pairs, failed_pairs, sample_total, sample_failed
        nonlocal sample_degenerate, max_solver_residual, budget_reason
        # Preserve the serial generator's whole-chunk solver counters, including
        # a possible unmerged suffix when candidate storage fills mid-chunk.
        failed_pairs += result.source_pairs_failed
        sample_total += result.geodesic_samples_total
        sample_failed += result.geodesic_samples_failed
        sample_degenerate += result.geodesic_samples_degenerate
        max_solver_residual = max(max_solver_residual, result.max_solver_residual_deg)
        for pair in result.pairs:
            for cell, evidence in pair.pair_evidence.items():
                if cell not in witness_index and len(witness_index) >= max_candidate_cells:
                    budget_reason = "candidate_cells"
                    break
                report_witnesses = witness_index.setdefault(cell, {})
                report_witnesses.setdefault(current_report_index, set()).add(
                    objects[pair.source_index].key
                )
                previous = residual_index.setdefault(cell, {}).get(current_report_index)
                if previous is None or evidence[0] < previous[0]:
                    residual_index[cell][current_report_index] = evidence
            processed_pairs += 1
            if budget_reason != "none":
                break

    source_executor: ProcessPoolExecutor | None = None
    if source_workers > 1:
        source_executor = ProcessPoolExecutor(
            max_workers=source_workers,
            mp_context=get_context("spawn"),
            initializer=_initialize_source_worker,
            initargs=(frame.projected_crs.to_wkt(), frame.origin_lonlat),
        )
    try:
        for observation in obs:
            if budget_reason != "none":
                break
            current_report_index = observation.report_index
            sigma_deg = sigma_by_report[current_report_index]
            tasks = iter(make_source_tasks(observation, sigma_deg))
            if source_executor is None:
                for task in tasks:
                    chunk_result = _compute_source_chunk(
                        task, forward, inverse, origin_x, origin_y
                    )
                    source_pairs_computed += len(chunk_result.pairs)
                    consume_source_chunk(chunk_result)
                    if budget_reason != "none":
                        break
                continue

            pending: deque[tuple[_SourceChunkTask, Future[_SourceChunkResult]]] = deque()
            for _ in range(source_workers):
                task = next(tasks, None)
                if task is None:
                    break
                pending.append((task, source_executor.submit(_compute_source_chunk_in_worker, task)))
            while pending:
                _, future = pending.popleft()
                try:
                    chunk_result = future.result(timeout=_SOURCE_WORKER_TIMEOUT_SECONDS)
                except FutureTimeoutError as exc:
                    _terminate_source_executor(source_executor)
                    source_executor = None
                    raise TimeoutError(
                        "Geodesic source worker exceeded its 300-second chunk timeout; "
                        "no partial proposal result was returned."
                    ) from exc
                except Exception as exc:
                    _terminate_source_executor(source_executor)
                    source_executor = None
                    raise RuntimeError(
                        "Geodesic source worker failed; no partial proposal result was returned."
                    ) from exc
                source_pairs_computed += len(chunk_result.pairs)
                consume_source_chunk(chunk_result)
                if budget_reason != "none":
                    # These already-submitted chunks are bounded by source_workers.
                    # Count their completed pairs as computed, but do not merge them.
                    while pending:
                        _, extra_future = pending.popleft()
                        try:
                            extra_result = extra_future.result(
                                timeout=_SOURCE_WORKER_TIMEOUT_SECONDS
                            )
                        except FutureTimeoutError as exc:
                            _terminate_source_executor(source_executor)
                            source_executor = None
                            raise TimeoutError(
                                "Prefetched geodesic source worker timed out; "
                                "no partial proposal result was returned."
                            ) from exc
                        except Exception as exc:
                            _terminate_source_executor(source_executor)
                            source_executor = None
                            raise RuntimeError(
                                "Prefetched geodesic source worker failed; "
                                "no partial proposal result was returned."
                            ) from exc
                        source_pairs_computed += len(extra_result.pairs)
                    break
                next_task = next(tasks, None)
                if next_task is not None:
                    pending.append(
                        (
                            next_task,
                            source_executor.submit(_compute_source_chunk_in_worker, next_task),
                        )
                    )
    except BaseException:
        if source_executor is not None:
            _terminate_source_executor(source_executor)
            source_executor = None
        raise
    else:
        if source_executor is not None:
            source_executor.shutdown(wait=True, cancel_futures=False)
            source_executor = None

'''
s=s[:start]+new+s[end:]
s=s.replace('        max_solver_residual_deg=float(max_solver_residual),\n', '        max_solver_residual_deg=float(max_solver_residual),\n        source_pairs_computed=source_pairs_computed,\n        source_pairs_discarded=source_pairs_computed - processed_pairs,\n',1)
p.write_text(s)
