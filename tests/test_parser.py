"""Synthetic fixtures only; never commit real HDF rows."""
import csv
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

PATH = Path(__file__).resolve().parents[1] / "custom_components/esb_meter/parser.py"
SPEC = importlib.util.spec_from_file_location("esb_parser_under_test", PATH)
parser = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = parser
SPEC.loader.exec_module(parser)


def hdf(*rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["MPRN", "Meter Serial Number", "Read Value", "Read Type", "Read Date and End Time"])
    for value, kind, label in rows:
        writer.writerow(["10000000000", "SYNTHETIC", value, kind, label])
    return output.getvalue()


IMPORT = "Active Import Interval (kW)"
EXPORT = "Active Export Interval (kW)"
STAMP = "14-10-2026 12:30"


class ParserTests(unittest.TestCase):
    def test_direction_and_kw_conversion(self):
        result = parser.parse_text(hdf(("2", IMPORT, STAMP), ("1", EXPORT, STAMP)), "UTC")
        data = parser.summary(result.readings)
        self.assertEqual(data["import"]["kwh"], 1)
        self.assertEqual(data["export"]["kwh"], 0.5)
        self.assertNotIn("10000000000", repr(result))

    def test_kwh_is_not_converted_twice(self):
        result = parser.parse_text(hdf(("1", IMPORT.replace("(kW)", "(kWh)"), STAMP)), "UTC")
        self.assertEqual(result.readings[0].kwh, Decimal(1))

    def test_end_label_belongs_to_previous_day(self):
        result = parser.parse_text(hdf(("2", IMPORT, "14-10-2026 00:00")), "UTC")
        self.assertEqual(result.readings[0].start, datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc))

    def test_identical_duplicates_are_deduplicated(self):
        row = ("2", IMPORT, STAMP)
        result = parser.parse_text(hdf(row, row), "UTC")
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(len(result.readings), 1)

    def test_conflicting_duplicates_reject_file(self):
        with self.assertRaisesRegex(parser.HDFError, "conflicting_duplicate"):
            parser.parse_text(hdf(("2", IMPORT, STAMP), ("3", IMPORT, STAMP)), "UTC")

    def test_daily_registers_rejected(self):
        with self.assertRaisesRegex(parser.HDFError, "unsupported_read_type"):
            parser.parse_text(hdf(("1000", "Total Import Register (kWh)", STAMP)), "UTC")

    def test_nonfinite_and_negative_values(self):
        for value in ("NaN", "Infinity", "-1", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(parser.HDFError):
                parser.parse_text(hdf((value, IMPORT, STAMP)), "UTC")

    def test_multiple_meters_rejected(self):
        text = hdf(("2", IMPORT, STAMP), ("1", EXPORT, STAMP))
        text = text.rsplit("10000000000", 1)
        with self.assertRaisesRegex(parser.HDFError, "multiple_meters"):
            parser.parse_text("10000000001".join(text), "UTC")

    def test_empty_file_rejected(self):
        with self.assertRaisesRegex(parser.HDFError, "empty_file"):
            parser.parse_text(hdf(), "UTC")

    def test_invalid_time_basis_even_for_empty_file(self):
        with self.assertRaises(parser.HDFError):
            parser.parse_text(hdf(("1", IMPORT, STAMP)), "automatic")

    def test_summer_timezone_is_explicit(self):
        utc = parser.interval_start("01-07-2026 12:30", "UTC")
        local = parser.interval_start("01-07-2026 12:30", "Europe/Dublin")
        self.assertEqual((utc - local).total_seconds(), 3600)

    def test_ambiguous_autumn_local_time_is_not_guessed(self):
        with self.assertRaisesRegex(parser.HDFError, "ambiguous_local_time"):
            parser.interval_start("25-10-2026 01:30", "Europe/Dublin")

    def test_nonexistent_spring_time_is_not_guessed(self):
        with self.assertRaisesRegex(parser.HDFError, "nonexistent_local_time"):
            parser.interval_start("29-03-2026 01:30", "Europe/Dublin")

    def test_full_autumn_repeated_hour_both_orders(self):
        labels = ["26-10-2025 00:30", "26-10-2025 01:00", "26-10-2025 01:30",
                  "26-10-2025 01:00", "26-10-2025 01:30", "26-10-2025 02:00"]
        for ordered in (labels, list(reversed(labels))):
            result = parser.parse_text(hdf(*[("2", IMPORT, t) for t in ordered]), "Europe/Dublin")
            self.assertEqual(len(result.readings), 6)
            self.assertEqual(parser.summary(result.readings)["import"]["kwh"], 6)
            starts = [r.start for r in result.readings]
            self.assertTrue(all((b-a).total_seconds() == 1800 for a,b in zip(starts,starts[1:])))

    def test_partial_autumn_hour_is_rejected(self):
        with self.assertRaisesRegex(parser.HDFError, "incomplete_repeated_hour"):
            parser.parse_text(hdf(("2", IMPORT, "26-10-2025 01:30")), "Europe/Dublin")

    def test_unsorted_repeated_hour_is_rejected(self):
        labels = ["26-10-2025 00:30", "26-10-2025 01:00", "26-10-2025 01:00",
                  "26-10-2025 01:30", "26-10-2025 01:30", "26-10-2025 02:00"]
        with self.assertRaisesRegex(parser.HDFError, "ambiguous_file_order"):
            parser.parse_text(hdf(*[("2", IMPORT, t) for t in labels]), "Europe/Dublin")

    def test_utc_covers_dst_without_ambiguity(self):
        result = parser.parse_text(hdf(("2", IMPORT, "25-10-2026 01:30")), "UTC")
        self.assertEqual(len(result.readings), 1)

    def test_merge_is_idempotent_and_corrects_overlap(self):
        old = parser.parse_text(hdf(("2", IMPORT, STAMP)), "UTC").readings
        same, count = parser.merge(old, old)
        self.assertEqual(same, old)
        self.assertEqual(count, 0)
        new = parser.parse_text(hdf(("4", IMPORT, STAMP)), "UTC").readings
        corrected, count = parser.merge(old, new)
        self.assertEqual(count, 1)
        self.assertEqual(corrected[0].kwh, Decimal(2))

    def test_gaps_remain_gaps(self):
        result = parser.parse_text(hdf(("2", IMPORT, "14-10-2026 12:30"),
                                      ("2", IMPORT, "14-10-2026 13:30")), "UTC")
        data = parser.summary(result.readings)
        self.assertEqual(data["import"]["missing_intervals"], 1)
        self.assertEqual(data["import"]["partial_hours"], 2)
        self.assertEqual(data["import"]["kwh"], 2)

    def test_bom_supported(self):
        self.assertEqual(len(parser.parse_text("\ufeff" + hdf(("2", IMPORT, STAMP)), "UTC").readings), 1)

    def test_filename_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "imports"
            root.mkdir()
            for name in ("../private.csv", "/private.csv", "file.txt"):
                with self.subTest(name=name), self.assertRaises(parser.HDFError):
                    parser.import_path(root, name)
            (root / "link.csv").symlink_to(Path(temp) / "outside.csv")
            with self.assertRaises(parser.HDFError):
                parser.import_path(root, "link.csv")

    def test_wrong_columns_rejected(self):
        with self.assertRaisesRegex(parser.HDFError, "invalid_columns"):
            parser.parse_text("hello,world\n1,2", "UTC")

    def test_interval_alignment(self):
        with self.assertRaisesRegex(parser.HDFError, "non_half_hour_timestamp"):
            parser.interval_start("14-10-2026 12:15", "UTC")


if __name__ == "__main__":
    unittest.main()
