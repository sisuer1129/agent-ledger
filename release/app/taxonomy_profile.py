"""Map the reviewed 768-row ledger onto the user's final long-term taxonomy."""

import re

from canonical_taxonomy import CATEGORY_ALIASES


def _has(text, pattern):
    return re.search(pattern, text, re.IGNORECASE) is not None


def remap_category(primary, secondary, description, transaction_kind):
    primary = (primary or "").strip()
    secondary = (secondary or "").strip()
    text = (description or "").strip()

    if transaction_kind == "credit_repayment":
        return "资金往来", "信用卡还款"
    if transaction_kind == "topup_withdrawal":
        return "资金往来", "充值提现"
    if transaction_kind == "balance_adjustment":
        return "资金往来", "余额调整"
    if transaction_kind == "transfer":
        if primary == "内部转账":
            return "内部转账", "家庭资金调拨"
        return "资金往来", "账户转账"
    if transaction_kind == "refund":
        if primary == "报销退款":
            return "报销退款", "工作报销"
        return "资金往来", "退款"

    pair = CATEGORY_ALIASES.get((primary, secondary), (primary, secondary))
    primary, secondary = pair

    if primary == "餐饮":
        if _has(text, r"咖啡|coffee"):
            return "餐饮", "咖啡饮品"
        if _has(text, r"外卖"):
            return "餐饮", "外卖"
        if secondary in ("正餐外卖", "食品杂货") and _has(
            text, r"便利|零食|面包|果园"
        ):
            return "餐饮", "零食便利店"
        if secondary == "食品杂货":
            return "日用购物", "食品杂货"
        return "餐饮", "正餐"

    if primary == "交通出行":
        if _has(text, r"网约车|出租车"):
            return "交通出行", "打车"
        if _has(text, r"机票|火车|高铁"):
            return "交通出行", "火车机票"
        if _has(text, r"共享单车|自行车"):
            return "交通出行", "自行车"
        if _has(text, r"停车"):
            return "交通出行", "停车"
        if _has(text, r"加油|充电"):
            return "交通出行", "加油充电"
        if secondary == "公共交通":
            return "交通出行", "公共交通"
        return "交通出行", "其他交通"

    if primary == "车辆":
        aliases = {
            "车贷分期": "车贷／分期", "车辆贷款": "车贷／分期",
            "停车": "停车", "加油充电": "加油充电", "充电/加油": "加油充电",
            "维修保养": "维修保养", "保养维修": "维修保养",
            "车载服务": "车载服务", "车辆保险": "车辆保险",
        }
        return "车辆", aliases.get(secondary, "其他车辆费用")

    if primary == "住房居家":
        if _has(text, r"充电桩"):
            return "车辆", "加油充电"
        if _has(text, r"房租|租金"):
            return "住房居家", "房租"
        if _has(text, r"酒店|住宿|公寓"):
            return "住房居家", "长期住宿"
        if _has(text, r"物业|宽带"):
            return "住房居家", "宽带物业"
        if _has(text, r"维修|修理"):
            return "住房居家", "家庭维修"
        if _has(text, r"家居|家具"):
            return "住房居家", "家居用品"
        return "住房居家", "水电燃气"

    if primary == "通信网络":
        if _has(text, r"vpn|proxy|代理"):
            return "通信网络", "VPN"
        if _has(text, r"宽带"):
            return "通信网络", "宽带"
        if _has(text, r"网络服务"):
            return "通信网络", "网络服务"
        if _has(text, r"话费|手机费|电话卡|流量卡|电信充值"):
            return "通信网络", "手机话费"
        return "通信网络", "其他通信费用"

    if primary == "数字订阅":
        aliases = {
            "AI工具": "AI 工具", "AI 工具": "AI 工具",
            "影音会员": "影音音乐", "影音音乐": "影音音乐",
            "软件服务": "软件服务", "云服务": "云存储", "云存储": "云存储",
            "会员订阅": "会员订阅", "其他订阅": "其他订阅",
        }
        return "数字订阅", aliases.get(secondary, "其他订阅")

    if primary == "休闲娱乐":
        if _has(text, r"电影|演出"):
            return "休闲娱乐", "电影演出"
        if _has(text, r"健身|羽毛球|运动"):
            return "休闲娱乐", "运动健身"
        if _has(text, r"温泉|汤"):
            return "休闲娱乐", "温泉休闲"
        if _has(text, r"景点|主题乐园"):
            return "休闲娱乐", "景点活动"
        if _has(text, r"游戏"):
            return "休闲娱乐", "游戏娱乐"
        return "休闲娱乐", "其他娱乐"

    if primary == "旅行度假":
        if secondary == "酒店住宿" or _has(text, r"酒店|住宿"):
            return "旅行度假", "酒店住宿"
        if _has(text, r"机票|火车|高铁"):
            return "旅行度假", "机票火车"
        if _has(text, r"门票|景点"):
            return "旅行度假", "景点门票"
        if _has(text, r"当地交通|缆车|观光船|车船渡"):
            return "旅行度假", "当地交通"
        if _has(text, r"coffee|咖啡|餐|便利|超市|商场"):
            return "旅行度假", "旅行餐饮"
        if _has(text, r"旅行服务|trip"):
            return "旅行度假", "旅行服务"
        return "旅行度假", "其他旅行费用"

    if primary == "医疗健康":
        if secondary == "保健品":
            return "医疗健康", "保健品"
        if _has(text, r"牙|眼科"):
            return "医疗健康", "牙科眼科"
        if _has(text, r"体检"):
            return "医疗健康", "体检"
        if _has(text, r"医院|诊疗|门诊"):
            return "医疗健康", "医院诊疗"
        if _has(text, r"药"):
            return "医疗健康", "药品"
        return "医疗健康", "健康服务"

    if primary == "个人护理":
        if secondary == "理发" or _has(text, r"理发|剪头"):
            return "个人护理", "理发"
        if _has(text, r"美容|护肤"):
            return "个人护理", "美容护肤"
        if _has(text, r"洗护|洗发|沐浴"):
            return "个人护理", "洗护用品"
        return "个人护理", "其他护理"

    if primary == "教育成长":
        if secondary == "子女教育":
            return "教育成长", "子女教育"
        if _has(text, r"书|笔记本|资料"):
            return "教育成长", "书籍资料"
        if _has(text, r"考试|报名|申请"):
            return "教育成长", "考试报名"
        if _has(text, r"课程|培训|补课"):
            return "教育成长", "课程培训"
        return "教育成长", "教育服务"

    if primary in ("人情与家庭", "红包礼金"):
        if primary == "红包礼金" or _has(text, r"红包|礼金"):
            return "人情与家庭", "红包礼金"
        if secondary in ("子女支出", "长辈支出"):
            return "人情与家庭", secondary
        if _has(text, r"捐赠|公益"):
            return "人情与家庭", "公益捐赠"
        if _has(text, r"礼物|赠礼|节日"):
            return "人情与家庭", "赠礼"
        return "人情与家庭", "家庭支持"

    if primary == "金融保险":
        if secondary == "个人所得税":
            return "金融保险", "个人所得税"
        if secondary == "商业保险":
            return "金融保险", "商业保险"
        if _has(text, r"分期.*手续费"):
            return "金融保险", "分期手续费"
        if _has(text, r"利息"):
            return "金融保险", "信用卡利息"
        if _has(text, r"汇兑|换汇"):
            return "金融保险", "汇兑费用"
        if _has(text, r"手续费"):
            return "金融保险", "银行手续费"
        return "金融保险", "其他金融费用"

    if primary == "工资薪酬":
        return "工资薪酬", "工资"
    if primary == "服务收入":
        return "服务收入", "兼职服务费" if secondary == "兼职服务费" else "网络服务费"
    if primary == "其他收入":
        if secondary == "返现" or _has(text, r"返现"):
            return "其他收入", "返现"
        if secondary == "退税" or _has(text, r"退税"):
            return "其他收入", "退税"
        if _has(text, r"网络服务费"):
            return "服务收入", "网络服务费"
        return "其他收入", "其他收入"
    if primary == "垫付":
        return "垫付", "待收回款"
    if primary == "内部转账":
        return "内部转账", "家庭资金调拨"
    if primary == "报销退款":
        return "报销退款", "工作报销"
    if primary == "日用购物":
        return "日用购物", secondary if secondary in (
            "食品杂货", "日用品", "服饰鞋包", "数码电器", "家居用品", "综合购物", "礼物"
        ) else "综合购物"
    if primary == "其他支出":
        return "其他支出", "临时支出" if secondary == "临时支出" else "待确认支出"

    if transaction_kind == "income":
        return "其他收入", "其他收入"
    return "其他支出", "待确认支出"
