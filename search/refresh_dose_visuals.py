"""Build a separate, self-contained dose review from recorded fields, without FEM."""

import argparse
import hashlib
import json
from pathlib import Path

from dose_report_defaults import ensure_lookup
from generate_calibration_dose_report import html_report, write_tsv


def refresh(source, output, priors=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace an existing dose report: {output}")
    original_bytes = source.read_bytes()
    original = json.loads(original_bytes)
    lookup = ensure_lookup(original, priors)
    for before, after in zip(original["entries"], lookup["entries"]):
        assert before["treatments"] == after["treatments"], "Dose selection changed"
    assert original["policy"] == lookup["policy"], "Dose policy changed"
    output.mkdir(parents=True)
    (output / "DoseLookup.json").write_text(json.dumps(lookup, separators=(",", ":")) + "\n")
    write_tsv(output / "DoseLookup.tsv", lookup)
    html_report(output / "DoseCalibrationReport.html", lookup)
    (output / "VisualRefreshValidation.json").write_text(json.dumps({
        "status": "passed",
        "source_lookup": str(source),
        "source_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "source_unchanged": source.read_bytes() == original_bytes,
        "original_selections_unchanged": True,
        "original_policy_unchanged": True,
        "rmt_entries_verified": len(original["entries"]),
        "new_simulations": 0,
        "continuous_input": "piecewise coil calibration; original fixed-pose grid rescored",
        "clinical_clearance": False,
    }, indent=2) + "\n")
    return output / "DoseCalibrationReport.html"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookup", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--priors")
    args = parser.parse_args()
    print(refresh(args.lookup, args.output, args.priors))
