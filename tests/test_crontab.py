import unittest

import crontab


class ParseFieldTests(unittest.TestCase):
    def test_wildcard_is_none(self):
        self.assertIsNone(crontab.parse_field("*", "hour"))

    def test_single_value(self):
        self.assertEqual(crontab.parse_field("9", "hour"), {9})

    def test_comma_list(self):
        self.assertEqual(crontab.parse_field("1,3,5", "hour"), {1, 3, 5})

    def test_range(self):
        self.assertEqual(crontab.parse_field("9-12", "hour"), {9, 10, 11, 12})

    def test_step(self):
        self.assertEqual(crontab.parse_field("*/15", "minute"), {0, 15, 30, 45})

    def test_range_with_step(self):
        self.assertEqual(crontab.parse_field("0-10/5", "minute"), {0, 5, 10})

    def test_dow_seven_normalizes_to_sunday_zero(self):
        self.assertEqual(crontab.parse_field("7", "dow"), {0})

    def test_out_of_range_value_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("60", "minute")
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("24", "hour")
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("0", "dom")  # dom is 1-31, 0 invalid

    def test_reversed_range_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("12-9", "hour")

    def test_non_numeric_value_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("abc", "hour")

    def test_invalid_step_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("*/0", "hour")
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("*/abc", "hour")

    def test_empty_list_item_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_field("1,,3", "hour")


class ParseCrontabTests(unittest.TestCase):
    def test_valid_daily_expression(self):
        parsed = crontab.parse_crontab("0 2 * * *")
        self.assertEqual(parsed["minute"], {0})
        self.assertEqual(parsed["hour"], {2})
        self.assertIsNone(parsed["dom"])
        self.assertIsNone(parsed["month"])
        self.assertIsNone(parsed["dow"])

    def test_empty_expression_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_crontab("")
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_crontab("   ")

    def test_wrong_field_count_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_crontab("0 2 * *")  # only 4 fields
        with self.assertRaises(crontab.CrontabError):
            crontab.parse_crontab("0 2 * * * *")  # 6 fields

    def test_invalid_field_reports_which_field(self):
        with self.assertRaises(crontab.CrontabError) as ctx:
            crontab.parse_crontab("99 2 * * *")
        self.assertIn("minute", str(ctx.exception))


class ToLaunchdIntervalsTests(unittest.TestCase):
    def test_simple_daily_schedule_is_a_single_dict(self):
        intervals = crontab.cron_to_launchd("30 9 * * *")
        self.assertEqual(intervals, [{"Minute": 30, "Hour": 9}])

    def test_comma_list_expands_to_multiple_dicts(self):
        intervals = crontab.cron_to_launchd("0 9,18 * * *")
        self.assertEqual(
            sorted(intervals, key=lambda d: d["Hour"]),
            [{"Minute": 0, "Hour": 9}, {"Minute": 0, "Hour": 18}],
        )

    def test_weekday_field_maps_to_weekday_key(self):
        intervals = crontab.cron_to_launchd("0 8 * * 1")
        self.assertEqual(intervals, [{"Minute": 0, "Hour": 8, "Weekday": 1}])

    def test_all_wildcards_rejected(self):
        with self.assertRaises(crontab.CrontabError):
            crontab.cron_to_launchd("* * * * *")

    def test_excessive_expansion_is_capped(self):
        # 60 minutes * 24 hours = 1440 combinations, way past the cap
        with self.assertRaises(crontab.CrontabError):
            crontab.cron_to_launchd("*/1 */1 * * *")


class LaunchdToCronTests(unittest.TestCase):
    def test_round_trip_simple_schedule(self):
        cron_str = crontab.launchd_to_cron({"Hour": 2, "Minute": 0})
        self.assertEqual(cron_str, "0 2 * * *")

    def test_round_trip_with_weekday(self):
        cron_str = crontab.launchd_to_cron({"Hour": 8, "Minute": 0, "Weekday": 1})
        self.assertEqual(cron_str, "0 8 * * 1")

    def test_compound_list_schedule_returns_none(self):
        self.assertIsNone(crontab.launchd_to_cron([{"Hour": 9}, {"Hour": 18}]))

    def test_non_dict_input_returns_none(self):
        self.assertIsNone(crontab.launchd_to_cron(None))
        self.assertIsNone(crontab.launchd_to_cron("garbage"))


if __name__ == "__main__":
    unittest.main()
