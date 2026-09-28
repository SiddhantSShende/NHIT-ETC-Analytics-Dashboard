import unittest

from scripts.build_json_export import parse_mlff_table, period_from_filename


def mlff_rows(combined_group_header):
    group_header = ["Month-Year", "Plaza Name", "PIU", "RO"] + [None] * 23
    categories = [None] * 27
    for index, name in [
        (4, "Car/Van/Jeep"), (8, "LCV"), (11, "Bus/Truck"),
        (14, "3-Axle"), (17, "4-6 Axle"), (20, "OSV"), (23, "Total"),
    ]:
        categories[index] = name
    if combined_group_header:
        group_header[4:] = categories[4:]
    else:
        categories[:4] = [None] * 4
    metric_header = [None] * 27
    metric_names = {
        4: "ETC Txn Count", 5: "ETC Txn Amount", 6: "Annual Pass Count", 7: "E-notice Count",
        8: "ETC Txn Count", 9: "ETC Txn Amount", 10: "E-notice Count",
        11: "ETC Txn Count", 12: "ETC Txn Amount", 13: "E-notice Count",
        14: "ETC Txn Count", 15: "ETC Txn Amount", 16: "E-notice Count",
        17: "ETC Txn Count", 18: "ETC Txn Amount", 19: "E-notice Count",
        20: "ETC Txn Count", 21: "ETC Txn Amount", 22: "E-notice Count",
        23: "ETC Txn Count", 24: "ETC Txn Amount", 25: "E-notice Count",
        26: "E-notice Amount Received",
    }
    for index, name in metric_names.items():
        metric_header[index] = name
    data = ["From 01-08-2026 to 31-08-2026", "Sample Plaza", "Sample PIU", "Sample RO"] + [None] * 23
    for index, value in {
        4: "1,000", 5: "100,000", 6: "3", 7: "4",
        8: "10", 9: "2,000", 10: "5",
        11: "0", 12: "0", 13: "0", 14: "0", 15: "0", 16: "0",
        17: "0", 18: "0", 19: "0", 20: "0", 21: "0", 22: "0",
        23: "1,010", 24: "102,000", 25: "9", 26: "800",
    }.items():
        data[index] = value
    if combined_group_header:
        title = ["MLFF monthly report"] + [None] * 26
        return [title, group_header, metric_header, data]
    group_header[4:] = metric_header[4:]
    return [categories, group_header, data]


class TestMlffExport(unittest.TestCase):
    def test_two_digit_year_filename_resolves_to_2000s(self):
        self.assertEqual(period_from_filename("MLFF-Plaza-Data-May-26_v1.pdf"), "2026-05")

    def test_parse_combined_category_and_month_header(self):
        rows = parse_mlff_table(mlff_rows(combined_group_header=True))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vehicles"]["Car/Van/Jeep"]["annual_pass_count"], 3)
        self.assertEqual(rows[0]["vehicles"]["LCV"]["transaction_count"], 10)

    def test_parse_separate_category_header(self):
        rows = parse_mlff_table(mlff_rows(combined_group_header=False))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"]["transaction_count"], 1010)
        self.assertEqual(rows[0]["total"]["transaction_amount"], 102000)
        self.assertEqual(rows[0]["total"]["e_notice_count"], 9)


if __name__ == "__main__":
    unittest.main()