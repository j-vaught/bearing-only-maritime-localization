"""Immutable, hash-verified development calibration parameters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Literal

SCORE_GAPS = (0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
RADIUS_LIMITS: tuple[float | None, ...] = (25.0, 50.0, 75.0, 100.0, 150.0, 200.0, None)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTEXT_KEYS = (
    "catalog_sha256",
    "map_sha256",
    "scope_manifest_sha256",
    "search_execution_sha256",
    "implementation_sha256",
)
_PROVENANCE_KEYS = (
    "plan_sha256",
    "trial_table_sha256",
    "run_manifest_sha256",
    "eligibility_manifest_sha256",
    "metrics_sha256",
)
_EXECUTION_PROTOCOL_HASH_KEY = "execution_protocol_sha256"
_EXECUTION_PROTOCOL_SCHEMA = "accuracy-execution-protocol-v1"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _validate_sha256(value: str, *, name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest.")


def validate_execution_protocol(value: object) -> dict[str, Any]:
    """Validate the formal subprocess timeout and process-group termination contract."""
    keys = {
        "schema",
        "timeout_s",
        "timeout_scope",
        "deadline_clock",
        "timeout_status",
        "termination_policy",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("execution_protocol must contain exactly the v1 protocol fields.")
    timeout_s = value["timeout_s"]
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not isfinite(float(timeout_s))
        or timeout_s <= 0.0
    ):
        raise ValueError("execution_protocol.timeout_s must be finite and positive.")
    constants = {
        "schema": _EXECUTION_PROTOCOL_SCHEMA,
        "timeout_scope": "per_command_subprocess",
        "deadline_clock": "monotonic_wall_clock_from_spawn",
        "timeout_status": "search_timeout",
    }
    for key, expected in constants.items():
        if value[key] != expected:
            raise ValueError(f"Unsupported execution_protocol.{key}.")
    policy = value["termination_policy"]
    expected_policy = {
        "start_new_session": True,
        "timeout_signal": "SIGTERM",
        "grace_s": 1.0,
        "escalation_signal": "SIGKILL",
        "reap_process_group": True,
    }
    if not isinstance(policy, dict) or set(policy) != set(expected_policy):
        raise ValueError("Unsupported execution_protocol termination policy.")
    if any(
        type(policy[key]) is not type(expected) or policy[key] != expected
        for key, expected in expected_policy.items()
    ):
        raise ValueError("Unsupported execution_protocol process-group termination policy.")
    return {
        **value,
        "termination_policy": dict(policy),
    }


def execution_protocol_sha256(value: object) -> str:
    """Return the canonical hash used by formal run manifests and trial rows."""
    protocol = validate_execution_protocol(value)
    return hashlib.sha256(canonical_json(protocol)).hexdigest()


@dataclass(frozen=True, slots=True)
class CalibrationOperatingCondition:
    """One range, type-use, objective, and search-algorithm condition."""

    range_m: float
    use_types: bool
    objective_version: str
    algorithm: str

    def __post_init__(self) -> None:
        if isinstance(self.range_m, bool) or not isinstance(self.range_m, (int, float)):
            raise TypeError("range_m must be numeric.")
        if not isfinite(float(self.range_m)) or self.range_m <= 0.0:
            raise ValueError("range_m must be finite and positive.")
        if type(self.use_types) is not bool:
            raise TypeError("use_types must be a boolean.")
        if not isinstance(self.objective_version, str) or not self.objective_version.strip():
            raise ValueError("objective_version must be a nonempty string.")
        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise ValueError("algorithm must be a nonempty string.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "range_m": float(self.range_m),
            "use_types": self.use_types,
            "objective_version": self.objective_version,
            "algorithm": self.algorithm,
        }

    @classmethod
    def from_dict(cls, value: object) -> CalibrationOperatingCondition:
        if not isinstance(value, dict) or set(value) != {
            "range_m",
            "use_types",
            "objective_version",
            "algorithm",
        }:
            raise ValueError("operating_condition must contain exactly the four v2 condition keys.")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CalibrationContext:
    """Content fingerprints binding a frozen decision threshold to its inputs."""

    catalog_sha256: str
    map_sha256: str
    scope_manifest_sha256: str
    search_execution_sha256: str
    implementation_sha256: str

    def __post_init__(self) -> None:
        for name in _CONTEXT_KEYS:
            _validate_sha256(getattr(self, name), name=name)

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _CONTEXT_KEYS}

    @classmethod
    def from_dict(cls, value: object) -> CalibrationContext:
        if not isinstance(value, dict) or set(value) != set(_CONTEXT_KEYS):
            raise ValueError("context must contain exactly the five v2 SHA-256 keys.")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class DecisionCalibration:
    """Frozen selection produced from development trials only."""

    min_score_gap: float
    max_95_radius_m: float | None
    split: str
    status: str
    availability: float
    conditional_accuracy: float
    accepted_p95_error_m: float | None
    artifact_sha256: str
    schema_version: int = 1
    verified_frozen_artifact: bool = False
    operating_condition: CalibrationOperatingCondition | None = None
    context: CalibrationContext | None = None
    provenance: dict[str, Any] | None = None
    population: dict[str, Any] | None = None
    release_eligible: bool = False
    execution_protocol: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in (1, 2) or self.split != "development":
            raise ValueError("Calibration must be schema v1/v2 and development-only.")
        if type(self.release_eligible) is not bool or (
            self.release_eligible and self.schema_version != 2
        ):
            raise ValueError("Only a schema-v2 calibration may be release eligible.")
        if self.execution_protocol is not None:
            validate_execution_protocol(self.execution_protocol)
        if self.release_eligible and self.execution_protocol is None:
            raise ValueError("Release-eligible calibration requires a formal execution protocol.")
        if self.min_score_gap not in SCORE_GAPS or self.max_95_radius_m not in RADIUS_LIMITS:
            raise ValueError("Calibration thresholds are outside the frozen grid.")
        if self.status not in ("PASS", "FAIL"):
            raise ValueError("Calibration status must be PASS or FAIL.")
        if not 0.0 <= self.availability <= 1.0 or not 0.0 <= self.conditional_accuracy <= 1.0:
            raise ValueError("Calibration rates must be within [0, 1].")
        if len(self.artifact_sha256) != 64:
            raise ValueError("Calibration artifact must carry a SHA-256 hash.")
        if self.schema_version == 2 and (
            self.operating_condition is None
            or self.context is None
            or self.provenance is None
            or self.population is None
        ):
            raise ValueError(
                "Schema-v2 calibration requires condition, context, provenance, and population."
            )
        if self.schema_version == 1 and (
            self.operating_condition is not None or self.context is not None
        ):
            raise ValueError("Schema-v1 calibration cannot carry v2 context.")


def calibration_payload(calibration: DecisionCalibration) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": calibration.schema_version,
        "split": calibration.split,
        "status": calibration.status,
        "thresholds": {
            "min_score_gap": calibration.min_score_gap,
            "max_95_radius_m": calibration.max_95_radius_m,
        },
        "metrics": {
            "availability": calibration.availability,
            "conditional_accuracy": calibration.conditional_accuracy,
            "accepted_p95_error_m": calibration.accepted_p95_error_m,
        },
    }
    if calibration.schema_version == 2:
        assert calibration.operating_condition is not None
        assert calibration.context is not None
        assert calibration.provenance is not None
        assert calibration.population is not None
        payload.update(
            operating_condition=calibration.operating_condition.as_dict(),
            context=calibration.context.as_dict(),
            provenance=calibration.provenance,
            population=calibration.population,
        )
        if calibration.execution_protocol is not None:
            payload["execution_protocol"] = validate_execution_protocol(
                calibration.execution_protocol
            )
    return payload


def search_execution_sha256(
    settings: Mapping[str, Any],
    *,
    seed_policy: Literal["fixed", "per_trial_variable"],
) -> str:
    """Hash normalized search settings and the explicit sampling-seed policy.

    Callers must pass only execution settings. Inputs identifying measurement,
    output, or artifact paths are rejected so these paths cannot affect parity.
    Under per-trial variability, seed and sampling_seed values are omitted while
    the policy itself remains part of the fingerprint.
    """
    if seed_policy not in ("fixed", "per_trial_variable"):
        raise ValueError("seed_policy must be fixed or per_trial_variable.")

    def clean(value: Any, *, key: str = "") -> Any:
        normalized = key.lower()
        if (
            any(token in normalized for token in ("path", "filepath", "directory"))
            or normalized in {"input", "inputs", "measurement", "measurements", "output", "out"}
            or normalized.endswith(("_dir", "_file", "_output", "_measurement"))
        ):
            raise ValueError(f"Path/output/measurement setting {key!r} is not fingerprintable.")
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for child_key, child_value in value.items():
                if not isinstance(child_key, str):
                    raise TypeError("Search execution setting keys must be strings.")
                child_normalized = child_key.lower()
                if seed_policy == "per_trial_variable" and child_normalized in {
                    "seed",
                    "sampling_seed",
                }:
                    continue
                result[child_key] = clean(child_value, key=child_key)
            return result
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    payload = {
        "schema": "search-execution-v1",
        "seed_policy": seed_policy,
        "settings": clean(settings),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def implementation_sha256(project_root: str | Path | None = None) -> str:
    """Hash implementation sources and a deterministic dependency manifest."""
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    source_paths = {
        *root.glob("maritime/search/**/*.py"),
        *root.glob("maritime/schemas/**/*.py"),
        *root.glob("maritime/geometry/**/*.py"),
        root / "maritime/serialization.py",
        root / "maritime/accuracy_cli.py",
    }
    if not source_paths or any(not path.is_file() for path in source_paths):
        raise ValueError("Implementation source set is missing.")
    inputs = {path.relative_to(root).as_posix(): path.read_bytes() for path in source_paths}
    project_manifest = root / "pyproject.toml"
    if project_manifest.is_file():
        inputs["dependency-binding/project-manifest/pyproject.toml"] = project_manifest.read_bytes()
    lock_path = root / "uv.lock"
    if lock_path.is_file():
        inputs["dependency-binding/uv.lock"] = lock_path.read_bytes()
    else:
        manifest_path = root / "pyproject.toml"
        if not manifest_path.is_file():
            manifests = sorted(
                [*root.glob("*.dist-info/METADATA"), *root.glob("*.egg-info/PKG-INFO")],
                key=lambda path: path.name,
            )
            if not manifests:
                raise ValueError(
                    "No dependency lock or package manifest is available for implementation hash."
                )
            for path in manifests:
                inputs[f"dependency-binding/installed-manifest/{path.name}"] = path.read_bytes()

    digest = hashlib.sha256()
    for relative, content in sorted(inputs.items()):
        name = relative.encode()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _validate_v2_provenance(
    provenance: object, *, require_execution_protocol: bool = False
) -> dict[str, Any]:
    if not isinstance(provenance, dict) or set(provenance) != {"development", "validation"}:
        raise ValueError("v2 provenance must contain development and validation entries.")
    normalized: dict[str, Any] = {}
    protocol_presence: set[bool] = set()
    for split in ("development", "validation"):
        record = provenance[split]
        if not isinstance(record, dict):
            raise ValueError(f"{split} provenance must be an object.")
        has_protocol_hash = _EXECUTION_PROTOCOL_HASH_KEY in record
        protocol_presence.add(has_protocol_hash)
        expected_keys = set(_PROVENANCE_KEYS)
        if has_protocol_hash:
            expected_keys.add(_EXECUTION_PROTOCOL_HASH_KEY)
        if set(record) != expected_keys:
            raise ValueError(f"{split} provenance has missing or unsupported source hashes.")
        for key in _PROVENANCE_KEYS:
            _validate_sha256(record[key], name=f"{split}.{key}")
        if has_protocol_hash:
            _validate_sha256(
                record[_EXECUTION_PROTOCOL_HASH_KEY],
                name=f"{split}.{_EXECUTION_PROTOCOL_HASH_KEY}",
            )
        normalized[split] = dict(record)
    if len(protocol_presence) != 1:
        raise ValueError("Both splits must carry the same execution protocol provenance.")
    if require_execution_protocol and protocol_presence != {True}:
        raise ValueError("V2 release provenance requires execution_protocol_sha256 per split.")
    return normalized


def _validate_v2_population(population: object) -> dict[str, Any]:
    if not isinstance(population, dict) or set(population) != {"development", "validation"}:
        raise ValueError("v2 population must contain development and validation entries.")
    categories = {
        "included_nominal_supported",
        "excluded_stress",
        "excluded_nominal_unsupported",
        "all_condition_trials",
    }
    normalized: dict[str, Any] = {}
    for split in ("development", "validation"):
        split_population = population[split]
        if not isinstance(split_population, dict) or set(split_population) != categories:
            raise ValueError(f"{split} population is incomplete.")
        normalized_split: dict[str, Any] = {}
        for category in sorted(categories):
            values = split_population[category]
            if not isinstance(values, dict) or set(values) != {"count", "trial_ids_sha256"}:
                raise ValueError(f"{split}.{category} population record is malformed.")
            count = values["count"]
            if type(count) is not int or count < 0:
                raise ValueError(f"{split}.{category}.count must be a nonnegative integer.")
            _validate_sha256(
                values["trial_ids_sha256"], name=f"{split}.{category}.trial_ids_sha256"
            )
            normalized_split[category] = dict(values)
        if normalized_split["included_nominal_supported"]["count"] < 1:
            raise ValueError(f"{split} must include supported nominal trials.")
        if (
            normalized_split["all_condition_trials"]["count"]
            != normalized_split["included_nominal_supported"]["count"]
            + normalized_split["excluded_stress"]["count"]
            + normalized_split["excluded_nominal_unsupported"]["count"]
        ):
            raise ValueError(f"{split} population counts do not reconcile.")
        normalized[split] = normalized_split
    return normalized


def load_frozen_calibration(
    path: str | Path,
    *,
    expected_condition: CalibrationOperatingCondition | None = None,
    expected_context: CalibrationContext | None = None,
    release_acceptance: bool = False,
) -> DecisionCalibration:
    """Verify frozen calibration; v2 release use requires matching condition/context."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        document.get("split") != "development"
        or document.get("frozen") is not True
        or document.get("validation_confirmed") is not True
        or not isinstance(document.get("validation_metrics"), dict)
    ):
        raise ValueError(
            "Runtime calibration must be frozen, dev-selected, and validation-confirmed."
        )
    payload = {key: value for key, value in document.items() if key not in ("frozen", "sha256")}
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    if document.get("sha256") != actual:
        raise ValueError("Frozen calibration SHA-256 verification failed.")
    version = document.get("schema_version")
    condition: CalibrationOperatingCondition | None = None
    context: CalibrationContext | None = None
    execution_protocol: dict[str, Any] | None = None
    provenance = None
    population = None
    release_eligible = False
    if version == 1:
        if release_acceptance:
            raise ValueError("Schema-v1 calibration cannot establish release acceptance.")
    elif version == 2:
        condition = CalibrationOperatingCondition.from_dict(document.get("operating_condition"))
        context = CalibrationContext.from_dict(document.get("context"))
        protocol_value = document.get("execution_protocol")
        protocol_hashes_present = (
            any(
                isinstance(record, dict) and _EXECUTION_PROTOCOL_HASH_KEY in record
                for record in document.get("provenance", {}).values()
            )
            if isinstance(document.get("provenance"), dict)
            else False
        )
        if protocol_value is None:
            if protocol_hashes_present:
                raise ValueError("V2 provenance declares a missing execution protocol.")
            if release_acceptance:
                raise ValueError("Schema-v2 release use requires a formal execution protocol.")
            provenance = _validate_v2_provenance(document.get("provenance"))
        else:
            execution_protocol = validate_execution_protocol(protocol_value)
            protocol_digest = execution_protocol_sha256(execution_protocol)
            provenance = _validate_v2_provenance(
                document.get("provenance"), require_execution_protocol=True
            )
            if any(
                record[_EXECUTION_PROTOCOL_HASH_KEY] != protocol_digest
                for record in provenance.values()
            ):
                raise ValueError("Frozen execution protocol hash does not match its provenance.")
        population = _validate_v2_population(document.get("population"))
        if expected_condition is None or expected_context is None:
            if release_acceptance:
                raise ValueError("Schema-v2 release use requires expected condition and context.")
        elif condition != expected_condition or context != expected_context:
            raise ValueError("Calibration operating condition or execution context mismatch.")
        elif protocol_value is not None:
            release_eligible = True
    else:
        raise ValueError("Unsupported frozen calibration schema version.")

    thresholds = document["thresholds"]
    metrics = document["metrics"]
    return DecisionCalibration(
        min_score_gap=thresholds["min_score_gap"],
        max_95_radius_m=thresholds["max_95_radius_m"],
        split=document["split"],
        status=document["status"],
        availability=metrics["availability"],
        conditional_accuracy=metrics["conditional_accuracy"],
        accepted_p95_error_m=metrics["accepted_p95_error_m"],
        artifact_sha256=actual,
        schema_version=version,
        verified_frozen_artifact=True,
        operating_condition=condition,
        context=context,
        provenance=provenance,
        population=population,
        release_eligible=release_eligible,
        execution_protocol=execution_protocol,
    )
