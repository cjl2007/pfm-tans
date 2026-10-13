import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import nibabel as nib
from nibabel.cifti2 import cifti2_axes as axes
import numpy as np
from scipy.io import savemat
import yaml

from network_inputs import (
    fallback_options, resolve_network_inputs, threshold_weights,
    validate_prepared_network_inputs, validate_requested_networks,
)
from prepare_targets import network_metadata, prepare
from run_pipeline import configure_target_plan


@contextmanager
def strict_temporary_directory(*args, **kwargs):
    """Require the owned directory to survive until cleanup, as in Python 3.7."""
    path = tempfile.mkdtemp(*args, **kwargs)
    try:
        yield path
    finally:
        shutil.rmtree(path)


class NetworkInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.axis = (axes.BrainModelAxis.from_surface(np.arange(4), 4, name="CortexLeft") +
                     axes.BrainModelAxis.from_surface(np.arange(4), 4, name="CortexRight"))
        self.config = {"subject": "TEST", "inputs": {"categorical_networks": None,
                       "network_probabilities": None, "network_metadata": None},
                       "targets": [{"network": "Salience"}]}

    def weights(self, rows, names=None, series=False):
        rows = np.atleast_2d(np.asarray(rows, np.float32))
        first = (axes.SeriesAxis(0, 1, len(rows)) if series else
                 axes.ScalarAxis(names or ["Salience"]))
        path = self.root / "weights.dscalar.nii"
        nib.save(nib.Cifti2Image(rows, nib.cifti2.Cifti2Header.from_axes((first, self.axis))), path)
        self.config["inputs"]["network_probabilities"] = str(path)
        return path

    def wta(self):
        path = self.root / "networks.dlabel.nii"
        table = {0: ("Unassigned", (0, 0, 0, 0)),
                 7: ("Salience", (0, 0.8, 0.8, 1)),
                 42: ("Somatomotor_Hand", (0.8, 0.6, 0, 1))}
        nib.save(nib.Cifti2Image(np.array([[7, 7, 42, 0, 0, 42, 7, 42]], np.int32),
                 nib.cifti2.Cifti2Header.from_axes((axes.LabelAxis(["WTA"], [table]), self.axis))), path)
        self.config["inputs"]["categorical_networks"] = str(path)
        return path

    def metadata(self, names):
        path = self.root / "Priors.mat"
        savemat(path, {"Priors": {"NetworkLabels": np.array([*names, "Noise"], dtype=object),
                                  "NetworkColors": np.full((len(names) + 1, 3), 0.5)}})
        self.config["inputs"]["network_metadata"] = str(path)
        return path

    def resolve(self):
        return resolve_network_inputs(self.config, self.root / "resolved")

    def percentile(self, cutoff=50, **options):
        self.config["input_fallbacks"] = {"weighted_to_binary_percentile": cutoff, **options}

    def test_wta_only_generates_exact_one_hot_and_remaps_colors(self):
        source = self.wta()
        original = source.read_bytes()
        with mock.patch('network_inputs.TemporaryDirectory', strict_temporary_directory):
            provenance = self.resolve()
        self.assertEqual(list(self.root.glob('.resolved-*')), [])
        self.assertEqual(provenance["mode"], "wta_only")
        self.assertIn("not measured", provenance["summary"])
        weights = nib.load(self.config["inputs"]["network_probabilities"]).get_fdata()
        np.testing.assert_array_equal(weights[0], [1, 1, 0, 0, 0, 0, 1, 0])
        np.testing.assert_array_equal(weights[1], [0, 0, 1, 0, 0, 1, 0, 1])
        labels = nib.load(self.config["inputs"]["categorical_networks"]).get_fdata()[0]
        np.testing.assert_array_equal(labels, [1, 1, 2, 0, 0, 2, 1, 2])
        names, colors = network_metadata(Path(self.config["inputs"]["network_metadata"]))
        self.assertEqual(names, ["Salience", "Somatomotor_Hand"])
        np.testing.assert_allclose(colors[0], [0, .8, .8])
        self.assertEqual(source.read_bytes(), original)
        validate_requested_networks(self.config)

    def test_missing_counterpart_filename_also_falls_back(self):
        self.wta()
        self.config["inputs"]["network_probabilities"] = str(self.root / "not-created.nii")
        self.assertEqual(self.resolve()["mode"], "wta_only")

    def test_both_provided_are_not_converted(self):
        wta = self.wta()
        weights = self.weights([[.1] * 8, [.9] * 8], ["Salience", "Somatomotor_Hand"])
        metadata = self.metadata(["Salience", "Somatomotor_Hand"])
        before = copy.deepcopy(self.config["inputs"])
        with mock.patch('network_inputs.TemporaryDirectory', strict_temporary_directory):
            self.assertEqual(self.resolve()["mode"], "provided_pair")
        self.assertEqual(list(self.root.glob('.resolved-*')), [])
        self.assertEqual(before, self.config["inputs"])
        self.assertEqual(len(list((self.root / "resolved").iterdir())), 1)
        self.assertTrue(all(path.is_file() for path in (wta, weights, metadata)))

    def test_product_write_failure_removes_staging_without_publishing(self):
        source = self.wta()
        original = source.read_bytes()
        with mock.patch('network_inputs.TemporaryDirectory', strict_temporary_directory), \
                mock.patch('network_inputs.nib.save', side_effect=OSError('simulated write failure')):
            with self.assertRaisesRegex(OSError, 'simulated write failure'):
                self.resolve()
        self.assertFalse((self.root / 'resolved').exists())
        self.assertEqual(list(self.root.glob('.resolved-*')), [])
        self.assertEqual(source.read_bytes(), original)

    def test_publish_failure_removes_staging_without_publishing(self):
        self.wta()
        with mock.patch('network_inputs.TemporaryDirectory', strict_temporary_directory), \
                mock.patch('network_inputs.Path.rename', side_effect=OSError('simulated publish failure')):
            with self.assertRaisesRegex(OSError, 'simulated publish failure'):
                self.resolve()
        self.assertFalse((self.root / 'resolved').exists())
        self.assertEqual(list(self.root.glob('.resolved-*')), [])

    def test_weighted_only_positive_percentile_before_mask_and_scale(self):
        source = self.weights([0, -9, 1, 2, 3, 4, np.nan, 0])
        original = source.read_bytes()
        self.percentile(50)
        self.resolve()
        labels = nib.load(self.config["inputs"]["categorical_networks"]).get_fdata()[0]
        np.testing.assert_array_equal(labels, [0, 0, 0, 0, 1, 1, 0, 0])
        weights = nib.load(self.config["inputs"]["network_probabilities"]).get_fdata()[0]
        np.testing.assert_array_equal(weights, [0, 0, .25, .5, .75, 1, 0, 0])
        manifest = json.loads(Path(self.config["network_input_provenance"]["manifest"]).read_text())
        detail = manifest["maps"][0]
        self.assertEqual(detail["threshold_before_normalization"], 2.5)
        self.assertEqual(detail["normalization_divisor"], 4)
        self.assertEqual(detail["excluded_nonfinite_vertex_count"], 1)
        self.assertEqual(source.read_bytes(), original)

    def test_probability_values_are_preserved(self):
        self.weights([.1, .2, .3, .4, .5, .6, .7, .8])
        self.percentile(90)
        self.resolve()
        np.testing.assert_allclose(nib.load(self.config["inputs"]["network_probabilities"]).get_fdata()[0],
                                   [.1, .2, .3, .4, .5, .6, .7, .8])

    def test_negative_and_absolute_modes(self):
        for polarity, expected in [("negative", [0, 1, 0, 0]), ("absolute", [0, 1, 0, 1])]:
            with self.subTest(polarity=polarity):
                options = fallback_options({"input_fallbacks": {
                    "weighted_to_binary_percentile": 50, "weight_polarity": polarity}})
                _, categorical, _, _ = threshold_weights(np.array([[-1, -4, 2, 3.]]), ["FC"], options)
                np.testing.assert_array_equal(categorical, expected)

    def test_cutoff_ties_and_endpoints(self):
        for percentile, expected in [(0, [0, 1, 1, 1]), (100, [0, 0, 1, 1])]:
            options = fallback_options({"input_fallbacks": {"weighted_to_binary_percentile": percentile}})
            _, labels, _, _ = threshold_weights(np.array([[0., .1, .5, .5]]), ["FC"], options)
            np.testing.assert_array_equal(labels, expected)

    def test_multiple_weighted_maps_overlap_competes_after_threshold(self):
        options = fallback_options({"input_fallbacks": {"weighted_to_binary_percentile": 0}})
        _, labels, details, overlaps = threshold_weights(
            np.array([[.8, .6, 0, .5], [.2, .9, 0, .5]]), ["A", "B"], options)
        np.testing.assert_array_equal(labels, [1, 2, 0, 1])
        self.assertEqual(overlaps, 3)
        self.assertEqual(details[0]["resolved_binary_vertex_count"], 2)

    def test_empty_rows_not_assigned_and_all_empty_errors(self):
        options = fallback_options({"input_fallbacks": {"weighted_to_binary_percentile": 50}})
        _, labels, details, _ = threshold_weights(np.array([[0., 0], [.2, .8]]), ["A", "B"], options)
        np.testing.assert_array_equal(labels, [0, 2])
        self.assertIsNone(details[0]["threshold_before_normalization"])
        with self.assertRaisesRegex(ValueError, "no finite"):
            threshold_weights(np.array([[0., -2]]), ["A"], options)

    def test_explicit_percentile_required(self):
        self.weights([.5] * 8)
        with self.assertRaisesRegex(ValueError, "requires.*percentile"):
            self.resolve()
        self.assertFalse((self.root / "resolved").exists())

    def test_no_maps_and_disabled_fallback_fail(self):
        with self.assertRaisesRegex(FileNotFoundError, "at least one"):
            self.resolve()
        self.wta()
        self.config["input_fallbacks"] = {"enabled": False}
        with self.assertRaisesRegex(ValueError, "required.*false"):
            self.resolve()

    def test_invalid_configuration_is_rejected(self):
        for settings in ({"weighted_to_binary_percentile": -1},
                         {"weighted_to_binary_percentile": float("nan")},
                         {"weighted_to_binary_percentile": True},
                         {"weight_polarity": "both"}, {"weight_normalization": "zscore"},
                         {"map_names": ["FC", "f-c"]}, {"map_names": ["Noise"]},
                         {"map_names": [" "]}, {"map_names": ["12"]}, {"typo": 1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                fallback_options({"input_fallbacks": settings})

    def test_series_rows_require_explicit_names_or_metadata(self):
        self.weights([.5] * 8, series=True)
        self.percentile()
        with self.assertRaisesRegex(ValueError, "row order"):
            self.resolve()
        self.config["input_fallbacks"]["map_names"] = ["MyFC"]
        self.resolve()
        names, colors = network_metadata(Path(self.config["inputs"]["network_metadata"]))
        self.assertEqual(names, ["MyFC"])
        self.assertEqual(colors.shape, (1, 3))

    def test_wrong_metadata_count_and_missing_metadata_fail(self):
        self.weights([.5] * 8)
        self.percentile()
        self.metadata(["A", "B"])
        with self.assertRaisesRegex(ValueError, "count does not match"):
            self.resolve()
        self.config["inputs"]["network_metadata"] = str(self.root / "missing.mat")
        with self.assertRaisesRegex(FileNotFoundError, "Missing network metadata"):
            self.resolve()

    def test_none_normalization_rejects_out_of_range(self):
        self.weights([2.] * 8)
        self.percentile(weight_normalization="none")
        with self.assertRaisesRegex(ValueError, "weights exceed 1"):
            self.resolve()

    def test_cached_and_resolved_configs_are_idempotent(self):
        self.wta()
        original = copy.deepcopy(self.config)
        first = self.resolve()
        self.assertEqual(first, self.resolve())
        self.config = original
        self.assertEqual(first, self.resolve())

    def test_source_change_blocks_resume(self):
        source = self.wta()
        self.resolve()
        source.write_bytes(source.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.resolve()

    def test_changed_percentile_and_tampered_derived_file_block_resume(self):
        self.weights([.5] * 8)
        self.percentile()
        original = copy.deepcopy(self.config)
        self.resolve()
        self.config["input_fallbacks"]["weighted_to_binary_percentile"] = 60
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.resolve()
        original["input_fallbacks"]["weighted_to_binary_percentile"] = 60
        with self.assertRaisesRegex(ValueError, "settings changed"):
            resolve_network_inputs(original, self.root / "resolved")
        self.config["input_fallbacks"]["weighted_to_binary_percentile"] = 50
        Path(self.config["inputs"]["network_probabilities"]).write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.resolve()

    def test_single_target_does_not_invent_motor_target(self):
        self.weights([.5] * 8)
        self.percentile()
        self.resolve()
        self.config["targets"].append({"network": "Somatomotor_Hand"})
        with self.assertRaisesRegex(ValueError, "skip-sm-hand-calibration"):
            validate_requested_networks(self.config)

    def test_treatment_only_configuration_does_not_need_motor_template(self):
        coil = self.root / "coil.ccd"
        coil.touch()
        mask = self.root / "mask.nii"
        mask.touch()
        config = {"targets": []}
        configure_target_plan(config, "MyFC", str(mask), "custom zone", False, coil, coil)
        self.assertEqual([item["network"] for item in config["targets"]], ["MyFC"])
        self.assertFalse(config["workflow"]["dose_transfer_requested"])

    def test_bundled_names_and_path_safe_target_display(self):
        # The actual bundled atlas includes Visual_Dorsal/VentralStream.
        from network_inputs import validate_names
        names, _ = network_metadata(Path(__file__).resolve().parents[1] / 'res0urces/Priors.mat')
        validate_names(names)
        coil = self.root / 'coil.ccd'
        coil.touch()
        mask = self.root / 'mask.nii'
        mask.touch()
        config = {'targets': []}
        configure_target_plan(config, 'Visual_Dorsal/VentralStream', str(mask), 'custom', False, coil, coil)
        self.assertEqual(config['targets'][0]['display_name'], 'Visual-Dorsal-VentralStream')

    def geometry_inputs(self):
        # Area and mask have an extra volume grayordinate before cortex. Derived
        # inputs intentionally contain only cortex, so each file must use its own indices.
        volume = axes.BrainModelAxis.from_mask(np.ones((1, 1, 1), bool), name="ThalamusLeft")
        mixed_axis = volume + self.axis
        for name, values in (("vertex_area", [[900, *([2.] * 8)]]),
                             ("mask", [[0, *([1.] * 8)]])):
            path = self.root / f"{name}.dscalar.nii"
            nib.save(nib.Cifti2Image(np.array(values, np.float32), nib.cifti2.Cifti2Header.from_axes(
                (axes.ScalarAxis([name]), mixed_axis))), path)
            if name == "vertex_area":
                self.config["inputs"][name] = str(path)
            else:
                self.config["targets"][0]["anatomical_mask"] = {
                    "type": "cifti_binary_mask", "name": "All", "file": str(path)}
        xyz = np.array([[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]], np.float32)
        faces = np.array([[0, 1, 2], [0, 2, 3]], np.int32)
        for name, offset in (("left_pial", 0), ("right_pial", 5), ("scalp", 10)):
            path = self.root / f"{name}.surf.gii"
            nib.save(nib.gifti.GiftiImage(darrays=[
                nib.gifti.GiftiDataArray(xyz + offset, intent="NIFTI_INTENT_POINTSET"),
                nib.gifti.GiftiDataArray(faces, intent="NIFTI_INTENT_TRIANGLE")]), path)
            self.config["inputs"][name] = str(path)

    def test_target_preparation_end_to_end_for_both_fallbacks(self):
        for mode in ("wta", "weighted"):
            with self.subTest(mode=mode):
                if mode == "wta":
                    self.wta()
                else:
                    self.config["inputs"]["categorical_networks"] = None
                    self.weights([0, 0, 1, 2, 3, 4, 0, 0])
                    self.percentile(50)
                self.geometry_inputs()
                config_path = self.root / f"{mode}.yaml"
                config_path.write_text(yaml.safe_dump(self.config))
                prepared = prepare(config_path, self.root / mode)
                self.assertIn("network_input_provenance", prepared["targets"][0])
                output = Path(prepared["targets"][0]["output_directory"])
                binary = nib.load(output / "FullBinaryTargetSupport.dscalar.nii").get_fdata()[0]
                expected = [1, 1, 0, 0, 0, 0, 1, 0] if mode == "wta" else [0, 0, 0, 0, 1, 1, 0, 0]
                np.testing.assert_array_equal(binary, expected)
                resolved = copy.deepcopy(self.config)
                resolve_network_inputs(resolved, self.root / f"{mode}-network-inputs")
                validate_prepared_network_inputs(resolved, prepared)
                self.assertEqual(prepared["targets"][0]["eligible_vertex_count"], sum(expected))


if __name__ == "__main__":
    unittest.main()
