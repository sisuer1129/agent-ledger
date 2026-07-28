import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from taxonomy_profile import remap_category


class TaxonomyProfileTest(unittest.TestCase):
    def test_moves_grocery_and_splits_obvious_food_subtypes(self):
        self.assertEqual(
            remap_category("餐饮", "食品杂货", "示例菜场水果", "expense"),
            ("日用购物", "食品杂货"),
        )
        self.assertEqual(
            remap_category("餐饮", "正餐外卖", "示例便利店", "expense"),
            ("餐饮", "零食便利店"),
        )
        self.assertEqual(
            remap_category("餐饮", "正餐外卖", "示例外卖", "expense"),
            ("餐饮", "外卖"),
        )

    def test_splits_transport_home_and_travel_by_description(self):
        self.assertEqual(
            remap_category("交通出行", "公共交通", "示例网约车", "expense"),
            ("交通出行", "打车"),
        )
        self.assertEqual(
            remap_category("住房居家", "水电物业", "示例物业费", "expense"),
            ("住房居家", "宽带物业"),
        )
        self.assertEqual(
            remap_category("旅行度假", "旅行支出", "咖啡和旅行餐", "expense"),
            ("旅行度假", "旅行餐饮"),
        )

    def test_non_consumption_kinds_remain_visible_but_separate(self):
        self.assertEqual(
            remap_category("", "信用卡还款", "还款示例信用卡D", "credit_repayment"),
            ("资金往来", "信用卡还款"),
        )
        self.assertEqual(
            remap_category("内部转账", "家庭资金调拨", "家庭转账", "transfer"),
            ("内部转账", "家庭资金调拨"),
        )


if __name__ == "__main__":
    unittest.main()
