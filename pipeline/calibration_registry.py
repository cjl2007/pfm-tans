#!/usr/bin/env python3
"""Resolve and validate stimulator %MSO-to-dI/dt calibration resources."""

import csv
import json
import math
from pathlib import Path


STANDARD_VALUE_COLUMNS = (
    "realized_didt_A_per_us",
    "didt_A_A_per_us",
)


class CalibrationSelectionError(ValueError):
    """Raised when dosing calibration requires an explicit user decision."""


def require_file(value, label):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def load_registry(path):
    registry_path = require_file(path, "calibration registry")
    registry = json.loads(registry_path.read_text())
    if registry.get("schema_version") != "tans-stimulator-calibration-registry-v1":
        raise ValueError(f"Unsupported calibration registry: {registry_path}")
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Calibration registry has no entries: {registry_path}")
    identifiers = [str(entry.get("id", "")) for entry in entries]
    if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Calibration registry IDs must be nonempty and unique")
    return registry_path, entries


def validate_table(path, value_column=None):
    table = require_file(path, "stimulator calibration CSV")
    with table.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = set(reader.fieldnames or [])
    if "amplitude_pct_mso" not in fields:
        raise ValueError(f"Calibration lacks amplitude_pct_mso: {table}")
    if value_column is None:
        value_column = next(
            (column for column in STANDARD_VALUE_COLUMNS if column in fields), None)
    if not value_column or value_column not in fields:
        raise ValueError(
            f"Calibration lacks a supported dI/dt column {STANDARD_VALUE_COLUMNS}: {table}")
    if len(rows) < 2:
        raise ValueError(f"Calibration requires at least two rows: {table}")
    mso = [float(row["amplitude_pct_mso"]) for row in rows]
    didt = [float(row[value_column]) for row in rows]
    if any(not math.isfinite(value) for value in mso + didt):
        raise ValueError(f"Calibration contains non-finite values: {table}")
    if any(right <= left for left, right in zip(mso, mso[1:])):
        raise ValueError(f"Calibration %MSO must be strictly increasing: {table}")
    if any(right <= left for left, right in zip(didt, didt[1:])):
        raise ValueError(f"Calibration dI/dt must be strictly increasing: {table}")
    return {
        "csv": str(table),
        "value_column": value_column,
        "mso_range": [mso[0], mso[-1]],
        "didt_range_A_per_us": [didt[0], didt[-1]],
        "rows": len(rows),
    }


def _entry_with_resolved_table(entry, registry_path):
    resolved = dict(entry)
    table = Path(str(entry["csv"])).expanduser()
    if not table.is_absolute():
        table = registry_path.parent / table
    validation = validate_table(table, entry.get("value_column"))
    declared = [float(value) for value in entry.get(
        "mso_range", validation["mso_range"])]
    if declared != validation["mso_range"]:
        raise ValueError(
            f"Registry/table MSO range mismatch for {entry['id']}: "
            f"{declared} versus {validation['mso_range']}")
    resolved.update(validation)
    resolved["registry"] = str(registry_path)
    return resolved


def _role_candidates(entries, coil_basename, role):
    return [
        entry for entry in entries
        if str(entry.get("simnibs_coil_model_basename")) == coil_basename
        and (not entry.get("intended_roles") or role in entry["intended_roles"])
    ]


def resolve_calibration(registry, coil_model, requested, role,
                        allow_similar=False, substitution_justification=""):
    registry_path, entries = load_registry(registry)
    coil_path = require_file(coil_model, f"{role} SimNIBS coil model")
    coil_basename = coil_path.name
    exact = _role_candidates(entries, coil_basename, role)
    by_id = {str(entry["id"]): entry for entry in entries}

    if not requested:
        if len(exact) == 1:
            entry = exact[0]
        else:
            choices = ", ".join(entry["id"] for entry in exact) or "none"
            reason = "ambiguous" if exact else "missing"
            raise CalibrationSelectionError(
                f"Exact {role} dI/dt calibration is {reason} for {coil_basename}. "
                f"Registry choices matching this simulation geometry: {choices}. "
                f"Specify the calibration ID or a CSV path. To use another coil's "
                f"table, also enable similar-coil substitution and provide a justification.")
        resolved = _entry_with_resolved_table(entry, registry_path)
        resolved["selection_mode"] = "automatic_unique_exact_registry_match"
        resolved["is_exact_simnibs_coil_match"] = True
    else:
        candidate_path = Path(str(requested)).expanduser()
        if candidate_path.is_file():
            validation = validate_table(candidate_path)
            resolved = {
                "id": f"user-provided:{candidate_path.stem}",
                "coil_hardware_name": coil_path.stem.replace("_", " "),
                "simnibs_coil_model_basename": coil_basename,
                "intended_roles": [role],
                "status": "user_provided",
                "registry": None,
                **validation,
                "selection_mode": "user_provided_exact_for_configured_coil",
                "is_exact_simnibs_coil_match": True,
            }
        else:
            if str(requested) not in by_id:
                choices = ", ".join(sorted(by_id))
                raise CalibrationSelectionError(
                    f"Unknown calibration {requested!r}. Available registry IDs: {choices}; "
                    "or provide a CSV path.")
            entry = by_id[str(requested)]
            resolved = _entry_with_resolved_table(entry, registry_path)
            is_exact = str(entry.get(
                "simnibs_coil_model_basename")) == coil_basename
            resolved["is_exact_simnibs_coil_match"] = is_exact
            resolved["selection_mode"] = (
                "explicit_exact_registry_entry" if is_exact
                else "explicit_similar_coil_substitution")
            if not is_exact:
                if not allow_similar:
                    raise CalibrationSelectionError(
                        f"Calibration {entry['id']} is for "
                        f"{entry.get('simnibs_coil_model_basename')}, not {coil_basename}. "
                        "Enable similar-coil substitution and provide a justification to proceed.")
                if not str(substitution_justification).strip():
                    raise CalibrationSelectionError(
                        "Similar-coil calibration substitution requires a nonempty justification.")

    resolved["configured_simnibs_coil_model"] = str(coil_path)
    resolved["substitution_justification"] = (
        str(substitution_justification).strip()
        if resolved["selection_mode"] == "explicit_similar_coil_substitution"
        else None)
    return resolved
