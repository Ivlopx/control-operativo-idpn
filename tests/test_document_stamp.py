"""Date boundaries and suffix validation independent of server timezone."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from document_stamp import publication_day, valid_stamp, next_stamp


class StampTests(unittest.TestCase):
    def test_date_uses_mexico_even_when_utc_is_already_tomorrow(self):
        instant = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
        with patch('document_stamp.datetime') as clock:
            clock.now.side_effect = lambda zone: instant.astimezone(zone)
            self.assertEqual(publication_day(), '2026-10-18')

    def test_rejects_invalid_dates_and_noncanonical_counters(self):
        for stamp in ('2026-02-30', '2026-10-18-0', '2026-10-18-1', '2026-10-18-02', '2026-10-18-extra'):
            self.assertFalse(valid_stamp(stamp), stamp)
        for stamp in ('2026-10-18', '2026-10-18-2', '2026-10-18-10', 'v017'):
            self.assertTrue(valid_stamp(stamp), stamp)

    def test_next_suffix_does_not_reuse_a_gap(self):
        with patch('document_stamp.publication_day', return_value='2026-10-18'):
            self.assertEqual(next_stamp([{'key':'folder/name-2026-10-18-4.pdf'},
                                         {'key':'folder/baja/name-2026-10-17-99.xlsx'}]), '2026-10-18-5')
