import csv
import json
import unittest
from pathlib import Path


DATA = Path(__file__).resolve().parents[1] / "data"


class FixtureContractTests(unittest.TestCase):
    def test_declared_sources_have_headers_and_records(self):
        manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
        for filename, columns in manifest["sources"].items():
            with self.subTest(source=filename):
                with (DATA / filename).open(encoding="utf-8", newline="") as source:
                    reader = csv.DictReader(source)
                    self.assertEqual(reader.fieldnames, columns)
                    rows = list(reader)
                    self.assertTrue(rows)
                    for row in rows:
                        self.assertNotIn(None, row)
                        self.assertNotIn(None, row.values())

    def records(self, filename):
        with (DATA / filename).open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))

    def test_ground_records_refer_to_stable_spatial_units(self):
        ground = self.records("ground_units.csv")
        observations = self.records("observations.csv")
        self.assertLessEqual({row["ground_id"] for row in observations}, {row["ground_id"] for row in ground})
        self.assertEqual({row["plot_id"] for row in ground if row["ground_id"] == "G-02"}, {"P-01", "P-02"})
        methods = {row["method"] for row in observations if row["ground_id"] == "G-03" and row["year"] == "2024"}
        self.assertEqual(len(methods), 2)
        self.assertGreaterEqual(len({row["year"] for row in observations}), 5)


if __name__ == "__main__":
    unittest.main()
