import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from billing import BillingPeriod, billing_period_for, shift_month


class BillingPeriodTest(unittest.TestCase):
    def test_account_specific_cycles_on_statement_day(self):
        cases = (
            (date(2026, 7, 14), 14, 3, 1, date(2026, 6, 15), date(2026, 7, 14), date(2026, 8, 3)),
            (date(2026, 7, 12), 12, 1, 1, date(2026, 6, 13), date(2026, 7, 12), date(2026, 8, 1)),
            (date(2026, 7, 20), 20, 6, 1, date(2026, 6, 21), date(2026, 7, 20), date(2026, 8, 6)),
            (date(2026, 7, 13), 13, 2, 1, date(2026, 6, 14), date(2026, 7, 13), date(2026, 8, 2)),
            (date(2026, 7, 6), 6, 25, 0, date(2026, 6, 7), date(2026, 7, 6), date(2026, 7, 25)),
        )

        for anchor, statement_day, due_day, due_offset, start, end, due_date in cases:
            with self.subTest(anchor=anchor, statement_day=statement_day):
                self.assertEqual(
                    billing_period_for(anchor, statement_day, due_day, due_offset),
                    BillingPeriod(start, end, end, due_date),
                )

    def test_previous_statement_and_next_day_choose_correct_cycle_boundaries(self):
        cases = (
            (date(2026, 7, 13), 14, 3, 1, date(2026, 6, 15), date(2026, 7, 14), date(2026, 8, 3)),
            (date(2026, 7, 14), 14, 3, 1, date(2026, 6, 15), date(2026, 7, 14), date(2026, 8, 3)),
            (date(2026, 7, 15), 14, 3, 1, date(2026, 7, 15), date(2026, 8, 14), date(2026, 9, 3)),
            (date(2026, 7, 11), 12, 1, 1, date(2026, 6, 13), date(2026, 7, 12), date(2026, 8, 1)),
            (date(2026, 7, 12), 12, 1, 1, date(2026, 6, 13), date(2026, 7, 12), date(2026, 8, 1)),
            (date(2026, 7, 13), 12, 1, 1, date(2026, 7, 13), date(2026, 8, 12), date(2026, 9, 1)),
            (date(2026, 7, 19), 20, 6, 1, date(2026, 6, 21), date(2026, 7, 20), date(2026, 8, 6)),
            (date(2026, 7, 20), 20, 6, 1, date(2026, 6, 21), date(2026, 7, 20), date(2026, 8, 6)),
            (date(2026, 7, 21), 20, 6, 1, date(2026, 7, 21), date(2026, 8, 20), date(2026, 9, 6)),
            (date(2026, 7, 12), 13, 2, 1, date(2026, 6, 14), date(2026, 7, 13), date(2026, 8, 2)),
            (date(2026, 7, 13), 13, 2, 1, date(2026, 6, 14), date(2026, 7, 13), date(2026, 8, 2)),
            (date(2026, 7, 14), 13, 2, 1, date(2026, 7, 14), date(2026, 8, 13), date(2026, 9, 2)),
            (date(2026, 7, 5), 6, 25, 0, date(2026, 6, 7), date(2026, 7, 6), date(2026, 7, 25)),
            (date(2026, 7, 6), 6, 25, 0, date(2026, 6, 7), date(2026, 7, 6), date(2026, 7, 25)),
            (date(2026, 7, 7), 6, 25, 0, date(2026, 7, 7), date(2026, 8, 6), date(2026, 8, 25)),
        )

        for anchor, statement_day, due_day, due_offset, start, end, due_date in cases:
            with self.subTest(anchor=anchor, statement_day=statement_day):
                self.assertEqual(
                    billing_period_for(anchor, statement_day, due_day, due_offset),
                    BillingPeriod(start, end, end, due_date),
                )

    def test_year_boundary_uses_next_calendar_year_for_due_date(self):
        self.assertEqual(
            billing_period_for(date(2026, 12, 14), 14, 3, 1),
            BillingPeriod(
                date(2026, 11, 15),
                date(2026, 12, 14),
                date(2026, 12, 14),
                date(2027, 1, 3),
            ),
        )

    def test_shift_month_clamps_to_month_end(self):
        self.assertEqual(shift_month(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(shift_month(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(shift_month(date(2026, 3, 31), -1, day=30), date(2026, 2, 28))

    def test_billing_period_is_frozen(self):
        period = billing_period_for(date(2026, 7, 14), 14, 3, 1)
        with self.assertRaises(FrozenInstanceError):
            period.start = date(2026, 1, 1)

    def test_rejects_invalid_billing_days_and_due_offsets(self):
        invalid_cases = (
            (0, 3, 1),
            (29, 3, 1),
            (14, 0, 1),
            (14, 29, 1),
            (14, 3, -1),
            (14, 3, 2),
            (True, 3, 1),
            (14, 3, True),
        )

        for statement_day, due_day, due_offset in invalid_cases:
            with self.subTest(
                statement_day=statement_day, due_day=due_day, due_offset=due_offset
            ):
                with self.assertRaises(ValueError):
                    billing_period_for(date(2026, 7, 14), statement_day, due_day, due_offset)


if __name__ == "__main__":
    unittest.main()
