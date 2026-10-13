"""Version and upgrade the offline dose interface without changing prescriptions."""

import base64
import copy
import io
import json
import os
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image


INTERFACE_VERSION = "tans-interactive-dose-v1"
HTML_MARKER = (f'<meta name="tans-dose-interface" content="{INTERFACE_VERSION}">\n'
               '<meta name="tans-dose-session" content="tans-measured-dose-v1">')


def _array(payload, key, dtype):
    return np.frombuffer(base64.b64decode(payload[key], validate=True), dtype=dtype)


def _calibration(table):
    mso = np.asarray(table["samples_mso"], dtype=float)
    didt = np.asarray(table["samples_didt_a_per_us"], dtype=float)
    if (mso.ndim != 1 or len(mso) < 2 or mso.shape != didt.shape or
            not np.all(np.isfinite(mso)) or not np.all(np.isfinite(didt)) or
            np.any(np.diff(mso) <= 0) or np.any(np.diff(didt) <= 0)):
        raise ValueError("Invalid embedded coil calibration")


def _validate_payload(lookup):
    targets = lookup["treatment_targets"]
    if not targets or not lookup["entries"]:
        raise ValueError("Dose report has no targets or rMT entries")
    _calibration(lookup["calibration"])
    factor = lookup["calibration"]["threshold_v_per_m_per_calibration_didt"]
    if not np.isfinite(factor) or factor <= 0:
        raise ValueError("Invalid motor-derived threshold coefficient")
    for target in targets:
        source = lookup["treatment_sources"][target]
        _calibration(source["calibration"])
        metrics = source["preview_metrics"]
        field = _array(metrics, "field_f64le_base64", "<f8")
        if not len(field) or not np.all(np.isfinite(field)) or np.any(np.diff(field) < 0):
            raise ValueError(f"{target}: invalid embedded field samples")
        for key in ("area_prefix_f64le_base64", "target_area_prefix_f64le_base64",
                    "target_mass_prefix_f64le_base64"):
            values = _array(metrics, key, "<f8")
            if (len(values) != len(field) + 1 or not np.all(np.isfinite(values)) or
                    values[0] != 0 or np.any(np.diff(values) < 0)):
                raise ValueError(f"{target}: invalid cumulative statistics: {key}")
        for key in ("total_target_area_mm2", "total_target_mass",
                    "maximum_target_reference_field"):
            if not np.isfinite(metrics[key]) or metrics[key] < 0:
                raise ValueError(f"{target}: invalid {key}")
        grid = np.asarray(metrics["treatment_grid_mso"], dtype=float)
        if (grid.ndim != 1 or not len(grid) or not np.all(np.isfinite(grid)) or
                np.any(np.diff(grid) <= 0)):
            raise ValueError(f"{target}: missing or invalid evaluated intensity grid")
        preview = lookup["hotspot_previews"][target]
        if preview["schema_version"] != "tans-dose-hotspot-preview-v1":
            raise ValueError(f"{target}: unsupported hotspot preview schema")
        width, height = preview["width"], preview["height"]
        if not all(isinstance(n, int) and 1 <= n <= 1024 for n in (width, height)):
            raise ValueError(f"{target}: invalid preview dimensions")
        if (_array(preview, "field_f32le_base64", "<f4").size != width * height or
                _array(preview, "network_index_u8_base64", "u1").size != width * height):
            raise ValueError(f"{target}: truncated preview arrays")
        for key in ("base_gray_png", "network_png", "coil_outline_png"):
            prefix, data = preview[key].split(",", 1)
            if prefix != "data:image/png;base64":
                raise ValueError(f"{target}: preview assets must be embedded PNGs")
            with Image.open(io.BytesIO(base64.b64decode(data, validate=True))) as asset:
                if asset.format != "PNG" or asset.size != (width, height):
                    raise ValueError(f"{target}: invalid preview image: {key}")
                asset.verify()
        if preview["reference_didt_a_per_us"] != source["reference_didt_a_per_us"]:
            raise ValueError(f"{target}: preview reference intensity mismatch")
        provenance = preview["provenance"]
        for key, value in (("subject", lookup["subject"]), ("network", source["network"]),
                           ("map_index_zero_based", source["reference_map_index_zero_based"]),
                           ("selection_mode", source["selection_mode"]),
                           ("coil_hardware_name", source["calibration"]["coil_hardware_name"])):
            if provenance[key] != value:
                raise ValueError(f"{target}: preview {key} mismatch")
        for entry in lookup["entries"]:
            if not np.isfinite(entry["preview_absolute_threshold_v_per_m"]):
                raise ValueError("Invalid exact motor-derived threshold")
            landmarks = entry["treatment_landmarks"][target]
            for key in ("first_cortex_mso", "first_target_mso", "first_cortex_row",
                        "first_target_row", "first_geometrically_feasible", "first_feasible",
                        "unconstrained_selectivity_peak"):
                if key not in landmarks:
                    raise ValueError(f"{target}: missing dose landmark: {key}")


def validate_lookup(lookup):
    if lookup.get("dose_report_interface") != INTERFACE_VERSION:
        raise ValueError("Dose report requires the approved interactive interface upgrade")
    try:
        _validate_payload(lookup)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"Incomplete interactive dose payload: {exc}") from exc


def validate_html(document, lookup):
    if HTML_MARKER not in document:
        raise ValueError("Dose HTML is not the current approved interface")
    prefix = "const LOOKUP="
    if prefix not in document:
        raise ValueError("Dose HTML lacks its embedded lookup")
    embedded, _ = json.JSONDecoder().raw_decode(document.split(prefix, 1)[1].lstrip())
    if embedded != lookup:
        raise ValueError("Dose HTML and lookup JSON disagree")


def ensure_lookup(lookup, priors=None):
    """Upgrade missing payloads; reuse complete embedded reports without source files."""
    output = copy.deepcopy(lookup)
    if output.get("dose_report_interface") == INTERFACE_VERSION:
        validate_lookup(output)
        return output
    sources = output["treatment_sources"]
    needs_metrics = ("threshold_v_per_m_per_calibration_didt" not in output["calibration"] or
                     any("preview_metrics" not in source for source in sources.values()) or
                     any("treatment_landmarks" not in entry for entry in output["entries"]))
    if needs_metrics:
        from dose_landmarks import enrich_lookup
        output = enrich_lookup(output)
    if any(target not in output.get("hotspot_previews", {})
           for target in output["treatment_targets"]):
        from dose_hotspot_preview import attach_hotspot_previews
        output = attach_hotspot_previews(output, priors)
    if ([entry["treatments"] for entry in lookup["entries"]] !=
            [entry["treatments"] for entry in output["entries"]] or
            lookup["policy"] != output["policy"]):
        raise ValueError("Interface upgrade changed recorded selections or policy")
    output["dose_report_interface"] = INTERFACE_VERSION
    validate_lookup(output)
    return output


def ensure_report(report_dir, priors=None):
    """Stage and validate private report updates before replacement; never invoke FEM."""
    from generate_calibration_dose_report import html_report, write_tsv

    report = Path(report_dir)
    source = report / "DoseLookup.json"
    original = source.read_bytes()
    lookup = ensure_lookup(json.loads(original), priors)
    with tempfile.TemporaryDirectory(prefix=".dose-interface-", dir=report.parent) as work:
        stage = Path(work)
        (stage / source.name).write_text(json.dumps(lookup, indent=2) + "\n")
        write_tsv(stage / "DoseLookup.tsv", lookup)
        html_report(stage / "DoseCalibrationReport.html", lookup)
        validate_html((stage / "DoseCalibrationReport.html").read_text(), lookup)
        if source.read_bytes() != original:
            raise RuntimeError("Dose lookup changed during upgrade; no files replaced")
        names = (source.name, "DoseLookup.tsv", "DoseCalibrationReport.html")
        changed = [name for name in names if not (report / name).is_file() or
                   (report / name).read_bytes() != (stage / name).read_bytes()]
        backups = {name: (report / name).read_bytes() if (report / name).exists() else None
                   for name in changed}
        replaced = []
        try:
            for name in changed:
                os.replace(stage / name, report / name)
                replaced.append(name)
        except Exception:
            for name in reversed(replaced):
                if backups[name] is None:
                    (report / name).unlink()
                else:
                    (stage / name).write_bytes(backups[name])
                    os.replace(stage / name, report / name)
            raise
    return report
