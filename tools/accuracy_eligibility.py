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
from typing import Any

SCHEMA = "accuracy-eligibility-manifest-v1"
EXPECTED_SPLIT_PORTS = {"development": 12, "validation": 4, "heldout": 8}
RANGES_M = (1000, 5000, 10000)
TYPE_LABELS = (True, False)


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


@dataclass(frozen=True)
class EligibilitySnapshot:
    """Plan-complete immutable eligibility population."""

    split: str
    plan_sha256: str
    config_sha256: str
    splits_sha256: str
    trial_table_sha256: str
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


def _support_count(support: Mapping[str, Any]) -> int:
    for field in (
        "detected_physical_catalog_matched_groups",
        "detected_physical_group_count",
        "support_count",
    ):
        value = support.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    raise EligibilityError("Support JSON lacks a nonnegative physical matched-group count.")


def _check_plan(
    plan_dir: Path, split: str
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
        "plan_sha256": _sha256(config_path),
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
    chosen = _split_name(split)
    ports = splits.get("ports")
    if not isinstance(ports, list):
        raise EligibilityError("Split manifest has no ports list.")
    selected_ports = {
        item.get("port_id")
        for item in ports
        if isinstance(item, dict) and item.get("effective_split") == chosen
    }
    expected_ports = EXPECTED_SPLIT_PORTS.get(chosen)
    if expected_ports is None or len(selected_ports) != expected_ports:
        raise EligibilityError(f"Unexpected port population for split {chosen!r}.")
    if len(rows) != config.get("trial_count"):
        raise EligibilityError("Trial table row count does not match the frozen plan.")
    ids = [row.get("trial_id", "") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise EligibilityError("Trial table has missing or duplicate trial IDs.")
    by_id = {row["trial_id"]: row for row in rows}
    ports_by_split = {
        name: {
            item.get("port_id")
            for item in ports
            if isinstance(item, dict) and item.get("effective_split") == name
        }
        for name in EXPECTED_SPLIT_PORTS
    }
    for row in rows:
        row_split = row.get("split")
        if (
            row_split not in EXPECTED_SPLIT_PORTS
            or row.get("port_id") not in ports_by_split[row_split]
        ):
            raise EligibilityError(f"Trial has wrong split/port metadata: {row.get('trial_id')}.")
        if row.get("sensor_condition") not in config.get("stress_profiles", {}):
            raise EligibilityError(f"Trial has unknown sensor condition: {row.get('trial_id')}.")
        expected_id = (
            f"{row_split}/{row['port_id']}/{row['placement_id']}/r{row['realization']}/"
            f"range{int(row['range_m'])}m/"
            f"types{'on' if row['report_types'] == 'True' else 'off'}/"
            f"{row['sensor_condition']}"
        )
        if row["trial_id"] != expected_id:
            raise EligibilityError(f"Trial ID does not match its metadata: {row.get('trial_id')}.")
        if row.get("profile_parameters") != json.dumps(
            config["stress_profiles"][row["sensor_condition"]], sort_keys=True
        ):
            raise EligibilityError(f"Trial condition parameters mismatch: {row.get('trial_id')}.")
        if row.get("range_m") not in {str(value) for value in RANGES_M}:
            raise EligibilityError(f"Trial has an unexpected range: {row.get('trial_id')}.")
        if row.get("report_types") not in {"True", "False"}:
            raise EligibilityError(
                f"Trial has invalid report-types metadata: {row.get('trial_id')}."
            )
        if row.get("status") != "planned":
            raise EligibilityError(f"Expected planned trial row: {row.get('trial_id')}.")
    moved = {
        decision.get("port_id")
        for decision in splits.get("overlap_decisions", [])
        if isinstance(decision, dict)
    }
    for item in ports:
        if not isinstance(item, dict):
            raise EligibilityError("Malformed port split entry.")
        pid = item.get("port_id")
        if item.get("effective_split") != item.get("declared_split") and pid not in moved:
            raise EligibilityError(f"Unrecorded split change for {pid}.")
    expected = [
        row for row in rows if row["split"] == chosen and row["sensor_condition"] == "nominal"
    ]
    nominal_ids = [row["trial_id"] for row in expected]
    expected_count = expected_ports * 25 * 2 * 3 * 2
    if len(expected) != expected_count:
        raise EligibilityError(
            f"Nominal population for {chosen} is {len(expected)}, expected {expected_count}."
        )
    if {row["port_id"] for row in expected} != selected_ports:
        raise EligibilityError(f"Nominal rows do not cover the complete {chosen} port set.")
    hashes["expected_nominal_ids_sha256"] = _canonical_ids_sha256(nominal_ids)
    hashes["expected_nominal_count"] = expected_count
    hashes["selected_split"] = chosen
    hashes["by_id"] = by_id
    hashes["expected_nominal_ids"] = nominal_ids
    return config, splits, rows, hashes


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
    _config, _splits, rows, hashes = _check_plan(plan_dir, split)
    chosen = hashes["selected_split"]
    table_by_id = hashes["by_id"]
    expected_ids = hashes["expected_nominal_ids"]
    prep_path = support_root / chosen / "preparation.csv"
    prep_rows = _read_csv(prep_path) if prep_path.is_file() else []
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
            if item.get(name) != planned.get(name):
                raise EligibilityError(f"Preparation metadata mismatch for {trial_id}: {name}.")
        prep_by_id[trial_id] = item

    frozen_by_id = {record["trial_id"]: record for record in frozen_records or []}
    records: list[EligibilityTrial] = []
    for row in rows:
        if row["split"] != chosen:
            continue
        nominal = row["sensor_condition"] == "nominal"
        support_rel = _support_relative_path(row)
        support_path = support_root / support_rel
        prep = prep_by_id.get(row["trial_id"])
        if support_path.is_file():
            support_hash = _sha256(support_path)
            support_doc = _read_json(support_path)
            if support_doc.get("supported") not in (True, False):
                raise EligibilityError(f"Support status is not boolean for {row['trial_id']}.")
            support_count = _support_count(support_doc)
            supported = support_doc["supported"]
            if support_doc.get("port_id") != row["port_id"]:
                raise EligibilityError(f"Support port mismatch for {row['trial_id']}.")
            if support_doc.get("placement_id") != row["placement_id"]:
                raise EligibilityError(f"Support placement mismatch for {row['trial_id']}.")
            expected_supported = (
                support_doc.get("valid_reference_water") is True and support_count >= 3
            )
            if supported is not expected_supported:
                raise EligibilityError(f"Support decision/count mismatch for {row['trial_id']}.")
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
            )
            old = frozen_by_id.get(row["trial_id"])
            if old is not None and old != _record_dict(record):
                raise EligibilityError(f"Frozen support evidence changed for {row['trial_id']}.")
            records.append(record)
        elif nominal and prep is not None and prep.get("status") == "failed":
            record = EligibilityTrial(
                trial_id=row["trial_id"],
                split=chosen,
                port_id=row["port_id"],
                placement_id=row["placement_id"],
                realization=row["realization"],
                range_m=int(row["range_m"]),
                report_types=row["report_types"] == "True",
                physical_seed=int(row["physical_seed"]),
                sensor_condition="nominal",
                status="unresolved",
                supported=None,
                support_count=None,
                support_path=support_rel.as_posix(),
                support_sha256=None,
            )
            old = frozen_by_id.get(row["trial_id"])
            if old is not None and old != _record_dict(record):
                raise EligibilityError(f"Frozen unresolved status changed for {row['trial_id']}.")
            records.append(record)
        elif nominal:
            raise EligibilityError(f"Missing support for planned nominal trial {row['trial_id']}.")
        # Stress rows are recorded only when support evidence is available.

    nominal_records = tuple(record for record in records if record.sensor_condition == "nominal")
    if tuple(record.trial_id for record in nominal_records) != tuple(expected_ids):
        raise EligibilityError(
            "Manifest does not cover the exact planned nominal trial population."
        )
    projections = {
        (range_m, report_types): tuple(
            record
            for record in nominal_records
            if record.range_m == range_m and record.report_types is report_types
        )
        for range_m in RANGES_M
        for report_types in TYPE_LABELS
    }
    expected_projection_count = EXPECTED_SPLIT_PORTS[chosen] * 25 * 2
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
        expected_nominal_count=hashes["expected_nominal_count"],
        expected_nominal_ids_sha256=hashes["expected_nominal_ids_sha256"],
        trials=tuple(records),
        nominal_projections=projections,
    )


def _manifest_doc(snapshot: EligibilitySnapshot) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": 1,
        "split": snapshot.split,
        "plan_sha256": snapshot.plan_sha256,
        "config_sha256": snapshot.config_sha256,
        "splits_sha256": snapshot.splits_sha256,
        "trial_table_sha256": snapshot.trial_table_sha256,
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
