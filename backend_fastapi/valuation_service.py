# -*- coding: utf-8 -*-
"""股票估值计算：两段法净利润贴现（前 N 年预测 + 永续增长）。

方法与口径来源：高阶课《股票价值计算说明》与《股票价值计算表.xlsx》。

核心口径：
  1. 贴现率统一 10%，永续增长率建议用 0；
  2. 期初净利润使用**扣非净利润**，避免非经常性损益影响；
  3. 增长率 = (机构预测第 N 年净利润 / 期初净利润) ^ (1/N) - 1；
  4. 估值公式（与计算表一致）：
       每股价值 = Σ_{t=1..5} E0×(1+g)^t/(1+r)^t
                  + E0×(1+g)^5/(r-g∞)/(1+r)^5        （永续部分）
       再整体 × (1+g)^lead / (1+r)^shift / S        （起点前移 & 折现回当前）
     其中 E0=期初净利润、S=股本、g=增长率、r=贴现率；lead=2、shift=3 沿用计算表口径；
  5. 三档情景：乐观 = 1×增长率，中性 = 0.8×，悲观 = 0.8×0.8×。
"""
from __future__ import annotations

import datetime as dt
import re
import threading

import requests

import market_service as ms

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0 Safari/537.36")
SINA_REFERER = "https://finance.sina.com.cn/"

DEFAULT_DISCOUNT_RATE = 0.10        # 贴现率（统一 10%）
DEFAULT_PERPETUAL_GROWTH = 0.0      # 永续增长率（建议 0）
DEFAULT_STAGE1_YEARS = 5            # 前段预测年数
DEFAULT_LEAD_YEARS = 0              # 标准两段法：不做起点前移
DEFAULT_DISCOUNT_SHIFT = 0          # 标准两段法：不做额外折现
DEFAULT_FORECAST_YEARS = 3          # 机构预测年数

# 三档增长率口径（对齐《股票估值_单文件模板.py》）
OPTIMISTIC_FACTOR = 1.5             # 乐观 = 中性 × 1.5
PESSIMISTIC_GROWTH = 0.05           # 悲观固定 5%
FALLBACK_NEUTRAL_GROWTH = 0.10      # 无机构预测时，中性兜底 10%
FALLBACK_OPTIMISTIC_GROWTH = 0.25   # 无机构预测时，乐观兜底 25%
SCENARIO_KEYS = (("optimistic", "乐观"), ("neutral", "中性"), ("pessimistic", "悲观"))

METHOD_NOTES = [
    "贴现率统一取 10%，永续增长率建议取 0",
    "期初净利润优先使用 TTM 归母净利润，缺失时退回年报扣非 / 年报归母",
    "三档增长率：乐观 = 机构预测复合增速 × 1.5，中性 = 机构预测复合增速，悲观固定 5%",
    "未提供机构预测时按模板兜底：中性 10%、乐观 25%、悲观 5%",
    "模型对困境公司、强周期公司、重组中的公司适用性有限",
    "结果为模型估算，不构成任何投资建议",
]

# 财务摘要里的净利润指标名（优先级：TTM 归母 → 年报扣非 → 年报归母）
_PARENT_PROFIT_KEYS = ("归母净利润", "归属母公司股东的净利润", "净利润")
_DEDUCT_PROFIT_KEYS = ("扣非净利润", "扣除非经常性损益后的净利润")
_SHARE_KEYS = ("总股本", "实收资本", "股本")


def _num(value, digits=4):
    """安全转 float。"""
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text or text in {"--", "-"}:
            return None
        return round(float(text), digits)
    except (TypeError, ValueError):
        return None


def _get(url, referer=None, timeout=12, encoding=None):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    if encoding:
        resp.encoding = encoding
    return resp


def _market_prefix(code: str) -> str:
    """6 位代码 → sh / sz / bj 前缀。"""
    if code.startswith(("60", "68", "51", "58", "11")):
        return "sh"
    if code.startswith(("00", "30", "12", "15", "16", "18")):
        return "sz"
    if code.startswith(("43", "83", "87", "92")):
        return "bj"
    return "sh"


def fetch_quote(code: str, errors: list) -> dict:
    """取当前股价与总股本（亿股）。

    股价：新浪 hq.sinajs.cn；总股本：腾讯行情总市值 / 股价（东财个股接口当前不可用）。
    """
    prefix = _market_prefix(code)
    symbol = f"{prefix}{code}"
    result: dict = {"price": None, "shares": None, "source": []}

    try:
        resp = _get(f"https://hq.sinajs.cn/list={symbol}", referer=SINA_REFERER,
                    encoding="gbk", timeout=10)
        payload = resp.text.split("=", 1)[-1].strip().strip('";')
        fields = payload.split(",")
        if len(fields) > 3:
            price = _num(fields[3], 3)          # [3] = 当前价
            name = re.sub(r"\s+", "", fields[0] or "")   # 新浪名称可能带空格，如「盐 田 港」
            if price:
                result["price"] = price
                result["price_source"] = "新浪行情"
                result["source"].append("新浪行情")
            if name:
                result["name"] = name
        else:
            errors.append("新浪行情返回格式异常")
    except Exception as exc:                  # noqa: BLE001 - 单源失败只降级
        errors.append(f"新浪行情获取失败：{exc}")

    try:
        resp = _get(f"https://qt.gtimg.cn/q={symbol}", timeout=10, encoding="gbk")
        payload = resp.text.split("=", 1)[-1].strip().strip('";')
        fields = payload.split("~")
        if len(fields) > 45:
            market_cap = _num(fields[45], 2)    # 总市值（亿元）
            if not result.get("name") and fields[1]:
                result["name"] = re.sub(r"\s+", "", fields[1])
            if market_cap and result.get("price"):
                result["shares"] = round(market_cap / result["price"], 4)   # 亿股
                result["market_cap"] = market_cap
                result["shares_source"] = "腾讯行情（总市值 / 股价 反推）"
                result["source"].append("腾讯行情")
            elif market_cap:
                result["market_cap"] = market_cap
        else:
            errors.append("腾讯行情返回字段不足")
    except Exception as exc:                  # noqa: BLE001
        errors.append(f"腾讯行情获取失败：{exc}")

    return result


def _ttm_profit(row, date_cols):
    """按最新报告期折算 TTM 净利润，返回 (亿元, 口径说明)。

    年报本身即 TTM；季报 / 中报用「最新累计 + 上年年报 − 上年同期累计」折算，
    与《股票估值_单文件模板.py》的 TTM 归母口径一致。
    """
    cols = sorted([c for c in date_cols if _num(row.get(c)) is not None], reverse=True)
    if not cols:
        return None, None
    latest = cols[0]
    year, mmdd = latest[:4], latest[4:]
    if mmdd == "1231":
        value = _num(row.get(latest))
        return (round(value / 1e8, 4) if value else None), latest
    prev_annual = f"{int(year) - 1}1231"
    prev_same = f"{int(year) - 1}{mmdd}"
    current = _num(row.get(latest))
    annual = _num(row.get(prev_annual))
    same = _num(row.get(prev_same))
    if current is None or annual is None or same is None:
        return None, None
    return round((current + annual - same) / 1e8, 4), f"{latest} TTM"


def fetch_finance(code: str, errors: list) -> dict:
    """取期初净利润（TTM 归母 → 年报扣非 → 年报归母）与总股本（东财财务摘要）。"""
    result: dict = {"net_profit_base": None, "shares": None, "period": None, "indicator": None}
    if ms.ak is None:
        errors.append("akshare 不可用，无法获取财务数据")
        return result

    df = ms._ak(ms.ak.stock_financial_abstract, symbol=code)
    if df is None or getattr(df, "empty", True):
        errors.append("财务摘要获取失败（东方财富接口）")
        return result

    try:
        columns = [str(c) for c in df.columns]
        date_cols = [c for c in columns if re.fullmatch(r"\d{8}", c)]
        if not date_cols:
            errors.append("财务摘要缺少报告期列")
            return result
        # 优先取年报（1231），否则取最新一期
        annual = sorted([c for c in date_cols if c.endswith("1231")], reverse=True)
        ordered = annual + sorted([c for c in date_cols if not c.endswith("1231")], reverse=True)

        def pick(keys):
            for key in keys:
                rows = df[df["指标"].astype(str).str.strip() == key]
                if len(rows):
                    return key, rows.iloc[0]
            return None, None

        # 净利润：TTM 归母 → 年报扣非 → 年报归母（三级兜底，与单文件模板一致）
        parent_name, parent_row = pick(_PARENT_PROFIT_KEYS)
        deduct_name, deduct_row = pick(_DEDUCT_PROFIT_KEYS)

        if parent_row is not None:
            ttm_value, ttm_period = _ttm_profit(parent_row, date_cols)
            if ttm_value:
                result["net_profit_base"] = ttm_value
                result["period"] = ttm_period
                result["indicator"] = f"{parent_name} TTM"

        if result["net_profit_base"] is None and deduct_row is not None:
            for col in ordered:
                value = _num(deduct_row.get(col))
                if value:
                    result["net_profit_base"] = round(value / 1e8, 4) if abs(value) > 1e6 else value
                    result["period"] = col
                    result["indicator"] = f"{deduct_name}（年报）"
                    break

        if result["net_profit_base"] is None and parent_row is not None:
            for col in ordered:
                value = _num(parent_row.get(col))
                if value:
                    result["net_profit_base"] = round(value / 1e8, 4) if abs(value) > 1e6 else value
                    result["period"] = col
                    result["indicator"] = f"{parent_name}（年报）"
                    break

        # 总股本（可能为「股」，也可能为「亿股」）
        for key in _SHARE_KEYS:
            name, row = pick((key,))
            if row is None:
                continue
            for col in ordered:
                value = _num(row.get(col))
                if value:
                    if value > 1e8:
                        value = round(value / 1e8, 4)
                    result["shares"] = value
                    result["shares_source"] = f"东财财务摘要（{name}）"
                    break
            if result.get("shares"):
                break

        if result["net_profit_base"] is None:
            errors.append("财务摘要中未找到净利润指标")
    except Exception as exc:                  # noqa: BLE001
        errors.append(f"财务摘要解析失败：{exc}")
    return result


def compute_scenario(base_profit, growth, shares, *, discount_rate=DEFAULT_DISCOUNT_RATE,
                     perpetual_growth=DEFAULT_PERPETUAL_GROWTH,
                     stage1_years=DEFAULT_STAGE1_YEARS,
                     lead_years=DEFAULT_LEAD_YEARS,
                     discount_shift=DEFAULT_DISCOUNT_SHIFT):
    """单情景两段法计算，返回每股价值与逐步明细（供前端展示「计算过程」）。"""
    steps = []
    pv_sum = 0.0

    for year in range(1, stage1_years + 1):
        cash = base_profit * (1 + growth) ** year
        pv = cash / (1 + discount_rate) ** year
        pv_sum += pv
        steps.append({
            "label": f"第 {year} 年净利润贴现",
            "formula": f"E0 × (1+g)^{year} ÷ (1+r)^{year}",
            "detail": f"{base_profit:.2f} × (1+{growth:.4f})^{year} ÷ (1+{discount_rate:.2f})^{year}",
            "value": round(pv, 4),
        })

    terminal_cash = base_profit * (1 + growth) ** stage1_years
    denominator = discount_rate - perpetual_growth
    terminal_value = terminal_cash / denominator if denominator else None
    terminal_pv = (terminal_value / (1 + discount_rate) ** stage1_years
                   if terminal_value is not None else None)
    if terminal_pv is not None:
        pv_sum += terminal_pv
        steps.append({
            "label": "永续现金流贴现",
            "formula": f"E0 × (1+g)^{stage1_years} ÷ (r - g∞) ÷ (1+r)^{stage1_years}",
            "detail": (f"{base_profit:.2f} × (1+{growth:.4f})^{stage1_years} ÷ "
                       f"({discount_rate:.2f} - {perpetual_growth:.2f}) ÷ "
                       f"(1+{discount_rate:.2f})^{stage1_years}"),
            "value": round(terminal_pv, 4),
        })

    steps.append({
        "label": "前段折现合计（元年口径）",
        "formula": "Σ 上述各项",
        "detail": "第 1~%d 年贴现 + 永续贴现" % stage1_years,
        "value": round(pv_sum, 4),
    })

    lead_factor = (1 + growth) ** lead_years
    shift_factor = (1 + discount_rate) ** discount_shift
    total_value = pv_sum * lead_factor / shift_factor
    steps.append({
        "label": f"起点前移 {lead_years} 年、整体折现 {discount_shift} 年",
        "formula": f"合计 × (1+g)^{lead_years} ÷ (1+r)^{discount_shift}",
        "detail": (f"{pv_sum:.4f} × (1+{growth:.4f})^{lead_years} ÷ "
                   f"(1+{discount_rate:.2f})^{discount_shift}"),
        "value": round(total_value, 4),
    })

    per_share = total_value / shares if shares else None
    if per_share is not None:
        steps.append({
            "label": "每股价值",
            "formula": "整体价值 ÷ 总股本",
            "detail": f"{total_value:.4f} ÷ {shares:.4f}",
            "value": round(per_share, 4),
        })

    return {
        "growth": round(growth, 6),
        "total_value": round(total_value, 4),
        "value_per_share": round(per_share, 4) if per_share is not None else None,
        "steps": steps,
    }


def quote_only(code: str) -> dict:
    """仅查名称与当前股价（不拉财务数据），供前端输入代码后即时确认。"""
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return {"ok": False, "error": "股票代码必须是 6 位数字"}
    errors: list = []
    quote = fetch_quote(code, errors)
    if not quote.get("name"):
        return {"ok": False, "code": code, "errors": errors,
                "error": "未找到该代码对应的股票，请检查代码是否正确"}
    return {
        "ok": True,
        "code": code,
        "name": quote.get("name"),
        "price": quote.get("price"),
        "market_cap": quote.get("market_cap"),
        "price_source": quote.get("price_source"),
        "errors": errors,
    }


def valuate(req: dict) -> dict:
    """估值主入口：补齐数据 → 计算三情景 → 返回结果与过程。"""
    errors: list = []
    code = str(req.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return {"ok": False, "error": "股票代码必须是 6 位数字"}

    auto = req.get("auto_fetch", True)
    quote = fetch_quote(code, errors) if auto else {"price": None, "shares": None, "source": []}
    finance = fetch_finance(code, errors) if auto else {"net_profit_base": None, "shares": None}
    if auto and not finance.get("net_profit_base"):
        # 首次调用可能撞上数据源冷启动或抖动，重试一次再判定缺失
        retry = fetch_finance(code, [])
        if retry.get("net_profit_base"):
            finance = {**finance, **{k: v for k, v in retry.items() if v}}

    price = _num(req.get("price")) or quote.get("price")
    shares = _num(req.get("shares")) or quote.get("shares") or finance.get("shares")
    base_profit = _num(req.get("net_profit_base")) or finance.get("net_profit_base")
    forecast_profit = _num(req.get("net_profit_forecast"))
    forecast_years = int(req.get("forecast_years") or DEFAULT_FORECAST_YEARS)

    discount_rate = _num(req.get("discount_rate"))
    discount_rate = DEFAULT_DISCOUNT_RATE if discount_rate is None else discount_rate
    perpetual_growth = _num(req.get("perpetual_growth"))
    perpetual_growth = DEFAULT_PERPETUAL_GROWTH if perpetual_growth is None else perpetual_growth
    predict_years = int(req.get("predict_years") or 1)

    missing = []
    if not price:
        missing.append("当前股价")
    if not shares:
        missing.append("总股本")
    if not base_profit:
        missing.append("期初净利润")
    if missing:
        return {"ok": False, "code": code, "errors": errors,
                "error": "缺少必要参数：" + "、".join(missing) + "（可在页面手动填写后重算）",
                "inputs": {"price": price, "shares": shares, "net_profit_base": base_profit}}

    # 增长率三档（对齐单文件模板）：机构预测为可选项，缺失时退回兜底常数
    auto_growth = None
    if forecast_profit:
        auto_growth = (forecast_profit / base_profit) ** (1 / forecast_years) - 1
        growth_steps = [{
            "label": "复合增长率（机构预测推算）",
            "formula": f"(预测净利润 ÷ 期初净利润)^(1/{forecast_years}) - 1",
            "detail": f"({forecast_profit:.2f} ÷ {base_profit:.2f})^(1/{forecast_years}) - 1",
            "value": round(auto_growth, 6),
        }]
    else:
        growth_steps = [{
            "label": "复合增长率（兜底，未提供机构预测）",
            "formula": "按模板兜底值",
            "detail": (f"中性 {FALLBACK_NEUTRAL_GROWTH:.0%} · 乐观 {FALLBACK_OPTIMISTIC_GROWTH:.0%} · "
                       f"悲观 {PESSIMISTIC_GROWTH:.0%}"),
            "value": FALLBACK_NEUTRAL_GROWTH,
        }]

    if auto_growth is not None:
        scenario_growth = {
            "optimistic": auto_growth * OPTIMISTIC_FACTOR,
            "neutral": auto_growth,
            "pessimistic": PESSIMISTIC_GROWTH,
        }
    else:
        scenario_growth = {
            "optimistic": FALLBACK_OPTIMISTIC_GROWTH,
            "neutral": FALLBACK_NEUTRAL_GROWTH,
            "pessimistic": PESSIMISTIC_GROWTH,
        }

    scenarios = []
    for key, label in SCENARIO_KEYS:
        growth = scenario_growth[key]
        calc = compute_scenario(base_profit, growth, shares,
                                discount_rate=discount_rate,
                                perpetual_growth=perpetual_growth)
        per_share = calc["value_per_share"]
        ratio = round(price / per_share, 4) if per_share else None
        target = round(per_share * (1 + growth) ** predict_years, 4) if per_share else None
        ret = round(target / price - 1, 4) if (target and price) else None
        scenarios.append({
            "key": key,
            "label": label,
            "growth": calc["growth"],
            "growth_basis": ("固定 5%" if key == "pessimistic" else
                             ("机构预测推算" if auto_growth is not None else "模板兜底")),
            "total_value": calc["total_value"],
            "value_per_share": per_share,
            "undervalued_ratio": ratio,        # 股价 / 价值：<1 低估，>1 高估
            "verdict": (None if ratio is None else
                        ("低估" if ratio < 0.9 else "合理" if ratio <= 1.1 else "高估")),
            "target_price": target,            # 预测 N 年价
            "return_rate": ret,                # 预测 N 年收益率
            "steps": calc["steps"],
        })

    return {
        "ok": True,
        "code": code,
        "name": req.get("name") or quote.get("name") or "",
        "as_of": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": {
            "price": price,
            "shares": shares,
            "market_cap": quote.get("market_cap"),
            "price_source": quote.get("price_source"),
            "shares_source": quote.get("shares_source") or finance.get("shares_source"),
        },
        "finance": {
            "net_profit_base": base_profit,
            "indicator": finance.get("indicator"),
            "period": finance.get("period"),
            "net_profit_forecast": forecast_profit,
            "forecast_years": forecast_years,
        },
        "growth": {"cagr": round(auto_growth, 6) if auto_growth is not None else None,
                   "from_consensus": auto_growth is not None,
                   "steps": growth_steps},
        "scenarios": scenarios,
        "assumptions": {
            "discount_rate": discount_rate,
            "perpetual_growth": perpetual_growth,
            "stage1_years": DEFAULT_STAGE1_YEARS,
            "lead_years": DEFAULT_LEAD_YEARS,
            "discount_shift": DEFAULT_DISCOUNT_SHIFT,
            "predict_years": predict_years,
        },
        "notes": METHOD_NOTES,
        "errors": errors,
    }
