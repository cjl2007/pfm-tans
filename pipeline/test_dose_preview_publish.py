import copy
import unittest

from publish_operator_export import filter_lookup


class DosePreviewPublicationTests(unittest.TestCase):
    def test_target_package_contains_only_matching_preview_and_landmarks(self):
        lookup = {
            "treatment_targets": ["SAL", "AMN"],
            "treatment_sources": {"SAL": {"network": "Salience"},
                                  "AMN": {"network": "CinguloOpercular"}},
            "dose_response_curves": {"SAL": [], "AMN": []},
            "hotspot_previews": {"SAL": {"image": "sal"}, "AMN": {"image": "amn"}},
            "entries": [{"treatments": {"SAL": {}, "AMN": {}},
                         "treatment_landmarks": {"SAL": {"onset": 54}, "AMN": {"onset": 58}}}],
        }
        original = copy.deepcopy(lookup)
        result, name = filter_lookup(lookup, "Salience")
        self.assertEqual(name, "SAL")
        self.assertEqual(result["hotspot_previews"], {"SAL": {"image": "sal"}})
        self.assertEqual(result["entries"][0]["treatment_landmarks"], {"SAL": {"onset": 54}})
        self.assertEqual(lookup, original)


if __name__ == "__main__":
    unittest.main()
