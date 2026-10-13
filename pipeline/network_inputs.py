#!/usr/bin/env python3
"""Resolve paired network inputs, recording any synthesized counterpart.

Source files are never modified. Derived 0/1 or relative weights are not
estimates of network-membership probability or statistical confidence.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import nibabel as nib
from nibabel.cifti2 import cifti2_axes
import numpy as np
from scipy.io import savemat

from prepare_targets import (
    cortical_data, cortex_mapping, file_sha256, label_axis_names,
    network_metadata, normalized_name,
)


NETWORK_INPUT_KEYS = (
    "categorical_networks", "network_probabilities", "network_metadata",
)
DEFAULTS = {
    "enabled": True,
    "weighted_to_binary_percentile": None,
    "weight_polarity": "positive",
    "weight_normalization": "auto",
    "map_names": None,
}


def fallback_options(config: dict) -> dict:
    supplied = config.get("input_fallbacks") or {}
    if not isinstance(supplied, dict):
        raise ValueError("input_fallbacks must be a mapping")
    unknown = set(supplied) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown input_fallbacks options: {sorted(unknown)}")
    options = {**DEFAULTS, **supplied}
    if not isinstance(options["enabled"], bool):
        raise ValueError("input_fallbacks.enabled must be true or false")
    percentile = options["weighted_to_binary_percentile"]
    if percentile is not None:
        if (isinstance(percentile, bool) or not isinstance(percentile, (int, float))
                or not np.isfinite(percentile) or not 0 <= percentile <= 100):
            raise ValueError("weighted_to_binary_percentile must be a number in [0, 100]")
        options["weighted_to_binary_percentile"] = float(percentile)
    if options["weight_polarity"] not in ("positive", "negative", "absolute"):
        raise ValueError("weight_polarity must be positive, negative, or absolute")
    if options["weight_normalization"] not in ("auto", "max", "none"):
        raise ValueError("weight_normalization must be auto, max, or none")
    names = options["map_names"]
    if names is not None:
        if not isinstance(names, list) or not names:
            raise ValueError("input_fallbacks.map_names must be a nonempty list or null")
        validate_names(names)
    return options


def validate_names(names: list[str]) -> None:
    if not names or any(not isinstance(name, str) or not normalized_name(name)
                        or name.strip().isdigit() or any(char in name for char in '\n\r\0')
                        for name in names):
        raise ValueError("Network names must be nonempty, nonnumeric strings")
    keys = [normalized_name(name) for name in names]
    if len(keys) != len(set(keys)) or "noise" in keys:
        raise ValueError("Network names must be unique; Noise is reserved for background")


def file_record(value) -> dict:
    if value in (None, ""):
        return {"path": None, "sha256": None}
    path = Path(value).expanduser().resolve()
    if path.exists() and not path.is_file():
        raise ValueError(f"Network input is not a file: {path}")
    return {"path": str(path), "sha256": file_sha256(path) if path.is_file() else None}


def request_signature(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def default_colors(count: int) -> np.ndarray:
    return np.asarray([colorsys.hsv_to_rgb((index * 0.61803398875) % 1, 0.65, 0.85)
                       for index in range(count)])


def metadata_for_image(image, mode: str, metadata_path: str | None,
                       options: dict) -> tuple[list[str], np.ndarray]:
    if mode == "wta_only" and options["map_names"] is not None:
        raise ValueError("map_names renames weighted-only rows; WTA names come from its label table")
    if options["map_names"] is not None:
        names = options["map_names"]
        colors = default_colors(len(names))
    elif metadata_path:
        names, colors = network_metadata(Path(metadata_path))
        colors = np.atleast_2d(colors)
    elif mode == "wta_only":
        entries = image.header.get_axis(0).label[0]
        keys = [key for key, name in label_axis_names(image).items()
                if normalized_name(name) != "noise"]
        names = [str(entries[key][0]) for key in keys]
        colors = np.asarray([entries[key][1][:3] for key in keys], dtype=float)
    else:
        axis = image.header.get_axis(0)
        if not isinstance(axis, cifti2_axes.ScalarAxis):
            raise ValueError("Weighted-only CIFTI needs network_metadata or input_fallbacks.map_names "
                             "in row order (or a named .dscalar.nii file)")
        names = [str(name) for name in axis.name]
        colors = default_colors(len(names))
    validate_names(names)
    if colors.shape != (len(names), 3) or not np.all(np.isfinite(colors)) or np.any(
            (colors < 0) | (colors > 1)):
        raise ValueError("Network metadata must provide one RGB color in [0, 1] per network")
    if mode == "weighted_only" and len(names) != image.shape[0]:
        raise ValueError("Weighted map count does not match network metadata/names. For a custom "
                         "map, set network_metadata: null and supply map_names in row order")
    return names, colors


def one_hot_weights(image, names: list[str]) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    mapping = cortex_mapping(image)
    labels = cortical_data(image, mapping)[0]
    entries = label_axis_names(image)
    if not np.all(np.isfinite(labels)) or np.any(labels != np.round(labels)):
        raise ValueError("Categorical cortical values must be finite integer label keys")
    unknown_keys = set(np.unique(labels).astype(int)) - {0} - set(entries)
    if unknown_keys:
        raise ValueError(f"Categorical values are missing from the label table: {sorted(unknown_keys)}")
    name_to_row = {normalized_name(name): index for index, name in enumerate(names)}
    entry_names = [normalized_name(name) for name in entries.values()
                   if normalized_name(name) != "noise"]
    if len(entry_names) != len(set(entry_names)):
        raise ValueError("Categorical label names must be unique after normalization")
    weights = np.zeros((len(names), len(labels)), dtype=np.float32)
    categorical = np.zeros(len(labels), dtype=np.int32)
    remapping = []
    for key, name in entries.items():
        normalized = normalized_name(name)
        if normalized == "noise":
            continue
        if normalized not in name_to_row:
            raise ValueError(f"WTA label {name!r} is absent from network_metadata; use matching "
                             "metadata or set network_metadata: null to use the label table")
        row = name_to_row[normalized]
        support = labels == key
        weights[row, support] = 1.0
        categorical[support] = row + 1
        remapping.append({"name": names[row], "source_label_key": key,
                          "resolved_label_key": row + 1,
                          "vertex_count": int(support.sum())})
    if not np.any(categorical):
        raise ValueError("WTA map has no labeled cortical target support")
    return weights, categorical, remapping


def threshold_weights(values: np.ndarray, names: list[str], options: dict
                      ) -> tuple[np.ndarray, np.ndarray, list[dict], int]:
    """Threshold nonzero selected-polarity cortex before any anatomical mask.

    Percentiles use NumPy's linear interpolation and include ties. Competing
    maps resolve to the largest normalized eligible weight, then first row.
    """
    percentile = options["weighted_to_binary_percentile"]
    if percentile is None:
        raise ValueError("Weighted-only input requires input_fallbacks.weighted_to_binary_percentile "
                         "(0–100; for example 90 retains the top 10% of positive cortical values, "
                         "including ties)")
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    cleaned = np.where(finite, values, 0.0)
    polarity = options["weight_polarity"]
    weights = (np.abs(cleaned) if polarity == "absolute" else
               np.maximum(-cleaned if polarity == "negative" else cleaned, 0.0))
    eligible = np.zeros_like(weights, dtype=bool)
    details = []
    for index, (row, name) in enumerate(zip(weights, names)):
        positive = row > 0
        maximum = float(row.max()) if row.size else 0.0
        normalization = options["weight_normalization"]
        if normalization == "none" and maximum > 1:
            raise ValueError(f"{name}: weights exceed 1; use weight_normalization: auto or max")
        scale = maximum if maximum > 0 and (normalization == "max" or maximum > 1) else 1.0
        raw_threshold = float(np.percentile(row[positive], percentile, method="linear")) if np.any(positive) else None
        if raw_threshold is not None:
            eligible[index] = positive & (row >= raw_threshold)
        row /= scale
        details.append({"name": name, "percentile": percentile,
                        "selected_polarity_vertex_count": int(positive.sum()),
                        "excluded_nonfinite_vertex_count": int((~finite[index]).sum()),
                        "excluded_opposite_polarity_vertex_count": int((
                            (cleaned[index] < 0) if polarity == "positive" else
                            (cleaned[index] > 0) if polarity == "negative" else
                            np.zeros(len(row), dtype=bool)).sum()),
                        "normalization_divisor": scale,
                        "threshold_before_normalization": raw_threshold,
                        "threshold_after_normalization": raw_threshold / scale if raw_threshold is not None else None,
                        "vertices_at_or_above_cutoff": int(eligible[index].sum())})
    overlap_count = int((eligible.sum(axis=0) > 1).sum())
    categorical = np.argmax(np.where(eligible, weights, -1), axis=0).astype(np.int32) + 1
    categorical[~np.any(eligible, axis=0)] = 0
    if not np.any(categorical):
        raise ValueError(f"Weighted maps contain no finite, nonzero {polarity} cortical values")
    for index, detail in enumerate(details):
        detail["resolved_binary_vertex_count"] = int((categorical == index + 1).sum())
    return weights.astype(np.float32), categorical, details, overlap_count


def validate_cached_manifest(manifest: dict) -> None:
    for key, record in manifest["request"]["inputs"].items():
        if file_record(record["path"]) != record:
            raise ValueError(f"Network source changed since preparation ({key}); use a fresh output directory")
    for key, record in manifest["resolved_inputs"].items():
        if file_record(record["path"]) != record:
            raise ValueError(f"Resolved network input changed or is missing ({key}); use a fresh output directory")


def adopt_manifest(config: dict, manifest: dict, path: Path) -> dict:
    config["inputs"].update({key: record["path"]
                             for key, record in manifest["resolved_inputs"].items()})
    provenance = {key: manifest[key] for key in ("mode", "summary", "request_sha256")}
    provenance["manifest"] = str(path)
    config["network_input_provenance"] = provenance
    return provenance


def resolve_network_inputs(config: dict, output_root: str | Path) -> dict:
    """Mutate config to effective paths; reuse only an identical validated request."""
    options = fallback_options(config)
    current = config.get("network_input_provenance")
    if current:
        path = Path(current["manifest"])
        manifest = json.loads(path.read_text())
        if (manifest["request"]["options"] != options or
                current["request_sha256"] != manifest["request_sha256"] or
                any(file_record(config["inputs"].get(key)) != record
                    for key, record in manifest["resolved_inputs"].items())):
            raise ValueError("Resolved network input configuration changed; restart from the source "
                             "configuration in a fresh output directory")
        validate_cached_manifest(manifest)
        return adopt_manifest(config, manifest, path)

    request = {"inputs": {key: file_record(config["inputs"].get(key))
                          for key in NETWORK_INPUT_KEYS}, "options": options}
    signature = request_signature(request)
    root = Path(output_root).expanduser().resolve()
    manifest_path = root / "NetworkInputManifest.json"
    if root.exists():
        if not manifest_path.is_file():
            raise FileExistsError(f"Incomplete network inputs require review: {root}")
        manifest = json.loads(manifest_path.read_text())
        if manifest["request_sha256"] != signature:
            raise ValueError("Network sources or fallback settings changed; use a fresh output directory")
        validate_cached_manifest(manifest)
        return adopt_manifest(config, manifest, manifest_path)

    inputs = request["inputs"]
    has_wta = inputs["categorical_networks"]["sha256"] is not None
    has_weights = inputs["network_probabilities"]["sha256"] is not None
    if not has_wta and not has_weights:
        raise FileNotFoundError("Supply at least one existing categorical_networks (WTA .dlabel.nii) "
                                "or network_probabilities (weighted CIFTI) input")
    mode = "provided_pair" if has_wta and has_weights else "wta_only" if has_wta else "weighted_only"
    if mode != "provided_pair" and not options["enabled"]:
        raise ValueError("Both network maps are required when input_fallbacks.enabled is false")
    metadata_record = inputs["network_metadata"]
    if metadata_record["path"] and not metadata_record["sha256"]:
        raise FileNotFoundError(f"Missing network metadata: {metadata_record['path']}; "
                                "set network_metadata: null to infer names for single-representation inputs")
    manifest = {"schema_version": 1, "mode": mode, "request": request,
                "request_sha256": signature, "resolved_inputs": {}}
    if mode == "provided_pair":
        if not metadata_record["sha256"]:
            raise ValueError("Paired inputs require network_metadata matching their names, order, and colors")
        manifest["summary"] = "Both categorical and weighted maps supplied; no counterpart synthesized."
        manifest["resolved_inputs"] = inputs
    else:
        source = inputs["categorical_networks" if has_wta else "network_probabilities"]["path"]
        image = nib.load(source)
        if not isinstance(image, nib.Cifti2Image):
            raise ValueError("Network inputs must be CIFTI files in the subject's cortical surface space")
        if has_wta:
            label_axis_names(image)
        elif not isinstance(image.header.get_axis(0), (cifti2_axes.ScalarAxis, cifti2_axes.SeriesAxis)):
            raise ValueError("Weighted network input must be a scalar/series CIFTI, not a label map")
        mapping = cortex_mapping(image)
        names, colors = metadata_for_image(image, mode, metadata_record["path"], options)
        if has_wta:
            weights, categorical, details = one_hot_weights(image, names)
            manifest["label_remapping"] = details
            manifest["summary"] = (
                "WTA-only input: generated 0/1 weights (1 inside each label, 0 outside). "
                "These are binary stand-ins, not measured membership probabilities or confidence.")
        else:
            weights, categorical, details, overlap_count = threshold_weights(
                cortical_data(image, mapping), names, options)
            manifest.update({"maps": details, "overlap_vertex_count": overlap_count,
                             "percentile_domain": "finite nonzero selected-polarity cortical vertices, before anatomical masking",
                             "percentile_method": "linear", "cutoff_comparison": ">= (ties retained)",
                             "overlap_rule": "largest normalized qualifying weight; first input row breaks ties"})
            manifest["summary"] = (
                f"Weighted-only input: binary support derived at the {options['weighted_to_binary_percentile']:g}th "
                f"percentile of nonzero {options['weight_polarity']} cortical values, before anatomical masking "
                "(ties retained). Overlaps go to the largest normalized qualifying weight, then first row. "
                "Weights are relative target representations, not calibrated probabilities or statistical confidence. "
                "Unassigned cortex is background, not an inferred network atlas.")
        manifest["networks"] = names
        manifest["interpretation"] = "Synthesized counterparts do not recover uncertainty or missing biological information."

    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{root.name}-", dir=root.parent) as temporary:
        # Publish a child directory so the context manager still owns an
        # existing directory when it cleans up, including on older Python.
        work = Path(temporary) / "payload"
        work.mkdir()
        if mode != "provided_pair":
            label_table = {0: ("Unassigned", (0.5, 0.5, 0.5, 0.0))}
            label_table.update({index + 1: (name, (*colors[index], 1.0))
                                for index, name in enumerate(names)})
            products = {
                "categorical_networks": "ResolvedNetworks.dlabel.nii",
                "network_probabilities": "ResolvedNetworkWeights.dscalar.nii",
                "network_metadata": "NetworkMetadata.mat",
            }
            nib.save(nib.Cifti2Image(categorical[np.newaxis], nib.cifti2.Cifti2Header.from_axes(
                (cifti2_axes.LabelAxis(["Resolved network support"], [label_table]), mapping.axis))),
                work / products["categorical_networks"])
            nib.save(nib.Cifti2Image(weights, nib.cifti2.Cifti2Header.from_axes(
                (cifti2_axes.ScalarAxis(names), mapping.axis))), work / products["network_probabilities"])
            # The trailing background entry preserves the existing resource format
            # and keeps singleton color arrays two-dimensional after loadmat squeeze.
            savemat(work / products["network_metadata"], {"Priors": {
                "NetworkLabels": np.asarray([*names, "Noise"], dtype=object),
                "NetworkColors": np.vstack((colors, [0.5, 0.5, 0.5])),
            }})
            manifest["resolved_inputs"] = {
                key: {"path": str(root / filename), "sha256": file_sha256(work / filename)}
                for key, filename in products.items()}
        (work / manifest_path.name).write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        work.rename(root)
    if mode != "provided_pair":
        print(f"[python-tans] {manifest['summary']}")
        for key in ("categorical_networks", "network_probabilities"):
            if inputs[key]["path"] and not inputs[key]["sha256"]:
                print(f"[python-tans] counterpart not found: {inputs[key]['path']}")
    return adopt_manifest(config, manifest, manifest_path)


def validate_prepared_network_inputs(config: dict, preparation: dict) -> None:
    """Do not reuse prepared targets after changing maps or conversion policy."""
    for key in NETWORK_INPUT_KEYS:
        if file_record(config["inputs"].get(key)) != preparation["inputs"].get(key):
            raise ValueError(f"Prepared targets use different network inputs ({key}); use a fresh output directory")
    previous = preparation.get("network_input_provenance")
    if previous and previous["request_sha256"] != config["network_input_provenance"]["request_sha256"]:
        raise ValueError("Prepared targets use different fallback settings; use a fresh output directory")


def validate_requested_networks(config: dict) -> None:
    from prepare_targets import resolve_unique_name

    names, _ = network_metadata(Path(config["inputs"]["network_metadata"]))
    categorical_image = nib.load(config["inputs"]["categorical_networks"])
    entries = label_axis_names(categorical_image)
    categorical = cortical_data(categorical_image, cortex_mapping(categorical_image))[0]
    for spec in config["targets"]:
        requested = str(spec["network"])
        try:
            name = names[resolve_unique_name(names, requested, "target network")]
            keys = [key for key, label in entries.items() if normalized_name(label) == normalized_name(name)]
            if len(keys) != 1 or not np.any(categorical == keys[0]):
                raise ValueError(f"No cortical binary support for {name}")
        except ValueError as exc:
            motor_note = (" A missing motor network cannot be inferred from a treatment map. "
                          "Supply Somatomotor_Hand or use --skip-sm-hand-calibration "
                          "(no motor-referenced dose transfer)." if requested == "Somatomotor_Hand" else "")
            raise ValueError(f"Requested target {requested!r} is unavailable in the resolved inputs.{motor_note} "
                             f"Details: {exc}") from exc
