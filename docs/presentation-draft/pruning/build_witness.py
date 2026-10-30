"""Build a Hall witness figure from saved GPU incidence snapshots only.

This is CPU analysis of recorded adjacency, not a localization execution.
No map, bearing, visibility, position inference, or GPU predicate is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import deque
from pathlib import Path

import numpy as np


def union_neighbors(rows: set[int], neighbors: list[set[int]]) -> set[int]:
    return set().union(*(neighbors[row] for row in rows)) if rows else set()


def maximum_matching(
    neighbors: list[set[int]], order: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    owners: dict[int, int] = {}

    def augment(row: int, seen: set[int]) -> bool:
        for obj in sorted(neighbors[row]):
            if obj in seen:
                continue
            seen.add(obj)
            if obj not in owners or augment(owners[obj], seen):
                owners[obj] = row
                return True
        return False

    for row in order:
        augment(row, set())
    return owners, {row: obj for obj, row in owners.items()}


def alternating_witness(
    start: int, neighbors: list[set[int]], owners: dict[int, int]
) -> set[int]:
    rows, pending = {start}, deque([start])
    while pending:
        for obj in neighbors[pending.popleft()]:
            if obj not in owners:
                raise ValueError(
                    "Recorded graph admitted an augmenting path after maximum matching."
                )
            row = owners[obj]
            if row not in rows:
                rows.add(row)
                pending.append(row)
    if len(union_neighbors(rows, neighbors)) >= len(rows):
        raise ValueError("Alternating closure is not a Hall-deficient set.")
    return rows


def shrink(rows: set[int], neighbors: list[set[int]], order: list[int]) -> set[int]:
    result = set(rows)
    changed = True
    while changed:
        changed = False
        for row in order:
            if row not in result:
                continue
            proposed = result - {row}
            if len(union_neighbors(proposed, neighbors)) < len(proposed):
                result = proposed
                changed = True
    return result


def read_candidate(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as data:
        metadata = json.loads(data["metadata_utf8"].tobytes().decode())
        if (
            metadata["state"] != 4
            or metadata["flags"] != 0
            or metadata["proof_valid"] is not True
        ):
            raise ValueError(
                f"Snapshot is not a certified zero-flag Hall rejection. {path}"
            )
        incidence = data["incidence"].copy()
        bearings, tolerances = (
            data["bearings_deg"].copy(),
            data["tolerances_deg"].copy(),
        )
        object_ids, objects = data["object_ids"].copy(), data["objects"].copy()
        cell = data["cell"].copy()
    report_count = len(bearings)
    if report_count != 64 or incidence.shape != (
        len(object_ids),
        (report_count + 31) // 32,
    ):
        raise ValueError("Expected the actual current 64-report snapshot layout.")
    if len(metadata["object_keys"]) != len(object_ids):
        raise ValueError("Snapshot object keys and columns are not aligned.")
    neighbors = [
        {
            j
            for j, word in enumerate(incidence[:, row // 32])
            if (int(word) >> (row % 32)) & 1
        }
        for row in range(report_count)
    ]
    if any(not values for values in neighbors):
        raise ValueError(
            "A state-4 snapshot unexpectedly has an individually unsupported report."
        )
    possibilities: set[tuple[int, ...]] = set()
    indices = list(range(report_count))
    # Different deterministic matching orders reveal different alternating
    # closures. Every retained witness is explicitly rechecked after shrinking.
    for shift in range(report_count):
        order = indices[shift:] + indices[:shift]
        owners, matches = maximum_matching(neighbors, order)
        if len(matches) == report_count:
            raise ValueError("Recorded state-4 incidence admits a complete assignment.")
        for start in indices:
            if start in matches:
                continue
            rows = alternating_witness(start, neighbors, owners)
            orders = [
                sorted(rows),
                sorted(rows, reverse=True),
                sorted(rows, key=lambda r: (len(neighbors[r]), r)),
            ]
            for deletion_order in orders:
                reduced = shrink(rows, neighbors, deletion_order)
                possibilities.add(tuple(sorted(reduced)))
    selected = min(
        possibilities,
        key=lambda rows: (len(rows), len(union_neighbors(set(rows), neighbors)), rows),
    )
    selected_objects = sorted(union_neighbors(set(selected), neighbors))
    if not len(selected_objects) < len(selected):
        raise ValueError("Chosen witness has lost its deficiency.")
    if any(
        len(union_neighbors(set(selected) - {r}, neighbors)) < len(selected) - 1
        for r in selected
    ):
        raise ValueError("Chosen witness can still be reduced by deleting a report.")
    return {
        "path": path,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "metadata": metadata,
        "cell": cell,
        "bearings": bearings,
        "tolerances": tolerances,
        "object_ids": object_ids,
        "objects": objects,
        "neighbors": neighbors,
        "reports": list(selected),
        "columns": selected_objects,
        "alternatives_considered": len(possibilities),
    }


def make_output(candidates: list[dict], base: Path) -> dict:
    chosen = min(
        candidates,
        key=lambda c: (
            len(c["reports"]),
            len(c["columns"]),
            c["reports"],
            c["metadata"]["device"],
        ),
    )
    rows, columns = chosen["reports"], chosen["columns"]
    metadata = chosen["metadata"]
    keys = metadata["object_keys"]
    object_lookup = {column: i for i, column in enumerate(columns)}
    edges = [
        {
            "report_index": row,
            "object_index": object_lookup[column],
            "object_key": keys[column],
        }
        for row in rows
        for column in sorted(chosen["neighbors"][row])
    ]
    # All N(S) columns and every incident edge appear in the output. A figure
    # with more than twelve reports uses an explicitly labelled subset view;
    # the JSON remains the complete proof.
    full_graph = len(rows) <= 12 and len(columns) <= 12
    displayed_rows = rows if full_graph else rows[:6]
    displayed_columns = (
        columns
        if full_graph
        else sorted(union_neighbors(set(displayed_rows), chosen["neighbors"]))
    )
    show_graph = len(displayed_columns) <= 12
    parity_path = base / "parity.json"
    parity = json.loads(parity_path.read_text()) if parity_path.exists() else {}
    result = {
        "schema": "RecordedHallWitnessV1",
        "author": "J.C. Vaught",
        "analysis_scope": "Saved incidence only. No geographic predicate or localization inference was executed.",
        "source_snapshot": str(chosen["path"].relative_to(base)),
        "source_sha256": chosen["source_sha256"],
        "source_metadata": metadata,
        "source_run": parity.get("source_run"),
        "snapshot_candidates": [
            {
                "path": str(c["path"].relative_to(base)),
                "sha256": c["source_sha256"],
                "device": c["metadata"]["device"],
                "report_count": len(c["reports"]),
                "neighbor_count": len(c["columns"]),
                "distinct_witnesses_considered": c["alternatives_considered"],
            }
            for c in candidates
        ],
        "selection_method": "Smallest found among deterministic maximum-matching alternating closures and deficiency-preserving deletion orders across the recorded GPU snapshots. Inclusion-minimal; no global minimum-cardinality claim.",
        "proof_valid": True,
        "flags": 0,
        "state": 4,
        "crs": "EPSG:32618",
        "cell_columns": ["xmin", "ymin", "xmax", "ymax"],
        "cell_bounds": chosen["cell"].tolist(),
        "report_index_semantics": "Zero-based order within the actual filtered 64 positive-aid bearings.",
        "report_indices": rows,
        "report_count": len(rows),
        "neighbor_count": len(columns),
        "deficiency": len(rows) - len(columns),
        "reports": [
            {
                "index": r,
                "bearing_deg": float(chosen["bearings"][r]),
                "tolerance_deg": float(chosen["tolerances"][r]),
            }
            for r in rows
        ],
        "neighbors": [
            {
                "index": i,
                "snapshot_column": column,
                "catalog_object_id": int(chosen["object_ids"][column]),
                "key": keys[column],
                "packed_object": chosen["objects"][column].tolist(),
            }
            for i, column in enumerate(columns)
        ],
        "edges": edges,
        "adjacency_complete_for_report_subset": True,
        "edge_count": len(edges),
        "figure": {
            "mode": "full_graph" if full_graph else "subset_with_full_witness_counts",
            "display_graph": show_graph,
            "display_report_indices": displayed_rows,
            "display_neighbor_indices": [object_lookup[c] for c in displayed_columns],
            "all_neighbors_of_displayed_reports_included": True,
            "minimum_label_pt": 14,
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--graphs-dir", type=Path, default=Path(__file__).parent / "pruning-graphs"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "witness.json"
    )
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    paths = sorted(args.graphs_dir.glob("gpu-*-state-4.npz"))
    if not paths:
        raise SystemExit("No saved state-4 graph snapshots are available.")
    result = make_output([read_candidate(path) for path in paths], args.output.parent)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    )
    if args.render:
        source = Path(__file__).with_name("witness.typ")
        subprocess.run(
            [
                "typst",
                "compile",
                "--root",
                str(source.parent),
                str(source),
                str(source.with_suffix(".pdf")),
            ],
            check=True,
        )
        subprocess.run(
            [
                "typst",
                "compile",
                "--root",
                str(source.parent),
                "--ppi",
                "180",
                str(source),
                str(source.with_suffix(".png")),
            ],
            check=True,
        )
    print(
        json.dumps(
            {
                "reports": result["report_indices"],
                "neighbor_count": result["neighbor_count"],
                "edges": result["edge_count"],
                "snapshot": result["source_snapshot"],
                "figure": result["figure"],
            }
        )
    )


if __name__ == "__main__":
    main()
