"""SCR（筹码集中度）选股：多期数据合并 + 周级三档分类。

数据来源是行情软件导出的「临时条件股YYYYMMDD.xls」——实际多为 GBK 编码的
制表符文本（扩展名虽为 .xls），列含：代码 / 名称 / 现价 / 市盈(动) / 细分行业 /
30日涨幅% / 换手% / 流通市值 等；同时兼容真正的 Excel 与 CSV。

目录约定（位于数据根目录下，位置由 config.DATA_DIR 决定，见 storage.py）：
    <data>/chip/raw/        导入的原始文件，保留原文件名（文件名中的 YYYYMMDD 为默认日期）
    <data>/chip/processed/  计算结果：最新一次分析的 JSON 明细与汇总 CSV
    <data>/chip/meta.json   文件日期等元信息；用户手动改过的日期会覆盖文件名解析值

分类口径对齐参考脚本《SCR 周级三档分类器》：
    第一档 磨主峰     最新一期在榜 ∩ 连续 FULL_WEEKS 期全勤 ∩ 流通市值 100-800 亿 ∩ PE > 0
    第二档 向下破位   出现过但最新一期已离榜，且未达启动阈值
    第三档 启动型离榜 离榜且区间涨幅达到 LAUNCH_THRESHOLD（参考脚本用 5 日涨跌，
                      导出数据无该字段，此处以「30日涨幅%」替代，页面已标注口径）
"""

import datetime as dt
import json
import os
import re

import pandas as pd

import storage

DATA_DIR = str(storage.CHIP_DIR)
RAW_DIR = os.path.join(DATA_DIR, "raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
META_PATH = os.path.join(DATA_DIR, "meta.json")

FULL_WEEKS = 5            # 连续几期算全勤
MIN_WEEKS_ON = 2          # 最少在榜期数：低于该值（只出现过 1 期）视为噪音，直接过滤
MIN_MARKET = 100.0        # 最小流通市值（亿）
MAX_MARKET = 800.0        # 最大流通市值（亿）
LAUNCH_THRESHOLD = 10.0   # 第三档启动阈值（%）

# 列名匹配规则：按顺序匹配，先命中者优先（避免「换手%」被「短换手%」抢占）
_COLUMN_ALIASES = (
    ("code", ("代码", "code")),
    ("name", ("名称", "name")),
    ("market", ("流通市值", "流通a股市值")),
    ("pe", ("市盈", "pe")),
    ("industry", ("细分行业", "行业", "industry")),
    ("price", ("现价", "最新价", "price")),
    ("chg30", ("30日涨幅",)),
    ("turn", ("换手",)),
)


def _ensure_dirs():
    for path in (DATA_DIR, RAW_DIR, PROCESSED_DIR):
        os.makedirs(path, exist_ok=True)


def parse_date_from_name(file_name: str) -> str:
    """从文件名解析 8 位日期，返回 YYYY-MM-DD；解析不到返回空串。"""
    match = re.search(r"(\d{8})", os.path.basename(file_name or ""))
    if not match:
        return ""
    raw = match.group(1)
    try:
        return dt.datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _load_meta() -> dict:
    _ensure_dirs()
    if not os.path.exists(META_PATH):
        return {}
    try:
        with open(META_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:                     # noqa: BLE001 - 元信息损坏时按空处理
        return {}


def _save_meta(meta: dict) -> None:
    _ensure_dirs()
    with open(META_PATH, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 文件导入与管理
# --------------------------------------------------------------------------- #
def save_upload(file_name: str, content: bytes) -> dict:
    """保存上传文件到 raw/，并把文件名解析出的日期写入 meta（可被手动覆盖）。"""
    _ensure_dirs()
    safe_name = os.path.basename(file_name or "").strip()
    if not safe_name:
        return {"ok": False, "error": "文件名为空"}
    if not safe_name.lower().endswith((".xls", ".xlsx", ".csv", ".txt")):
        return {"ok": False, "error": f"不支持的文件类型：{safe_name}"}

    target = os.path.join(RAW_DIR, safe_name)
    with open(target, "wb") as handle:
        handle.write(content)

    meta = _load_meta()
    entry = meta.get(safe_name) or {}
    entry["date_auto"] = parse_date_from_name(safe_name)
    entry["date"] = entry.get("date_auto") or entry.get("date") or ""   # 已有手动修改则保留
    entry.setdefault("manual", False)
    meta[safe_name] = entry
    _save_meta(meta)

    return {"ok": True, "file": safe_name, "date": entry["date"],
            "date_auto": entry["date_auto"]}


def list_files() -> list:
    """列出 raw/ 下的数据文件及其日期（手动修改值优先），按日期升序。"""
    _ensure_dirs()
    meta = _load_meta()
    rows = []
    for name in os.listdir(RAW_DIR):
        if name.startswith(".") or name.startswith("~$"):
            continue
        full = os.path.join(RAW_DIR, name)
        if not os.path.isfile(full):
            continue
        entry = meta.get(name) or {}
        auto = entry.get("date_auto") or parse_date_from_name(name)
        date = entry.get("date") or auto
        rows.append({
            "file": name,
            "date": date,
            "date_auto": auto,
            "manual": bool(entry.get("manual")) and bool(entry.get("date")),
            "size": os.path.getsize(full),
            "mtime": dt.datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
        })
    rows.sort(key=lambda item: (item["date"] or "9999", item["file"]))
    return rows


def set_file_date(file_name: str, date: str) -> dict:
    """手动修改某个文件的所属日期（保留手动修改能力）。"""
    _ensure_dirs()
    safe_name = os.path.basename(file_name or "")
    if not os.path.exists(os.path.join(RAW_DIR, safe_name)):
        return {"ok": False, "error": f"文件不存在：{safe_name}"}
    text = (date or "").strip()
    if text and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    meta = _load_meta()
    entry = meta.get(safe_name) or {}
    entry.setdefault("date_auto", parse_date_from_name(safe_name))
    entry["date"] = text
    entry["manual"] = bool(text)
    meta[safe_name] = entry
    _save_meta(meta)
    return {"ok": True, "file": safe_name, "date": text}


def delete_file(file_name: str) -> dict:
    """删除已导入的数据文件及其元信息。"""
    safe_name = os.path.basename(file_name or "")
    full = os.path.join(RAW_DIR, safe_name)
    if not os.path.exists(full):
        return {"ok": False, "error": f"文件不存在：{safe_name}"}
    os.remove(full)
    meta = _load_meta()
    meta.pop(safe_name, None)
    _save_meta(meta)
    return {"ok": True, "file": safe_name}


# --------------------------------------------------------------------------- #
# 表格解析
# --------------------------------------------------------------------------- #
def clean_code(value) -> str:
    """清洗股票代码：处理 ="601128" 包装与不可见字符。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    match = re.match(r'^="?(\d+)"?$', text)
    return match.group(1) if match else re.sub(r"\D", "", text)


def _clean_name(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", "", str(value).replace('"', ""))


def parse_market_cap(value):
    """「233.85亿」/「1,234万」→ 亿元。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "")
    match = re.match(r"([\d.]+)\s*(亿|万)?", text)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2) == "万":
        number /= 10000
    return round(number, 4)


def _to_number(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text in ("", "—", "-", "/", "N/A", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_table(path: str) -> pd.DataFrame:
    """读取数据文件：兼容真 Excel、GBK 制表符文本（伪 .xls）与 CSV。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        try:
            return pd.read_excel(path, engine="openpyxl")
        except Exception:                 # noqa: BLE001 - 落回文本解析
            pass
    if ext == ".xls":
        try:
            return pd.read_excel(path, engine="xlrd")
        except Exception:                 # noqa: BLE001 - 行情软件导出的 GBK 文本
            pass
    for encoding in ("gbk", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, sep="\t", encoding=encoding)
        except Exception:                 # noqa: BLE001 - 换下一种编码
            continue
    try:
        return pd.read_csv(path, sep=None, engine="python", encoding="gbk")
    except Exception:                     # noqa: BLE001
        return pd.DataFrame()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """列名归一化为内部字段，返回值清洗后的精简表。"""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    mapping: dict = {}
    for target, keywords in _COLUMN_ALIASES:
        for column in df.columns:
            if column in mapping:
                continue
            lowered = column.lower()
            if any(key.lower() in lowered for key in keywords):
                mapping[column] = target
                break
    df = df.rename(columns=mapping)
    if "code" not in df.columns:
        return pd.DataFrame()

    result = pd.DataFrame()
    result["code"] = df["code"].apply(clean_code)
    result["name"] = df["name"].apply(_clean_name) if "name" in df.columns else ""
    result["market"] = df["market"].apply(parse_market_cap) if "market" in df.columns else None
    result["pe"] = df["pe"].apply(_to_number) if "pe" in df.columns else None
    result["industry"] = df["industry"].fillna("").astype(str).str.strip() if "industry" in df.columns else ""
    result["price"] = df["price"].apply(_to_number) if "price" in df.columns else None
    result["chg30"] = df["chg30"].apply(_to_number) if "chg30" in df.columns else None
    result["turn"] = df["turn"].apply(_to_number) if "turn" in df.columns else None
    return result[result["code"].str.len() == 6]


# --------------------------------------------------------------------------- #
# 三档分类计算
# --------------------------------------------------------------------------- #
MARKET_CLOSE_HOUR = 15     # A 股 15:00 收盘：周五收盘后当日数据即已生成


def _as_moment(value):
    """把 None / str / date / datetime 统一成 datetime。

    纯日期（含 YYYY-MM-DD 字符串）按「当日结束」处理；带时刻的按给定时刻判断。
    """
    if value is None:
        return dt.datetime.now()
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, 23, 59)
    text = str(value).strip()
    if len(text) <= 10:                     # 纯日期（YYYY-MM-DD）：按当日结束处理
        return dt.datetime.combine(dt.date.fromisoformat(text[:10]), dt.time(23, 59))
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.datetime.combine(dt.date.fromisoformat(text[:10]), dt.time(23, 59))


def week_start(value):
    """日期（字符串或 date/datetime）→ 所在周的周一。"""
    day = _as_moment(value).date()
    return day - dt.timedelta(days=day.weekday())


def expected_weeks(weeks: int = FULL_WEEKS, now=None) -> list:
    """推算最近 weeks 期应有的数据节点（按周，由新到旧）。

    SCR 数据每周一份、周五盘后导出，因此最新节点按「当期数据是否已生成」判断：
      · 周一 ~ 周四        → 上一周（本周尚未结束）
      · 周五 15:00 之前   → 上一周
      · 周五 15:00 及之后 → 本周（收盘后当日数据已生成，不必等到周末）
      · 周六 / 周日       → 本周
    """
    moment = _as_moment(now)
    this_monday = week_start(moment)
    friday_closed = moment.weekday() == 4 and moment.hour >= MARKET_CLOSE_HOUR
    closed = moment.weekday() >= 5 or friday_closed
    latest = this_monday if closed else this_monday - dt.timedelta(weeks=1)
    return [latest - dt.timedelta(weeks=index) for index in range(weeks)]


def analyze(params: dict = None) -> dict:
    """读取 raw/ 下最近 weeks 周的数据，合并后做三档分类，并把结果写入 processed/。

    数据完备性：必须覆盖最近 weeks 周，缺少任一期的数据直接报错（不静默跳过）；
    窗口之外的更早文件不参与本次计算。
    """
    settings = params or {}
    full_weeks = int(settings.get("full_weeks") or FULL_WEEKS)
    min_market = float(settings.get("min_market") if settings.get("min_market") is not None else MIN_MARKET)
    max_market = float(settings.get("max_market") if settings.get("max_market") is not None else MAX_MARKET)
    threshold = float(settings.get("launch_threshold")
                      if settings.get("launch_threshold") is not None else LAUNCH_THRESHOLD)
    min_weeks_on = int(settings.get("min_weeks_on") or MIN_WEEKS_ON)

    errors: list = []
    all_files = list_files()
    files = [item for item in all_files if item["date"]]
    undated = [item["file"] for item in all_files if not item["date"]]
    if undated:
        errors.append("以下文件未能解析出日期：" + "、".join(undated))
    if not files:
        return {"ok": False, "error": "尚未导入任何数据文件，请先在上方导入「临时条件股YYYYMMDD.xls」",
                "errors": errors, "files": all_files}

    # 按周归组：同一周内的多份文件只取日期最新的一份
    week_map: dict = {}
    for item in files:
        monday = week_start(item["date"])
        current = week_map.get(monday)
        if current is None or item["date"] > current["date"]:
            week_map[monday] = item

    expected = expected_weeks(full_weeks)
    missing = sorted(monday for monday in expected if monday not in week_map)
    window_info = {
        "need": full_weeks,
        "latest_expected": expected[0].isoformat(),
        "expected": [monday.isoformat() for monday in expected],
        "covered": sorted(monday.isoformat() for monday in week_map if monday in set(expected)),
        "extra": sorted(monday.isoformat() for monday in week_map if monday not in set(expected)),
    }
    if missing:
        detail = "；".join(
            f"{monday.isoformat()} ~ {(monday + dt.timedelta(days=6)).isoformat()}" for monday in missing)
        return {
            "ok": False,
            "error": (f"数据不完整：缺少最近 {full_weeks} 周中的 {len(missing)} 期（{detail}）。"
                      f"最新一期应覆盖 {expected[0].isoformat()} ~ "
                      f"{(expected[0] + dt.timedelta(days=6)).isoformat()} 这一周"
                      f"（周一~周四及周五收盘前最新节点为上一周，周五收盘后及周末为本周），"
                      f"请补齐后重新计算"),
            "missing_weeks": [monday.isoformat() for monday in missing],
            "window": window_info,
            "errors": errors,
            "files": all_files,
        }

    # 仅使用窗口内的数据（更早的文件不参与计算）
    window_files = sorted((week_map[monday] for monday in expected), key=lambda item: item["date"])

    merged: dict = {}
    periods = []
    for item in window_files:
        frame = _normalize(read_table(os.path.join(RAW_DIR, item["file"])))
        if frame.empty:
            errors.append(f"{item['file']} 解析失败或无有效数据行")
            continue
        periods.append({"date": item["date"], "file": item["file"], "count": int(len(frame))})
        for row in frame.itertuples(index=False):
            entry = merged.get(row.code)
            if entry is None:
                entry = {"code": row.code, "name": row.name, "weeks": [], "dates": [], "market": row.market,
                         "pe": row.pe, "industry": row.industry, "price": row.price,
                         "chg30": row.chg30, "turn": row.turn}
                merged[row.code] = entry
            else:
                # 后续期数覆盖为空的历史字段，保证展示最新可得值
                for field, value in (("name", row.name), ("market", row.market), ("pe", row.pe),
                                     ("industry", row.industry), ("price", row.price),
                                     ("chg30", row.chg30), ("turn", row.turn)):
                    if value not in (None, "") and entry.get(field) in (None, ""):
                        entry[field] = value
            # weeks 存「周标识」（该周周一），保证同一周多份文件只算一期；dates 存原始日期用于展示
            key = week_start(item["date"]).isoformat()
            if key not in entry["weeks"]:
                entry["weeks"].append(key)
            entry["dates"].append(item["date"])

    if not periods:
        return {"ok": False, "error": "没有可用数据（文件解析失败）", "errors": errors,
                "files": files}

    latest_key = expected[0].isoformat()
    tier1, tier2, tier3 = [], [], []
    filtered_weeks = 0          # 因在榜期数不足（低于 min_weeks_on）被过滤
    filtered_market = 0         # 因流通市值不在区间内被过滤
    unclassified = 0            # 仍在最新一期在榜、但未满全勤期数（观察中，不入档）

    for entry in merged.values():
        weeks = sorted(entry["weeks"])
        dates = sorted(entry.get("dates") or [])
        on_latest = latest_key in weeks
        market = entry.get("market")
        pe = entry.get("pe")
        base = {
            "code": entry["code"],
            "name": entry.get("name") or "",
            "industry": entry.get("industry") or "",
            "market": market,
            "pe": pe,
            "weeks_on": len(weeks),
            "last_week": dates[-1] if dates else "",
            "first_week": dates[0] if dates else "",
            "price": entry.get("price"),
            "chg30": entry.get("chg30"),
            "turn": entry.get("turn"),
        }
        # 前置门槛（三档通用）：
        #   1) 在榜期数不足的标的多为一次性噪音，直接过滤（默认最少 2 期）；
        #   2) 流通市值须落在区间内——参考脚本未对离榜标的做市值过滤，导致 900 亿以上大盘股混入离榜池。
        # PE > 0 仅第一档要求（离榜池不排除亏损股）。
        if len(weeks) < min_weeks_on:
            filtered_weeks += 1
            continue
        in_range = bool(market and min_market <= market <= max_market)
        if not in_range:
            filtered_market += 1
            continue

        if on_latest and len(weeks) >= full_weeks and pe and pe > 0:
            tier1.append({**base, "on_latest": True})
        elif not on_latest:
            if (entry.get("chg30") or 0) >= threshold:
                tier3.append({**base, "on_latest": False})
            else:
                tier2.append({**base, "on_latest": False})
        else:
            unclassified += 1   # 刚刚入榜、期数还没攒够，等下一期再定档

    tier1.sort(key=lambda item: -(item.get("market") or 0))
    tier2.sort(key=lambda item: -(item.get("market") or 0))
    tier3.sort(key=lambda item: -(item.get("chg30") or 0))

    result = {
        "ok": True,
        "as_of": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "periods": periods,
        "latest": periods[-1]["date"],
        "window": window_info,
        "params": {"full_weeks": full_weeks, "min_market": min_market,
                   "max_market": max_market, "launch_threshold": threshold,
                   "min_weeks_on": min_weeks_on, "launch_field": "30日涨幅%",
                   "filtered_out": filtered_market + filtered_weeks,
                   "filtered_market": filtered_market, "filtered_weeks": filtered_weeks,
                   "unclassified": unclassified},
        "tiers": {
            "tier1": {"key": "tier1", "name": "第一档 · 磨主峰",
                      "desc": f"最新一期在榜 ∩ 连续 {full_weeks} 期全勤 ∩ 流通市值 "
                              f"{min_market:.0f}-{max_market:.0f} 亿 ∩ PE > 0",
                      "count": len(tier1), "items": tier1},
            "tier2": {"key": "tier2", "name": "第二档 · 向下破位离榜",
                      "desc": f"曾入选但最新一期已离榜、区间涨幅未达启动阈值（市值同域 "
                              f"{min_market:.0f}-{max_market:.0f} 亿）｜向上为启动前深度洗盘、"
                              f"向下为真破位，公式暂无法识别方向",
                      "risk": "该形态存在两个相反的演化方向，本系统公式目前无法识别："
                              "向上——筹码最高峰锁定、价格企稳，为启动前深度洗盘（第一预期回拉主峰价，"
                              "后续强势突破则大概率进入主升）；向下——下方筹码持续堆积、支撑失守，"
                              "为真正的破位下行。下方筹码堆积时千万不能过早介入，"
                              "须等价格企稳、回拉主峰价确认后再评估，并结合筹码结构与量能人工判断。",
                      "count": len(tier2), "items": tier2},
            "tier3": {"key": "tier3", "name": "第三档 · 启动型离榜",
                      "desc": f"离榜且 30日涨幅 ≥ {threshold:.0f}%"
                              f"（市值同域：{min_market:.0f}-{max_market:.0f} 亿，PE 不限；"
                              f"参考脚本用 5 日涨跌，导出数据无该字段）",
                      "count": len(tier3), "items": tier3},
        },
        "total": len(merged),
        "errors": errors,
    }

    result["saved"] = _save_result(result)
    return result


def _save_result(result: dict) -> dict:
    """把分析结果写入 processed/：JSON 明细 + 汇总 CSV。"""
    _ensure_dirs()
    stamp = result["latest"].replace("-", "") or dt.date.today().strftime("%Y%m%d")
    json_path = os.path.join(PROCESSED_DIR, f"scr_result_{stamp}.json")
    csv_path = os.path.join(PROCESSED_DIR, f"scr_summary_{stamp}.csv")

    payload = {k: v for k, v in result.items() if k != "saved"}
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    rows = []
    for tier in result["tiers"].values():
        for item in tier["items"]:
            rows.append({"档位": tier["name"], **{k: item.get(k) for k in
                        ("code", "name", "industry", "market", "pe", "weeks_on", "last_week",
                         "price", "chg30", "turn")}})
    if rows:
        pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

    return {"json": os.path.relpath(json_path, BASE_DIR).replace("\\", "/"),
            "csv": os.path.relpath(csv_path, BASE_DIR).replace("\\", "/") if rows else ""}
