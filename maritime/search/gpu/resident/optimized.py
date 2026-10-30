"""Opt-in acceleration; the existing running baseline and saved cache are unchanged.

Uses the baseline scoring/status contract with parallel prediction and exact
cooperative matching. Only independent region proofs change pruning behavior.
"""

from typing import Any

from .chart_pruning import prune_chart_geometry
from .cooperative_matching import solve_cooperative_marker_costs
from .engine import ResidentDevice
from .parallel_prediction import predict_groups_parallel


class OptimizedResidentDevice(ResidentDevice):
    def __init__(
        self,
        world: Any,
        config: Any,
        device: int,
        *,
        parallel_prediction: bool = True,
        cooperative_matching: bool = True,
        true_bearings: bool = True,
        independent_chart_pruning: bool = True,
    ) -> None:
        super().__init__(world, config, device)
        self.use_parallel_prediction = parallel_prediction
        self.use_cooperative_matching = cooperative_matching
        self.use_true_bearings = true_bearings
        self.use_independent_chart_pruning = independent_chart_pruning
        if independent_chart_pruning:
            cp = self.cp
            with cp.cuda.Device(device), self.stream:
                objects = self.object_args[2]
                eligible = objects[:, 6] != 2
                bounds = objects[eligible, 4]
                self.chart_position_support_complete = bool(cp.all(cp.isfinite(bounds)).item())
                self.exact_marker_world = bool(cp.all(bounds == 0).item())
                self.marker_identities_complete = bool(cp.all(objects[eligible, 7] == 1).item())
                self.max_uncertainty = (
                    float(cp.max(bounds).item())
                    if self.chart_position_support_complete and len(bounds)
                    else 0.0
                )
            # Known vessels cannot explain a positive chart-aid report. Their
            # positional or identity uncertainty must not disable aid proofs.
            # Unknown object types remain included among possible chart markers.
            self.marker_proofs = bool(
                world.chart_scope_certified
                and self.chart_position_support_complete
                and world.max_projection_scale is not None
                and (true_bearings or world.azimuth_error_deg is not None)
                and config.trusted_marker_labels
                and config.hard_bearing_bounds_asserted
                and config.numerical_validation_accepted
            )
        if cooperative_matching:
            self.assignment = self._cooperative_assignment

    def prune(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
        if not self.use_independent_chart_pruning:
            return super().prune(cells, bearings, tolerances)
        return prune_chart_geometry(
            self, cells, bearings, tolerances, true_bearings=self.use_true_bearings
        )

    def _launch(self, name: str, n: int, args: tuple[Any, ...]) -> None:
        if self.use_parallel_prediction and name == "predict_groups" and n:
            result = predict_groups_parallel(self, args[0])
            keys = (
                "group_angles",
                "group_extents",
                "group_ids",
                "group_exact",
                "group_counts",
                "flags",
                "point_states",
            )
            # Preserve the original device-array outputs and numerical/status
            # logic; the four serial scratch arrays are no longer populated.
            for key, destination in zip(keys, args[20:27], strict=True):
                destination[...] = result[key]
            return
        super()._launch(name, n, args)

    def positive_marker_rejection(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
        """Prove center impossibility without unrelated nominal predictor warnings."""
        if not self.use_independent_chart_pruning:
            return super().positive_marker_rejection(cells, bearings, tolerances)
        cp = self.cp
        centers = (cells[:, :2] + cells[:, 2:]) * 0.5
        center_cells = cp.concatenate((centers, centers), axis=1)
        reasons = prune_chart_geometry(
            self, center_cells, bearings, tolerances, true_bearings=self.use_true_bearings
        )
        return cp.where((reasons >= 2) & (reasons <= 4), reasons, 0).astype(cp.int32)

    def _cooperative_assignment(self, grid: Any, block: Any, args: tuple[Any, ...]) -> None:
        cp = self.cp
        cost, counts = args[0], args[4]
        batch, rows, groups = map(int, cost.shape)
        # Every removed column is forbidden for every report. Stable compaction
        # retains original-column order and maps all selected IDs back on GPU.
        available = cp.any(cp.isfinite(cost), axis=1)
        available &= cp.arange(groups)[None, :] < counts[:, None]
        compact_counts = cp.sum(available, axis=1, dtype=cp.int32)
        width = int(cp.max(compact_counts).item()) if batch else 0
        if width:
            order = cp.argsort(~available, axis=1, kind="stable")[:, :width]
            compact = cp.take_along_axis(cost, order[:, None, :], axis=2)
            result = solve_cooperative_marker_costs(compact, compact_counts, cupy_module=cp)
            chosen = cp.clip(result.columns, 0, width - 1)
            original = cp.take_along_axis(order, chosen, axis=1)
            args[14][...] = cp.where(result.columns >= 0, original, -1)
            args[15][...] = result.residuals
            args[16][...] = (rows - result.matched_counts) * args[1][:, 0]
            args[19][...] = result.errors
        else:
            args[14].fill(-1)
            args[15].fill(0.0)
            args[16][...] = cp.sum(args[1], axis=1)
            args[19].fill(0)
        args[17].fill(0.0)
        args[18][...] = args[15] + args[16] + args[3]
