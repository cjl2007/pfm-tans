#!/usr/bin/env python3
"""Prepare individualized TANS targets without a MATLAB runtime."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import nibabel as nib
from nibabel.cifti2 import cifti2_axes
import numpy as np
from scipy.io import loadmat
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
import yaml


CORTEX_STRUCTURES = (
    "CIFTI_STRUCTURE_CORTEX_LEFT",
    "CIFTI_STRUCTURE_CORTEX_RIGHT",
)


@dataclass(frozen=True)
class CortexMapping:
    axis: cifti2_axes.BrainModelAxis
    grayordinate_indices: np.ndarray
    full_surface_vertices: np.ndarray
    hemisphere: np.ndarray
    left_surface_vertices: int
    right_surface_vertices: int


@dataclass(frozen=True)
class SurfaceGeometry:
    pial_xyz: np.ndarray
    cortical_faces: np.ndarray


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")


def load_yaml(path: str | Path) -> dict:
    value = yaml.safe_load(require_file(path, "configuration").read_text())
    if not isinstance(value, dict):
        raise ValueError("Configuration root must be a mapping")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_unique_name(names: Sequence[str], requested: str, label: str) -> int:
    token = str(requested).strip()
    if token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(names):
            return index
        raise ValueError(
            f"{label} number must be between 1 and {len(names)}; got {requested!r}")
    key = normalized_name(requested)
    matches = [index for index, name in enumerate(names)
               if normalized_name(str(name)) == key]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {label} named {requested!r}; found {len(matches)}")
    return matches[0]


def network_metadata(path: Path) -> Tuple[List[str], np.ndarray]:
    priors = loadmat(path, squeeze_me=True, struct_as_record=False)["Priors"]
    names = [str(value) for value in np.atleast_1d(priors.NetworkLabels)]
    colors = np.atleast_2d(np.asarray(priors.NetworkColors, dtype=float))
    if names and normalized_name(names[-1]) == "noise":
        names = names[:-1]
        colors = colors[:-1]
    return names, colors


def label_axis_names(image: nib.Cifti2Image) -> Dict[int, str]:
    axis = image.header.get_axis(0)
    if not isinstance(axis, cifti2_axes.LabelAxis) or len(axis) != 1:
        raise ValueError("Categorical network input must have one CIFTI label map")
    return {int(key): str(value[0]) for key, value in axis.label[0].items()
            if int(key) != 0}


def cortex_mapping(image: nib.Cifti2Image) -> CortexMapping:
    axis = image.header.get_axis(1)
    if not isinstance(axis, cifti2_axes.BrainModelAxis):
        raise ValueError("CIFTI second axis must be a BrainModelAxis")
    gray_indices: List[np.ndarray] = []
    full_vertices: List[np.ndarray] = []
    hemispheres: List[np.ndarray] = []
    surface_counts: Dict[str, int] = {}
    for name, slc, model in axis.iter_structures():
        if name not in CORTEX_STRUCTURES:
            continue
        indices = np.arange(len(axis), dtype=np.int64)[slc]
        vertices = np.asarray(model.vertex, dtype=np.int64)
        if len(indices) != len(vertices):
            raise ValueError(f"CIFTI vertex mapping mismatch for {name}")
        count = int(model.nvertices[name])
        surface_counts[name] = count
        gray_indices.append(indices)
        if name == CORTEX_STRUCTURES[0]:
            full_vertices.append(vertices)
            hemispheres.append(np.full(len(vertices), "L", dtype="U1"))
        else:
            full_vertices.append(vertices + surface_counts.get(
                CORTEX_STRUCTURES[0], count))
            hemispheres.append(np.full(len(vertices), "R", dtype="U1"))
    if set(surface_counts) != set(CORTEX_STRUCTURES):
        raise ValueError("CIFTI must contain both left and right cortical structures")
    return CortexMapping(
        axis=axis[np.concatenate(gray_indices)],
        grayordinate_indices=np.concatenate(gray_indices),
        full_surface_vertices=np.concatenate(full_vertices),
        hemisphere=np.concatenate(hemispheres),
        left_surface_vertices=surface_counts[CORTEX_STRUCTURES[0]],
        right_surface_vertices=surface_counts[CORTEX_STRUCTURES[1]],
    )


def cortical_data(image: nib.Cifti2Image, mapping: CortexMapping) -> np.ndarray:
    data = np.asarray(image.dataobj)
    if data.ndim != 2:
        raise ValueError("CIFTI data must be two-dimensional")
    return np.asarray(data[:, mapping.grayordinate_indices], dtype=float)


def validate_cortex_axis(image: nib.Cifti2Image, expected: CortexMapping) -> None:
    actual = cortex_mapping(image)
    if (actual.left_surface_vertices != expected.left_surface_vertices or
            actual.right_surface_vertices != expected.right_surface_vertices or
            not np.array_equal(actual.full_surface_vertices,
                               expected.full_surface_vertices)):
        raise ValueError("CIFTI cortical indexing does not match the network map")


def load_surface_geometry(left_path: Path, right_path: Path,
                          mapping: CortexMapping) -> SurfaceGeometry:
    left = nib.load(str(left_path))
    right = nib.load(str(right_path))
    left_xyz = np.asarray(left.darrays[0].data, dtype=float)
    right_xyz = np.asarray(right.darrays[0].data, dtype=float)
    left_faces = np.asarray(left.darrays[1].data, dtype=np.int64)
    right_faces = np.asarray(right.darrays[1].data, dtype=np.int64)
    if len(left_xyz) != mapping.left_surface_vertices:
        raise ValueError("Left surface vertex count does not match CIFTI")
    if len(right_xyz) != mapping.right_surface_vertices:
        raise ValueError("Right surface vertex count does not match CIFTI")
    full_xyz = np.vstack((left_xyz, right_xyz))
    full_faces = np.vstack((left_faces, right_faces + len(left_xyz)))
    full_to_cortex = np.full(len(full_xyz), -1, dtype=np.int64)
    full_to_cortex[mapping.full_surface_vertices] = np.arange(
        len(mapping.full_surface_vertices))
    keep = np.all(full_to_cortex[full_faces] >= 0, axis=1)
    return SurfaceGeometry(
        pial_xyz=full_xyz[mapping.full_surface_vertices],
        cortical_faces=full_to_cortex[full_faces[keep]],
    )


def gifti_label_mask(path: Path, label_names: Sequence[str], hemisphere: str,
                     mapping: CortexMapping) -> np.ndarray:
    image = nib.load(str(path))
    values = np.asarray(image.darrays[0].data).reshape(-1)
    table = {normalized_name(name): int(key)
             for key, name in image.labeltable.get_labels_as_dict().items()}
    keys = []
    for name in label_names:
        key = normalized_name(name)
        if key not in table:
            raise ValueError(f"GIFTI label not found: {name}")
        keys.append(table[key])
    if hemisphere.upper() == "L":
        if len(values) != mapping.left_surface_vertices:
            raise ValueError("Left label surface does not match CIFTI")
        full = np.zeros(mapping.left_surface_vertices + mapping.right_surface_vertices,
                        dtype=bool)
        full[:mapping.left_surface_vertices] = np.isin(values, keys)
    elif hemisphere.upper() == "R":
        if len(values) != mapping.right_surface_vertices:
            raise ValueError("Right label surface does not match CIFTI")
        full = np.zeros(mapping.left_surface_vertices + mapping.right_surface_vertices,
                        dtype=bool)
        full[mapping.left_surface_vertices:] = np.isin(values, keys)
    else:
        raise ValueError("hemisphere must be L or R")
    return full[mapping.full_surface_vertices]


def cifti_binary_mask(path: Path, mapping: CortexMapping) -> np.ndarray:
    image = nib.load(str(path))
    validate_cortex_axis(image, mapping)
    values = cortical_data(image, cortex_mapping(image))
    if values.shape[0] != 1:
        raise ValueError("Anatomical CIFTI mask must contain one map")
    if not np.all(np.isfinite(values)):
        raise ValueError("Anatomical mask must contain only finite binary values")
    unique = np.unique(values)
    if not np.all(np.isin(unique, (0.0, 1.0))):
        raise ValueError(f"Anatomical mask is not binary: {unique.tolist()}")
    return values[0].astype(bool)


def anatomical_mask(spec: Mapping[str, object], mapping: CortexMapping) -> np.ndarray:
    kind = str(spec.get("type", ""))
    if kind == "gifti_labels":
        return gifti_label_mask(
            require_file(str(spec["file"]), "anatomical label file"),
            [str(value) for value in spec["labels"]],
            str(spec["hemisphere"]), mapping)
    if kind == "cifti_binary_mask":
        return cifti_binary_mask(
            require_file(str(spec["file"]), "anatomical CIFTI mask"), mapping)
    raise ValueError(f"Unsupported anatomical mask type: {kind}")


def stable_components(faces: np.ndarray, support: np.ndarray,
                      hemisphere: np.ndarray) -> np.ndarray:
    labels = np.zeros(len(support), dtype=np.int32)
    next_id = 1
    for side in ("L", "R"):
        ids = np.flatnonzero(support & (hemisphere == side))
        if not len(ids):
            continue
        local_lookup = np.full(len(support), -1, dtype=np.int64)
        local_lookup[ids] = np.arange(len(ids))
        face_keep = np.all(local_lookup[faces] >= 0, axis=1)
        local_faces = local_lookup[faces[face_keep]]
        edges = np.vstack((local_faces[:, [0, 1]], local_faces[:, [1, 2]],
                           local_faces[:, [2, 0]])) if len(local_faces) else np.empty((0, 2), int)
        if len(edges):
            graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])),
                               shape=(len(ids), len(ids))).tocsr()
            _, local_labels = connected_components(graph, directed=False)
        else:
            local_labels = np.arange(len(ids), dtype=np.int32)
        components = [np.flatnonzero(local_labels == key)
                      for key in np.unique(local_labels)]
        components.sort(key=lambda member: int(ids[member].min()))
        for member in components:
            labels[ids[member]] = next_id
            next_id += 1
    return labels


def weighted_quantile(values: np.ndarray, weights: np.ndarray,
                      probabilities: Sequence[float]) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    values = np.asarray(values, dtype=float)[order]
    weights = np.asarray(weights, dtype=float)[order]
    cumulative = np.cumsum(weights) / weights.sum()
    indices = np.searchsorted(cumulative, probabilities, side="left")
    return values[np.minimum(indices, len(values) - 1)]


def characterize_components(labels: np.ndarray, mapping: CortexMapping,
                            area: np.ndarray, weights: np.ndarray,
                            pial_xyz: np.ndarray, scalp_xyz: np.ndarray,
                            anatomy_name: str,
                            quantiles: Sequence[float],
                            thresholds: Sequence[float]) -> List[dict]:
    tree = cKDTree(scalp_xyz)
    rows = []
    for component_id in np.unique(labels[labels > 0]):
        index = np.flatnonzero(labels == component_id)
        component_area = area[index]
        component_weights = weights[index]
        area_weight = component_area * component_weights
        area_total = float(component_area.sum())
        weighted_total = float(area_weight.sum())
        area_centroid = np.average(pial_xyz[index], axis=0,
                                   weights=component_area)
        weighted_centroid = (np.average(pial_xyz[index], axis=0,
                                        weights=area_weight)
                             if weighted_total > 0 else area_centroid)
        area_distance, area_vertex = tree.query(area_centroid)
        weighted_distance, weighted_vertex = tree.query(weighted_centroid)
        rows.append({
            "cluster_id": int(component_id),
            "hemisphere": str(mapping.hemisphere[index[0]]),
            "vertex_count": int(len(index)),
            "surface_area_mm2": area_total,
            "weighted_area_mm2": weighted_total,
            "mean_weight": weighted_total / area_total,
            "median_weight": float(weighted_quantile(
                component_weights, component_area, [0.5])[0]),
            "weight_quantiles": weighted_quantile(
                component_weights, component_area, quantiles),
            "area_above_threshold_mm2": np.asarray([
                component_area[component_weights >= threshold].sum()
                for threshold in thresholds], dtype=float),
            "area_centroid": area_centroid,
            "weighted_centroid": weighted_centroid,
            "nearest_scalp_vertex_area": int(area_vertex) + 1,
            "nearest_scalp_vertex_weighted": int(weighted_vertex) + 1,
            "projection_distance_area_mm": float(area_distance),
            "projection_distance_weighted_mm": float(weighted_distance),
            "anatomical_labels": anatomy_name,
        })
    return rows


CLUSTER_COLUMNS = (
    "cluster_id", "hemisphere", "vertex_count", "surface_area_mm2",
    "weighted_area_mm2", "mean_weight", "median_weight", "weight_quantiles",
    "area_above_threshold_mm2", "area_centroid_x", "area_centroid_y",
    "area_centroid_z", "weighted_centroid_x", "weighted_centroid_y",
    "weighted_centroid_z", "nearest_scalp_vertex_area",
    "nearest_scalp_vertex_weighted", "projection_distance_area_mm",
    "projection_distance_weighted_mm", "anatomical_labels",
)


def vector_text(values: Iterable[float]) -> str:
    return ";".join(f"{float(value):.15g}" for value in values)


def write_clusters(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLUSTER_COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            area_centroid = np.asarray(row["area_centroid"])
            weighted_centroid = np.asarray(row["weighted_centroid"])
            writer.writerow({
                **{key: row[key] for key in CLUSTER_COLUMNS
                   if key in row and not isinstance(row[key], np.ndarray)},
                "weight_quantiles": vector_text(row["weight_quantiles"]),
                "area_above_threshold_mm2": vector_text(
                    row["area_above_threshold_mm2"]),
                "area_centroid_x": area_centroid[0],
                "area_centroid_y": area_centroid[1],
                "area_centroid_z": area_centroid[2],
                "weighted_centroid_x": weighted_centroid[0],
                "weighted_centroid_y": weighted_centroid[1],
                "weighted_centroid_z": weighted_centroid[2],
            })


def write_dscalar(path: Path, values: np.ndarray, name: str,
                  mapping: CortexMapping) -> None:
    data = np.asarray(values, dtype=np.float32).reshape(1, -1)
    header = nib.cifti2.Cifti2Header.from_axes(
        (cifti2_axes.ScalarAxis([name]), mapping.axis))
    nib.save(nib.Cifti2Image(data, header), str(path))


def component_maps(labels: np.ndarray, rows: Sequence[Mapping[str, object]]) -> Tuple[np.ndarray, np.ndarray]:
    area_map = np.zeros(len(labels), dtype=float)
    weighted_map = np.zeros(len(labels), dtype=float)
    for row in rows:
        selected = labels == int(row["cluster_id"])
        area_map[selected] = float(row["surface_area_mm2"])
        weighted_map[selected] = float(row["weighted_area_mm2"])
    return area_map, weighted_map


def prepare(config_path: str | Path, output_root: str | Path) -> dict:
    config_path = require_file(config_path, "configuration")
    config = load_yaml(config_path)
    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists():
        raise FileExistsError(f"Refusing to overwrite output root: {output_root}")
    from network_inputs import resolve_network_inputs, validate_requested_networks
    resolve_network_inputs(config, output_root.with_name(output_root.name + "-network-inputs"))
    validate_requested_networks(config)
    inputs = config["inputs"]
    categorical_path = require_file(inputs["categorical_networks"],
                                    "categorical network CIFTI")
    probability_path = require_file(inputs["network_probabilities"],
                                    "network probability CIFTI")
    metadata_path = require_file(inputs["network_metadata"], "network metadata")
    area_path = require_file(inputs["vertex_area"], "vertex-area CIFTI")
    left_pial = require_file(inputs["left_pial"], "left pial surface")
    right_pial = require_file(inputs["right_pial"], "right pial surface")
    scalp_path = require_file(inputs["scalp"], "scalp surface")

    categorical_image = nib.load(str(categorical_path))
    probability_image = nib.load(str(probability_path))
    area_image = nib.load(str(area_path))
    mapping = cortex_mapping(categorical_image)
    validate_cortex_axis(probability_image, mapping)
    validate_cortex_axis(area_image, mapping)
    categorical = cortical_data(categorical_image, mapping)[0]
    probabilities = cortical_data(probability_image, cortex_mapping(probability_image))
    area = cortical_data(area_image, cortex_mapping(area_image))[0]
    if np.any(~np.isfinite(area)) or np.any(area <= 0):
        raise ValueError("Cortical vertex areas must be finite and positive")
    geometry = load_surface_geometry(left_pial, right_pial, mapping)
    scalp_image = nib.load(str(scalp_path))
    scalp_xyz = np.asarray(scalp_image.darrays[0].data, dtype=float)
    network_names, _ = network_metadata(metadata_path)
    if probabilities.shape[0] != len(network_names):
        raise ValueError("Probability maps do not match network metadata")
    binary_names_by_key = label_axis_names(categorical_image)
    binary_names = list(binary_names_by_key.values())
    quantiles = config.get("components", {}).get(
        "quantiles", [0.10, 0.25, 0.50, 0.75, 0.90])
    thresholds = config.get("components", {}).get(
        "report_weight_thresholds", [0.25, 0.50, 0.75])

    output_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "subject": str(config["subject"]),
        "configuration": str(config_path),
        "configuration_sha256": file_sha256(config_path),
        "network_input_provenance": config["network_input_provenance"],
        "inputs": {key: {"path": str(require_file(value, key)),
                          "sha256": file_sha256(require_file(value, key))}
                   for key, value in inputs.items()},
        "targets": [],
    }
    for spec in config["targets"]:
        requested_name = str(spec["network"])
        probability_index = resolve_unique_name(
            network_names, requested_name, "probability network")
        name = network_names[probability_index]
        display_name = str(spec.get("display_name", name))
        binary_index = resolve_unique_name(binary_names, name, "categorical network")
        binary_key = list(binary_names_by_key.keys())[binary_index]
        binary_support = categorical == binary_key
        raw_weights = probabilities[probability_index]
        mask = anatomical_mask(spec["anatomical_mask"], mapping)
        side = spec["anatomical_mask"].get("hemisphere")
        eligible = binary_support & mask
        if side is not None:
            side = str(side).upper()
            if side not in ("L", "R"):
                raise ValueError("anatomical-mask hemisphere must be L or R")
            eligible &= mapping.hemisphere == side
        if not np.any(eligible):
            raise ValueError(f"Eligible target is empty for {name}")
        labels = stable_components(
            geometry.cortical_faces, eligible, mapping.hemisphere)
        mode = str(spec.get("map_mode", "weighted"))
        cluster_weights = np.ones_like(raw_weights) if mode == "binary" else raw_weights
        rows = characterize_components(
            labels, mapping, area, cluster_weights, geometry.pial_xyz, scalp_xyz,
            str(spec["anatomical_mask"].get("name", "anatomical_mask")),
            quantiles, thresholds)
        target_dir = output_root / "targets" / safe_slug(display_name) / "target"
        target_dir.mkdir(parents=True)
        write_dscalar(target_dir / "FullBinaryTargetSupport.dscalar.nii",
                      binary_support, "binary_support", mapping)
        write_dscalar(target_dir / "FullWeightedTargetMap.dscalar.nii",
                      raw_weights, "raw_weight", mapping)
        write_dscalar(target_dir / "SelectedAnatomicalMask.dscalar.nii",
                      mask, "selected_anatomical_mask", mapping)
        write_dscalar(target_dir / "EligibleBinaryTarget.dscalar.nii",
                      eligible, "eligible_binary_support", mapping)
        write_dscalar(target_dir / "EligibleWeightedTarget.dscalar.nii",
                      raw_weights * eligible, "weighted_target_inside_anatomical_mask",
                      mapping)
        write_dscalar(target_dir / "ComponentLabels.dscalar.nii",
                      labels, "stable_component_id", mapping)
        area_map, weighted_map = component_maps(labels, rows)
        write_dscalar(target_dir / "ComponentSurfaceArea.dscalar.nii",
                      area_map, "component_surface_area_mm2", mapping)
        write_dscalar(target_dir / "ComponentWeightedArea.dscalar.nii",
                      weighted_map, "component_weighted_area_mm2", mapping)
        write_clusters(target_dir / "Components.tsv", rows)
        target_manifest = {
            "schema_version": 1,
            "network": name,
            "requested_network": requested_name,
            "display_name": display_name,
            "map_mode": mode,
            "network_input_provenance": config["network_input_provenance"],
            "categorical_label_key": int(binary_key),
            "probability_map_index_zero_based": int(probability_index),
            "anatomical_mask": spec["anatomical_mask"],
            "component_count": len(rows),
            "eligible_vertex_count": int(eligible.sum()),
            "output_directory": str(target_dir),
        }
        (target_dir / "TargetManifest.json").write_text(
            json.dumps(target_manifest, indent=2) + "\n")
        manifest["targets"].append(target_manifest)
    (output_root / "TargetPreparationManifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config, args.output), indent=2))


if __name__ == "__main__":
    main()
