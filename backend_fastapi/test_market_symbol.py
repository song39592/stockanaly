# -*- coding: utf-8 -*-
"""第 16 项：三份 `market_symbol` 规则集的**边界行为锁定**（不收敛实现，只锁现状）。

## 为什么是「锁现状」而不是「统一成一份」

待办 16 原始建议是收敛为一份实现。评估结论是**不该收敛**（详见同目录
`16-后端-market_symbol三份口径.md`），理由两条：

1. **分歧段在业务上不可达**。真实股票池 5585 只的 2 位前缀只有
   `00 / 30 / 60 / 68 / 92` 五种，三份实现在这五种上**完全一致**。
   出现分歧的段（沪市可转债 `11`、B 股 `900`/`200`、北交所老号段 `43/83/87`）
   都不在池子里 —— 这些不是股票，`price_service` 也不为它们准备数据源。
2. **收敛有真实风险而收益为零**。待办的踩坑点说得很准：「不要顺手取并集」。
   若把 `price_service` 的 1 位规则改成 2 位规则，沪市可转债会从 `sz` 变 `sh` ——
   改的是一条**永不被执行**的分支，纯属引入新的分叉面。

所以本文件的定位是**回归锁**：把「三份当前各自的判定」全部钉死，
任何人改动其中一份而没同步另两份，测试立刻失败并指出是哪一段。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.price_service import market_symbol as price_sym
from core.share_service import market_symbol as share_sym
from features.stock.valuation import _market_prefix as val_prefix


def val_sym(code: str) -> str:
    return val_prefix(code) + code


# 业务范围内、三份必须一致的前缀（真实股票池只出现这五种）
POOL_PREFIXES = {
    "00": "深市主板",
    "30": "创业板",
    "60": "沪市主板",
    "68": "科创板",
    "92": "北交所新号段",
}

# 三份判定**不一致**的段 —— 这是评估发现的实情，待办原文只提到北交所。
# 每一行：代码 -> (price 前缀, share 前缀, valuation 前缀)
DIVERGENT = {
    "110059": ("sz", "sh", "sh"),   # 沪市可转债：price 的 1 位规则误判成 sz
    "113050": ("sz", "sh", "sh"),
    "900901": ("bj", "bj", "sh"),   # 沪市B股：只有 valuation 对
    "200011": ("sz", "sz", "sh"),   # 深市B股：只有 valuation 错
    "889999": ("bj", "bj", "sh"),   # 北交所白名单外的新号段占位 -> valuation 兜底 sh
    "999999": ("bj", "bj", "sh"),   # 未知号段
    "777777": ("bj", "bj", "sh"),
}


class MarketSymbolTest(unittest.TestCase):
    """三份规则集的边界行为。"""

    def test_pool_prefixes_all_agree(self):
        """业务范围内的 5 类前缀：三份必须一致（这是实际生效的口径）。"""
        for pre, label in POOL_PREFIXES.items():
            code = pre + "0000"
            a, b, c = price_sym(code), share_sym(code), val_sym(code)
            self.assertEqual(a, b, "%s（%s）price/share 不一致：%s vs %s"
                             % (code, label, a, b))
            self.assertEqual(a, c, "%s（%s）price/valuation 不一致：%s vs %s"
                             % (code, label, a, c))

    def test_documented_segments_unchanged(self):
        """三份**已知不一致**的段：钉死当前行为。

        这些段业务上不可达（不在股票池），但正是任一份被改就会静默分叉的地方。
        """
        for code, (want_p, want_s, want_v) in DIVERGENT.items():
            self.assertEqual(price_sym(code)[:2], want_p,
                             "price 对 %s 的判定变了（期望 %s）" % (code, want_p))
            self.assertEqual(share_sym(code)[:2], want_s,
                             "share 对 %s 的判定变了（期望 %s）" % (code, want_s))
            self.assertEqual(val_prefix(code), want_v,
                             "valuation 对 %s 的判定变了（期望 %s）" % (code, want_v))

    def test_valuation_bj_whitelist_gap(self):
        """valuation 的北交所白名单只列了 43/83/87/92，缺**兜底 bj**。

        这是本项识别出的**唯一真实未来风险**：北交所若开新号段，
        `valuation` 会兜底成 `sh`（新浪报价取不到），而另两份判 `bj`。
        与其等它发生，不如先把这个行为写进测试，让人知道要改哪。
        """
        # 白名单内（含 87段 —— 注意 873169 也判bj，别误以为 87 漏了）
        for code in ("430047", "830799", "870508", "873169", "920002"):
            self.assertEqual(val_prefix(code), "bj", "%s 应判 bj" % code)
            self.assertEqual(price_sym(code)[:2], "bj", "%s price 应判 bj" % code)
            self.assertEqual(share_sym(code)[:2], "bj", "%s share 应判 bj" % code)
        # 白名单之外 -> 兜底 sh（不是 bj）：这才是真正的分叉点
        for code in ("889999", "999999", "777777"):
            self.assertEqual(val_prefix(code), "sh",
                             "%s 的兜底行为变了（valuation 目前兜底 sh）" % code)
            self.assertEqual(price_sym(code)[:2], "bj",
                             "%s price 应判 bj" % code)

    def test_non_six_digit_inputs(self):
        """`share` 会 zfill 补零，`price` / `valuation` 不会。

        这是三份在**非 6 位输入**上的行为差异：传 "519" 时 price 判 sh、
        share 判 sz（zfill 成 000519）。业务上只传 6 位代码，故不改，
        但改动会让这里失败。
        """
        self.assertEqual(price_sym("519"), "sh519")
        self.assertEqual(share_sym("519"), "sz000519")
        self.assertEqual(val_sym("519"), "sh519")
        self.assertEqual(share_sym("60051"), "sz060051")
        self.assertEqual(price_sym("60051"), "sh60051")

    def test_whole_universe_agrees(self):
        """真实股票池全量：三份必须给出相同前缀（评估结论的回归保护）。

        这条是本项的核心断言 —— 若将来股票池纳入了可转债 / B 股 / 北交所新段，
        这里会先失败，提示「该重新评估口径，而不是让三份静默分叉」。
        """
        from core import price_store
        codes = sorted(price_store.code_latest_dates("raw").keys())
        if not codes:
            self.skipTest("股票池为空（无本地日线），跳过全量一致性检查")
        bad = []
        for code in codes:
            a = price_sym(code)[:2]
            if a != share_sym(code)[:2] or a != val_prefix(code):
                bad.append(code)
        self.assertFalse(
            bad,
            "股票池中有 %d 只代码三份判定不一致，前几只：%s —— "
            "说明口径需要重新评估（见 docs/todo/16-后端-market_symbol三份口径.md）"
            % (len(bad), bad[:10]))


if __name__ == "__main__":
    unittest.main()