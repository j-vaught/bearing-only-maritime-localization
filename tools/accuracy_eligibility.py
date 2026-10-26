"""Freeze pre-search support eligibility against an immutable benchmark plan."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCHEMA = "accuracy-eligibility-manifest-v1"
EXPECTED_SPLIT_PORTS = {"development": 12, "validation": 4, "heldout": 8}
RANGES_M = (1000, 5000, 10000)
TYPE_LABELS = (True, False)
PROFILE_ORDER = (
    "nominal",
    "miss",
    "clutter",
    "noise",
    "label_error",
    "heading_bias",
    "catalog_displacement",
    "map_buffer",
)


@dataclass(frozen=True)
class EligibilityTrial:
    """Immutable support evidence for one expected trial."""

    trial_id: str
    split: str
    port_id: str
    placement_id: str
    realization: str
    range_m: int
    report_types: bool
    physical_seed: int
    sensor_condition: str
    status: str
    supported: bool | None
    support_count: int | None
    support_path: str | None
    support_sha256: str | None
    preparation_status: str
    preparation_reason: str
    eligibility_status: str
    eligibility_reason: str | None


@dataclass(frozen=True)
class EligibilitySnapshot:
    """Plan-complete immutable eligibility population."""

    split: str
    plan_sha256: str
    config_sha256: str
    splits_sha256: str
    trial_table_sha256: str
    preparation_sha256: str | None
    expected_trial_count: int
    expected_trial_ids_sha256: str
    expected_nominal_count: int
    expected_nominal_ids_sha256: str
    trials: tuple[EligibilityTrial, ...]
    nominal_projections: Mapping[tuple[int, bool], tuple[EligibilityTrial, ...]]

    @property
    def nominal_trials(self) -> tuple[EligibilityTrial, ...]:
        return tuple(row for row in self.trials if row.sensor_condition == "nominal")

    @property
    def supported_nominal(self) -> tuple[EligibilityTrial, ...]:
        return tuple(row for row in self.nominal_trials if row.supported is True)

    @property
    def supported_denominator(self) -> int:
        return len(self.supported_nominal)

    @property
    def all_trials(self) -> tuple[EligibilityTrial, ...]:
        return self.trials


class EligibilityError(ValueError):
    """Raised when the planned population or frozen evidence cannot be trusted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_ids_sha256(ids: list[str] | tuple[str, ...]) -> str:
    payload = ("\n".join(ids) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EligibilityError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EligibilityError(f"Expected a JSON object in {path}.")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
    except OSError as exc:
        raise EligibilityError(f"Cannot read table {path}: {exc}") from exc


def _split_name(split: str) -> str:
    return "heldout" if split == "test" else split


def _support_relative_path(row: Mapping[str, str]) -> Path:
    type_mode = "types-on" if row["report_types"] == "True" else "types-off"
    return (
        Path(row["split"])
        / row["port_id"]
        / row["placement_id"]
        / f"r{row['realization']}"
        / f"{int(row['range_m'])}m"
        / type_mode
        / row["sensor_condition"]
        / "support.json"
    )


def _support_manifest(support: Mapping[str, Any]) -> tuple[bool, int]:
    """Validate the evaluator's support schema and return its derived decision."""
    required = {"valid_reference_water", "detected_physical_catalog_matched_groups"}
    optional = {"port_id", "placement_id", "supported"}
    keys = set(support)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise EligibilityError("Support JSON does not match the SupportManifest schema.")
    valid_water = support["valid_reference_water"]
    count = support["detected_physical_catalog_matched_groups"]
    if type(valid_water) is not bool:
        raise EligibilityError("Support valid_reference_water must be bool.")
    if type(count) is not int or count < 0:
        raise EligibilityError("Support matched-group count must be a nonnegative integer.")
    for field in ("port_id", "placement_id"):
        value = support.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise EligibilityError(f"Support {field} must be a nonempty string or null.")
    derived = valid_water and count >= 3
    if "supported" in support:
        value = support["supported"]
        if type(value) is not bool:
            raise EligibilityError("Support supported must be bool when present.")
        if value is not derived:
            raise EligibilityError("Support supported disagrees with its source fields.")
    return derived, count


def _stable_seed(*parts: object) -> int:
    encoded = "/".join(str(part) for part in parts).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:4], "big")


def _gate_key(range_m: int, report_types: str) -> str:
    mode = "on" if report_types == "True" else "off"
    return f"{float(range_m)}m/types{mode}"


def _validate_extension_history(
    config: Mapping[str, Any],
    splits: Mapping[str, Any],
    rows: list[dict[str, str]],
    support_root: Path,
    ports_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    initial = config.get("initial_placements_per_port")
    maximum = config.get("maximum_placements_per_port")
    if initial != 25 or maximum != 100:
        raise EligibilityError(
            "The frozen plan must use the authorized 25-to-100 placement limits."
        )
    if config.get("base_seed") != 20261025:
        raise EligibilityError("Unexpected plan base seed.")
    if config.get("seed_tuple") != [
        20261025,
        "split",
        "port",
        "placement",
        "realization",
        "range_m",
    ] or config.get("seed_excludes") != ["algorithm", "report_types"]:
        raise EligibilityError("Plan seed definition does not match the frozen benchmark.")
    if config.get("ranges_m") != list(RANGES_M) or config.get("type_labels") != [True, False]:
        raise EligibilityError("Plan range or type-label population is invalid.")
    if config.get("realizations_per_placement") != 2:
        raise EligibilityError("Plan realization population must contain two realizations.")
    if config.get("stress_placements_per_port") != 5 or config.get("stress_realizations") != [0]:
        raise EligibilityError("Plan stress population is invalid.")
    profiles = config.get("stress_profiles")
    if not isinstance(profiles, dict) or "nominal" not in profiles:
        raise EligibilityError("Plan has no nominal profile.")
    history = config.get("support_extension_batches", [])
    if not isinstance(history, list):
        raise EligibilityError("Plan support-extension history must be a list.")
    if history and config.get("extension_rule") != (
        "split-level support below 100 in any range/label gate; add deterministic 25-placement "
        "batches to one port at a time, up to 100, and reevaluate from pre-search support"
    ):
        raise EligibilityError("Plan extension rule does not match the authorized rule.")
    if not history and "extension_rule" in config:
        raise EligibilityError("Plan declares an extension rule without extension batches.")

    split_ports: dict[str, list[str]] = {
        split: sorted(
            port_id for port_id, port in ports_by_id.items() if port["effective_split"] == split
        )
        for split in EXPECTED_SPLIT_PORTS
    }
    max_placement = {
        (split, port_id): initial for split, port_ids in split_ports.items() for port_id in port_ids
    }
    validated_history: list[dict[str, Any]] = []
    evidence_by_split: dict[str, dict[tuple[int, str], int]] = {}
    for batch_index, batch in enumerate(history, start=1):
        if not isinstance(batch, dict):
            raise EligibilityError(f"Malformed support-extension batch {batch_index}.")
        split = batch.get("split")
        port_id = batch.get("port_id")
        if split not in {"development", "validation"} or port_id not in split_ports.get(split, []):
            raise EligibilityError(f"Invalid split/port in support-extension batch {batch_index}.")
        eligible_ports = [
            pid for pid in split_ports[split] if max_placement[(split, pid)] < maximum
        ]
        expected_port = eligible_ports[0] if eligible_ports else None
        first = max_placement[(split, port_id)] + 1
        last = min(first + 24, maximum)
        if (
            expected_port != port_id
            or batch.get("first_placement") != first
            or batch.get("last_placement") != last
            or last - first + 1 != 25
        ):
            raise EligibilityError(
                f"Unauthorized placement batch boundaries at batch {batch_index}."
            )
        gates = batch.get("deficient_gates_before_batch")
        counts = batch.get("supported_units_before_batch")
        valid_gate_order = [(range_m, label) for range_m in RANGES_M for label in ("True", "False")]
        if not isinstance(gates, list) or not gates or not isinstance(counts, dict):
            raise EligibilityError(f"Missing deficient-gate evidence at batch {batch_index}.")
        parsed_gates: list[tuple[int, str]] = []
        for gate in gates:
            if not isinstance(gate, dict):
                raise EligibilityError(f"Malformed deficient gate at batch {batch_index}.")
            range_m = gate.get("range_m")
            labels = gate.get("report_types")
            if range_m not in RANGES_M or labels not in {"True", "False"}:
                raise EligibilityError(f"Invalid deficient gate at batch {batch_index}.")
            parsed_gates.append((range_m, labels))
        if len(parsed_gates) != len(set(parsed_gates)) or parsed_gates != [
            gate for gate in valid_gate_order if gate in parsed_gates
        ]:
            raise EligibilityError(
                f"Deficient gates are duplicated or out of order at batch {batch_index}."
            )
        expected_count_keys = {_gate_key(range_m, labels) for range_m, labels in parsed_gates}
        if set(counts) != expected_count_keys:
            raise EligibilityError(
                f"Deficient-gate counts do not match gates at batch {batch_index}."
            )
        for key, count in counts.items():
            if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count < 100:
                raise EligibilityError(f"Invalid support count in batch {batch_index}: {key}.")
        if split not in evidence_by_split:
            units: dict[tuple[int, str], set[tuple[str, str, str]]] = {}
            for row in rows:
                if (
                    row["split"] != split
                    or row["sensor_condition"] != "nominal"
                    or int(row["placement_id"].rsplit("-", 1)[1]) > initial
                ):
                    continue
                evidence_path = support_root / _support_relative_path(row)
                if not evidence_path.is_file():
                    raise EligibilityError(
                        f"Cannot validate extension history; missing initial support "
                        f"{evidence_path}."
                    )
                evidence = _read_json(evidence_path)
                evidence_supported, _ = _support_manifest(evidence)
                if evidence_supported:
                    gate = (int(row["range_m"]), row["report_types"])
                    units.setdefault(gate, set()).add(
                        (row["port_id"], row["placement_id"], row["realization"])
                    )
            evidence_by_split[split] = {gate: len(value) for gate, value in units.items()}
        actual_units = evidence_by_split[split]
        actual_deficient = [gate for gate in valid_gate_order if actual_units.get(gate, 0) < 100]
        expected_batch_counts = {
            _gate_key(range_m, labels): actual_units.get((range_m, labels), 0)
            for range_m, labels in actual_deficient
        }
        if parsed_gates != actual_deficient or counts != expected_batch_counts:
            raise EligibilityError(
                f"Support-extension evidence does not match initial support at batch {batch_index}."
            )
        validated_history.append(batch)
        max_placement[(split, port_id)] = last
    return {
        port_id: max_placement[(port["effective_split"], port_id)]
        for port_id, port in ports_by_id.items()
    }, validated_history


def _expected_plan_rows(
    config: Mapping[str, Any],
    ports_by_id: Mapping[str, Mapping[str, Any]],
    placements_per_port: Mapping[str, int],
    extension_history: list[dict[str, Any]],
) -> list[dict[str, str]]:
    profiles = config["stress_profiles"]
    ranges = config["ranges_m"]
    labels = config["type_labels"]
    expected: list[dict[str, str]] = []

    def append_trial(
        split: str,
        port_id: str,
        source_site_id: str,
        placement_index: int,
        realization: int,
        range_m: int,
        report_types: bool,
        condition: str,
    ) -> None:
        range_value = int(range_m)
        seed = _stable_seed(
            config["base_seed"],
            split,
            port_id,
            f"placement-{placement_index:03d}",
            realization,
            range_value,
        )
        type_name = "on" if report_types else "off"
        trial_id = (
            f"{split}/{port_id}/placement-{placement_index:03d}/r{realization}/"
            f"range{range_value}m/types{type_name}/{condition}"
        )
        expected.append(
            {
                "trial_id": trial_id,
                "split": split,
                "port_id": port_id,
                "source_site_id": source_site_id,
                "placement_id": f"placement-{placement_index:03d}",
                "realization": str(realization),
                "range_m": str(range_value),
                "report_types": str(report_types),
                "physical_seed": str(seed),
                "sensor_condition": condition,
                "profile_parameters": json.dumps(profiles[condition], sort_keys=True),
                "status": "planned",
            }
        )

    initial = config["initial_placements_per_port"]
    for port_id in sorted(ports_by_id):
        port = ports_by_id[port_id]
        split = port["effective_split"]
        for placement_index in range(1, initial + 1):
            for realization in range(config["realizations_per_placement"]):
                for range_m in ranges:
                    for condition in PROFILE_ORDER:
                        if condition not in profiles:
                            raise EligibilityError(f"Missing frozen stress profile {condition}.")
                        if condition != "nominal" and (
                            placement_index > config["stress_placements_per_port"]
                            or realization not in config["stress_realizations"]
                        ):
                            continue
                        for report_types in labels:
                            append_trial(
                                split,
                                port_id,
                                port["source_site_id"],
                                placement_index,
                                realization,
                                range_m,
                                report_types,
                                condition,
                            )
    for batch in extension_history:
        port = ports_by_id[batch["port_id"]]
        split = batch["split"]
        for placement_index in range(batch["first_placement"], batch["last_placement"] + 1):
            for realization in range(config["realizations_per_placement"]):
                for range_m in ranges:
                    for report_types in labels:
                        append_trial(
                            split,
                            batch["port_id"],
                            port["source_site_id"],
                            placement_index,
                            realization,
                            range_m,
                            report_types,
                            "nominal",
                        )
    return expected


def _check_plan(
    plan_dir: Path, split: str, support_root: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    config_path = plan_dir / "config.json"
    splits_path = plan_dir / "splits.json"
    config = _read_json(config_path)
    splits = _read_json(splits_path)
    table_name = config.get("trial_table")
    if not isinstance(table_name, str) or Path(table_name).name != table_name:
        raise EligibilityError("Plan must name a local trial table.")
    table_path = plan_dir / table_name
    rows = _read_csv(table_path)
    hashes: dict[str, Any] = {
        "config_sha256": _sha256(config_path),
        "splits_sha256": _sha256(splits_path),
        "trial_table_sha256": _sha256(table_path),
    }
    if config.get("split_manifest_sha256") != hashes["splits_sha256"]:
        raise EligibilityError("Plan split-manifest hash does not match splits.json.")
    if config.get("trial_table_sha256") != hashes["trial_table_sha256"]:
        raise EligibilityError("Plan trial-table hash does not match trials.csv.")
    if config.get("schema") != "accuracy-suite-plan-v1":
        raise EligibilityError("Unsupported accuracy plan schema.")
    ports = splits.get("ports")
    if not isinstance(ports, list) or not all(isinstance(item, dict) for item in ports):
        raise EligibilityError("Split manifest has no valid ports list.")
    ports_by_id = {item.get("port_id"): item for item in ports}
    if len(ports_by_id) != len(ports) or None in ports_by_id:
        raise EligibilityError("Split manifest has missing or duplicate port IDs.")
    for split_name, expected_count in EXPECTED_SPLIT_PORTS.items():
        actual = sum(port.get("effective_split") == split_name for port in ports)
        if actual != expected_count:
            raise EligibilityError(f"Unexpected port population for split {split_name!r}.")
    moved = {
        decision.get("port_id")
        for decision in splits.get("overlap_decisions", [])
        if isinstance(decision, dict)
    }
    for port in ports:
        if (
            port.get("effective_split") != port.get("declared_split")
            and port.get("port_id") not in moved
        ):
            raise EligibilityError(f"Unrecorded split change for {port.get('port_id')}.")
    if len(rows) != config.get("trial_count"):
        raise EligibilityError("Trial table row count does not match the frozen plan.")
    ids = [row.get("trial_id", "") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise EligibilityError("Trial table has missing or duplicate trial IDs.")

    placements_by_port, extension_history = _validate_extension_history(
        config, splits, rows, support_root, ports_by_id
    )
    expected_rows = _expected_plan_rows(config, ports_by_id, placements_by_port, extension_history)
    if len(expected_rows) != len(rows):
        raise EligibilityError(
            f"Plan contains {len(rows)} rows; validated frozen plan requires {len(expected_rows)}."
        )
    for index, (actual, expected) in enumerate(zip(rows, expected_rows, strict=True)):
        for field, value in expected.items():
            if actual.get(field) != value:
                raise EligibilityError(
                    f"Trial {index} has invalid {field}: {actual.get(field)!r}, expected {value!r}."
                )

    chosen = _split_name(split)
    if chosen not in EXPECTED_SPLIT_PORTS:
        raise EligibilityError(f"Unknown split {chosen!r}.")
    expected = [
        row
        for row in expected_rows
        if row["split"] == chosen and row["sensor_condition"] == "nominal"
    ]
    nominal_ids = [row["trial_id"] for row in expected]
    expected_count = (
        sum(
            placements_by_port[port_id]
            for port_id, port in ports_by_id.items()
            if port["effective_split"] == chosen
        )
        * config["realizations_per_placement"]
        * len(config["ranges_m"])
        * len(config["type_labels"])
    )
    if len(expected) != expected_count:
        raise EligibilityError(
            f"Nominal population for {chosen} is {len(expected)}, expected {expected_count}."
        )
    if {row["port_id"] for row in expected} != {
        port_id for port_id, port in ports_by_id.items() if port["effective_split"] == chosen
    }:
        raise EligibilityError(f"Nominal rows do not cover the complete {chosen} port set.")
    split_ids = [row["trial_id"] for row in expected_rows if row["split"] == chosen]
    hashes["plan_sha256"] = hashlib.sha256(
        (hashes["config_sha256"] + hashes["splits_sha256"] + hashes["trial_table_sha256"]).encode()
    ).hexdigest()
    hashes["expected_trial_ids_sha256"] = _canonical_ids_sha256(split_ids)
    hashes["expected_trial_count"] = len(split_ids)
    hashes["expected_nominal_ids_sha256"] = _canonical_ids_sha256(nominal_ids)
    hashes["expected_nominal_count"] = expected_count
    hashes["selected_split"] = chosen
    hashes["ports_by_id"] = ports_by_id
    hashes["by_id"] = {row["trial_id"]: row for row in expected_rows}
    hashes["expected_trial_ids"] = split_ids
    hashes["expected_nominal_ids"] = nominal_ids
    return config, splits, expected_rows, hashes


def _record_dict(record: EligibilityTrial) -> dict[str, Any]:
    return {
        "trial_id": record.trial_id,
        "split": record.split,
        "port_id": record.port_id,
        "placement_id": record.placement_id,
        "realization": record.realization,
        "range_m": record.range_m,
        "report_types": record.report_types,
        "physical_seed": record.physical_seed,
        "sensor_condition": record.sensor_condition,
        "status": record.status,
        "supported": record.supported,
        "support_count": record.support_count,
        "support_path": record.support_path,
        "support_sha256": record.support_sha256,
    }


def _build_snapshot(
    plan_dir: Path,
    support_root: Path,
    split: str,
    *,
    frozen_records: list[dict[str, Any]] | None = None,
) -> EligibilitySnapshot:
    _config, _splits, rows, hashes = _check_plan(plan_dir, split, support_root)
    chosen = hashes["selected_split"]
    table_by_id = hashes["by_id"]
    expected_ids = hashes["expected_nominal_ids"]
    expected_all_ids = hashes["expected_trial_ids"]
    prep_path = support_root / chosen / "preparation.csv"
    if not prep_path.is_file():
        raise EligibilityError(f"Missing preparation ledger for split {chosen}.")
    prep_rows = _read_csv(prep_path)
    preparation_sha256 = _sha256(prep_path)
    prep_by_id: dict[str, dict[str, str]] = {}
    for item in prep_rows:
        trial_id = item.get("trial_id", "")
        if not trial_id or trial_id in prep_by_id:
            raise EligibilityError("Preparation table has missing or duplicate trial IDs.")
        planned = table_by_id.get(trial_id)
        if planned is None or planned["split"] != chosen:
            raise EligibilityError(f"Preparation table contains a foreign trial: {trial_id}.")
        for name in (
            "port_id",
            "placement_id",
            "realization",
            "range_m",
            "report_types",
            "physical_seed",
            "sensor_condition",
        ):
            if name in item and item[name] != planned.get(name):
                raise EligibilityError(f"Preparation metadata mismatch for {trial_id}: {name}.")
        if item.get("status") not in {"completed", "failed"}:
            raise EligibilityError(f"Invalid preparation status for {trial_id}.")
        prep_by_id[trial_id] = item
    if set(prep_by_id) != set(expected_all_ids):
        missing = sorted(set(expected_all_ids) - set(prep_by_id))
        unexpected = sorted(set(prep_by_id) - set(expected_all_ids))
        raise EligibilityError(
            f"Preparation ledger population mismatch; missing={missing[:3]}, "
            f"unexpected={unexpected[:3]}."
        )

    frozen_by_id = {record["trial_id"]: record for record in frozen_records or []}
    records: list[EligibilityTrial] = []
    for row in rows:
        if row["split"] != chosen:
            continue
        nominal = row["sensor_condition"] == "nominal"
        support_rel = _support_relative_path(row)
        support_path = support_root / support_rel
        prep = prep_by_id[row["trial_id"]]
        prep_status = prep["status"]
        prep_reason = prep.get("failure", "")
        if support_path.is_file():
            if prep_status != "completed":
                raise EligibilityError(
                    f"Failed preparation has support output for {row['trial_id']}."
                )
            support_hash = _sha256(support_path)
            support_doc = _read_json(support_path)
            supported, support_count = _support_manifest(support_doc)
            if support_doc.get("port_id") not in (None, row["port_id"]):
                raise EligibilityError(f"Support port mismatch for {row['trial_id']}.")
            if support_doc.get("placement_id") not in (None, row["placement_id"]):
                raise EligibilityError(f"Support placement mismatch for {row['trial_id']}.")
            status = (
                ("supported" if supported else "unsupported")
                if nominal
                else ("stress_supported" if supported else "stress_unsupported")
            )
            record = EligibilityTrial(
                trial_id=row["trial_id"],
                split=chosen,
                port_id=row["port_id"],
                placement_id=row["placement_id"],
                realization=row["realization"],
                range_m=int(row["range_m"]),
                report_types=row["report_types"] == "True",
                physical_seed=int(row["physical_seed"]),
                sensor_condition=row["sensor_condition"],
                status=status,
                supported=supported,
                support_count=support_count,
                support_path=support_rel.as_posix(),
                support_sha256=support_hash,
                preparation_status=prep_status,
                preparation_reason=prep_reason,
                eligibility_status="supported" if supported else "unsupported",
                eligibility_reason=None,
            )
        elif prep_status == "failed":
            record = EligibilityTrial(
                trial_id=row["trial_id"],
                split=chosen,
                port_id=row["port_id"],
                placement_id=row["placement_id"],
                realization=row["realization"],
                range_m=int(row["range_m"]),
                report_types=row["report_types"] == "True",
                physical_seed=int(row["physical_seed"]),
                sensor_condition=row["sensor_condition"],
                status="unresolved",
                supported=None,
                support_count=None,
                support_path=None,
                support_sha256=None,
                preparation_status=prep_status,
                preparation_reason=prep_reason,
                eligibility_status="unresolved",
                eligibility_reason=prep_reason,
            )
        else:
            raise EligibilityError(f"Missing support for completed trial {row['trial_id']}.")
        old = frozen_by_id.get(row["trial_id"])
        if old is not None and old != _record_dict(record):
            raise EligibilityError(f"Frozen trial evidence changed for {row['trial_id']}.")
        records.append(record)

    all_records = tuple(records)
    if tuple(record.trial_id for record in all_records) != tuple(expected_all_ids):
        raise EligibilityError("Manifest does not cover the exact planned trial population.")
    nominal_records = tuple(
        record for record in all_records if record.sensor_condition == "nominal"
    )
    if tuple(record.trial_id for record in nominal_records) != tuple(expected_ids):
        raise EligibilityError(
            "Manifest does not cover the exact planned nominal trial population."
        )
    if any(record.supported is None for record in nominal_records):
        raise EligibilityError("Unresolved nominal support blocks eligibility freeze.")
    projections = {
        (range_m, report_types): tuple(
            record
            for record in nominal_records
            if record.range_m == range_m and record.report_types is report_types
        )
        for range_m in RANGES_M
        for report_types in TYPE_LABELS
    }
    expected_projection_count = hashes["expected_nominal_count"] // (
        len(RANGES_M) * len(TYPE_LABELS)
    )
    if any(len(projection) != expected_projection_count for projection in projections.values()):
        raise EligibilityError("One or more nominal condition projections are incomplete.")
    return EligibilitySnapshot(
        split=chosen,
        plan_sha256=hashlib.sha256(
            (
                hashes["config_sha256"] + hashes["splits_sha256"] + hashes["trial_table_sha256"]
            ).encode()
        ).hexdigest(),
        config_sha256=hashes["config_sha256"],
        splits_sha256=hashes["splits_sha256"],
        trial_table_sha256=hashes["trial_table_sha256"],
        preparation_sha256=preparation_sha256,
        expected_trial_count=hashes["expected_trial_count"],
        expected_trial_ids_sha256=hashes["expected_trial_ids_sha256"],
        expected_nominal_count=hashes["expected_nominal_count"],
        expected_nominal_ids_sha256=hashes["expected_nominal_ids_sha256"],
        trials=all_records,
        nominal_projections=MappingProxyType(projections),
    )


def _record_dict(record: EligibilityTrial) -> dict[str, Any]:
    return {
        "trial_id": record.trial_id,
        "split": record.split,
        "port_id": record.port_id,
        "placement_id": record.placement_id,
        "realization": record.realization,
        "range_m": record.range_m,
        "report_types": record.report_types,
        "physical_seed": record.physical_seed,
        "sensor_condition": record.sensor_condition,
        "status": record.status,
        "supported": record.supported,
        "support_count": record.support_count,
        "support_path": record.support_path,
        "support_sha256": record.support_sha256,
        "preparation_status": record.preparation_status,
        "preparation_reason": record.preparation_reason,
        "eligibility_status": record.eligibility_status,
        "eligibility_reason": record.eligibility_reason,
    }


def _manifest_doc(snapshot: EligibilitySnapshot) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": 1,
        "split": snapshot.split,
        "plan_sha256": snapshot.plan_sha256,
        "config_sha256": snapshot.config_sha256,
        "splits_sha256": snapshot.splits_sha256,
        "trial_table_sha256": snapshot.trial_table_sha256,
        "preparation_sha256": snapshot.preparation_sha256,
        "expected_trial_count": snapshot.expected_trial_count,
        "expected_trial_ids_sha256": snapshot.expected_trial_ids_sha256,
        "expected_nominal_count": snapshot.expected_nominal_count,
        "expected_nominal_ids_sha256": snapshot.expected_nominal_ids_sha256,
        "supported_nominal_count": snapshot.supported_denominator,
        "supported_nominal_ids_sha256": _canonical_ids_sha256(
            tuple(record.trial_id for record in snapshot.supported_nominal)
        ),
        "trials": [_record_dict(record) for record in snapshot.trials],
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def freeze_eligibility_manifest(
    plan_dir: Path | str,
    support_root: Path | str,
    output_path: Path | str,
    split: str,
) -> EligibilitySnapshot:
    """Validate complete pre-search evidence, then atomically freeze it."""
    plan_dir = Path(plan_dir)
    support_root = Path(support_root)
    output_path = Path(output_path)
    snapshot = _build_snapshot(plan_dir, support_root, split)
    frozen = _manifest_doc(snapshot)
    if output_path.exists():
        existing = _read_json(output_path)
        if existing != frozen:
            raise EligibilityError(
                "Refusing to replace an existing eligibility manifest with changed evidence."
            )
    else:
        _atomic_write_json(output_path, frozen)
    return snapshot


def verify_eligibility_manifest(
    manifest_path: Path | str,
    plan_dir: Path | str,
    support_root: Path | str,
    split: str,
) -> EligibilitySnapshot:
    """Read-only check of a frozen manifest and its source/support hashes."""
    doc = _read_json(Path(manifest_path))
    if doc.get("schema") != SCHEMA or doc.get("version") != 1:
        raise EligibilityError("Unsupported eligibility manifest schema/version.")
    if doc.get("split") != _split_name(split):
        raise EligibilityError("Eligibility manifest split does not match request.")
    frozen_records = doc.get("trials")
    if not isinstance(frozen_records, list):
        raise EligibilityError("Eligibility manifest has no trial records.")
    snapshot = _build_snapshot(
        Path(plan_dir), Path(support_root), split, frozen_records=frozen_records
    )
    if _manifest_doc(snapshot) != doc:
        raise EligibilityError("Eligibility manifest differs from verified plan/support evidence.")
    return snapshot
