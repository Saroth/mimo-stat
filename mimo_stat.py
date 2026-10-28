#!/usr/bin/env python3
"""MiMo 平台 token 使用量查询工具，用于 tmux 状态栏等场景。

API 端点:
- GET  /api/v1/tokenPlan/detail              — 套餐详情
- GET  /api/v1/tokenPlan/usage              — 月度使用量
- POST /api/v1/usage/token-plan/list      — 每日使用明细

认证方式: 小米账号 Cookie，从浏览器登录后获取。
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import yaml


class AuthError(Exception):
    """认证失败异常（401）。"""
    pass


CONFIG_DIR = Path(__file__).resolve().parent / "conf"
CONFIG_FILE = CONFIG_DIR / "config.yml"
CACHE_FILE = CONFIG_DIR / "cache.json"
CACHE_TTL = 30    # MiMo 缓存有效期（秒）
HISTORY_FILE = CONFIG_DIR / "history.json"    # 已定稿历史（区分模型），跨天/月复用
HISTORY_KEEP_DAYS = 90      # 历史文件保留的天数
HISTORY_KEEP_MONTHS = 24  # 历史文件保留的月数

# 图标定义
ICON_MIMO = ""    # MiMo 平台标识

DEFAULT_CONFIG = {
    "base_url": "https://platform.xiaomimimo.com",
    "cookie": "",
    "display": {
        "recent_days": 3,
        "recent_months": 2,
        "value_format": "token",   # token | percent | amount
        "precision": 3,               # 小数点位数（amount 模式固定 2 位）
    },
}

# 套餐价格映射: plan_code -> (月价¥, 月度Credits)
# plan_code 来自 get_plan_detail 返回的 data.planCode
PLAN_PRICING = {
    "lite:month":      (39,      4_100_000_000),
    "standard:month": (99,     11_000_000_000),
    "pro:month":      (329,     38_000_000_000),
    "max:month":      (659,     82_000_000_000),
    "lite:year":      (411.84,    49_200_000_000),
    "standard:year":  (1045.44, 132_000_000_000),
    "pro:year":          (3474.24, 456_000_000_000),
    "max:year":          (6959.04, 984_000_000_000),
}


def load_config() -> dict:
    """加载配置文件，不存在则创建默认配置。"""
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, allow_unicode=True)
        print(f"已创建默认配置: {CONFIG_FILE}", file=sys.stderr)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    # 合并默认值
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    return config


def update_cookie(cookie: str) -> None:
    """更新配置文件中的 cookie 值。"""
    config = load_config()
    config["cookie"] = cookie
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"Cookie 已更新到 {CONFIG_FILE}")


def login_with_browser() -> bool:
    """通过浏览器登录获取 Cookie。返回 True 表示成功，False 表示失败。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("错误: 需要安装 playwright，请运行: pip install playwright", file=sys.stderr)
        return False

    # 检查是否有图形显示环境
    import os
    if not os.environ.get("DISPLAY"):
        # SSH/tmux 会话不会继承 WSLg 的 DISPLAY，但 X11 socket 可用
        if Path("/tmp/.X11-unix/X0").exists():
            os.environ["DISPLAY"] = ":0"
        else:
            print("错误: 当前环境没有图形界面 (DISPLAY 未设置)，无法打开浏览器。", file=sys.stderr)
            return False

    # 使用持久化浏览器上下文，保存登录状态
    browser_data_dir = CONFIG_DIR / "browser-data"
    browser_data_dir.mkdir(parents=True, exist_ok=True)

    print("正在打开浏览器...", file=sys.stderr)
    print("请在浏览器中登录小米账号，登录完成后程序将自动获取 Cookie。", file=sys.stderr)

    try:
        with sync_playwright() as p:
            # 使用持久化上下文，保存登录状态到本地目录
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(browser_data_dir),
                headless=False,
            )
            page = context.new_page()

            # 访问 platform.xiaomimimo.com
            page.goto("https://platform.xiaomimimo.com/console/plan-manage")

            # 等待用户登录（最多 5 分钟）
            # 循环检查是否已登录（检测 api-platform_serviceToken cookie）
            print("等待登录中... (最多 5 分钟)", file=sys.stderr)
            for i in range(300):  # 300秒 = 5分钟
                time.sleep(1)
                cookies = context.cookies()
                has_token = any(c["name"] == "api-platform_serviceToken" for c in cookies)
                if has_token:
                    break
            else:
                print("错误: 登录超时", file=sys.stderr)
                context.close()
                return False

            # 获取所有 Cookie
            cookies = context.cookies()

            context.close()
    except Exception as e:
        print(f"错误: 浏览器启动失败 - {e}", file=sys.stderr)
        return False

    # 提取需要的 Cookie
    cookie_parts = []
    for cookie in cookies:
        if cookie["name"] in [
            "serviceToken",
            "xiaomichatbot_ph",
            "api-platform_serviceToken",
            "userId",
            "api-platform_slh",
            "api-platform_ph",
        ]:
            # 确保值不包含多余的引号
            value = cookie["value"].strip('"')
            cookie_parts.append(f'{cookie["name"]}="{value}"')

    if not cookie_parts:
        print("错误: 未获取到有效的 Cookie", file=sys.stderr)
        return False

    cookie_str = "; ".join(cookie_parts)
    update_cookie(cookie_str)
    print("登录成功！")
    return True


def load_cache() -> dict | None:
    """加载缓存，超过 TTL 则返回 None。

    返回格式:
    - 正常数据: {"detail": ..., "usage": ..., ...}
    - 错误缓存: {"error": "错误信息"}
    - 无缓存: None
    """
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        if time.time() - cache.get("timestamp", 0) > CACHE_TTL:
            return None
        # 如果缓存包含错误信息，返回错误
        if "error" in cache:
            return {"error": cache["error"]}
        return cache.get("data")
    except (json.JSONDecodeError, KeyError):
        return None


def save_cache(data: dict) -> None:
    """保存数据到缓存。

    data 中包含 "error" 键时，将错误信息保存在顶层，否则作为正常数据保存。
    """
    CACHE_DIR = CACHE_FILE.parent
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if "error" in data:
        cache_entry = {"timestamp": time.time(), "error": data["error"]}
    else:
        cache_entry = {"timestamp": time.time(), "data": data}
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_entry, f, ensure_ascii=False)


def load_history() -> dict:
    """加载定稿历史文件。

    结构: {"daily": {"YYYY-MM-DD": {model: {field: value}}},
           "monthly": {"YYYY-MM": {model: {field: value}}}}
    """
    if not HISTORY_FILE.exists():
        return {"daily": {}, "monthly": {}}
    try:
        with open(HISTORY_FILE) as f:
            h = json.load(f)
        h.setdefault("daily", {})
        h.setdefault("monthly", {})
        return h
    except (json.JSONDecodeError, KeyError):
        return {"daily": {}, "monthly": {}}


def save_history(history: dict) -> None:
    """保存历史并裁剪超出保留期的条目。"""
    cutoff_day = (datetime.now(timezone(timedelta(hours=8))).date()
                  - timedelta(days=HISTORY_KEEP_DAYS)).strftime("%Y-%m-%d")
    cutoff_month = (datetime.now(timezone(timedelta(hours=8))).date()
                    - timedelta(days=HISTORY_KEEP_MONTHS * 31)).strftime("%Y-%m")
    history["daily"] = {k: v for k, v in sorted(history["daily"].items())
                        if k >= cutoff_day}
    history["monthly"] = {k: v for k, v in sorted(history["monthly"].items())
                          if k >= cutoff_month}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False)


def settled_cutoff() -> tuple[date, str]:
    """返回 (已定稿的最新日期, 最新定稿月份 "YYYY-MM")。

    官网将每天 8:00 前的消耗计入前一天，故 8:30 后前一天数据才不再变化；
    跨月同理，次月 1 日 8:30 后上月数据才定稿。
    """
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    settled_date = cn_now.date() - timedelta(days=2 if (cn_now.hour, cn_now.minute) < (8, 30) else 1)
    if (cn_now.day, cn_now.hour, cn_now.minute) < (1, 8, 30):
        # 月初1日8:30前，上上月才定稿
        first_of_prev = (cn_now.date().replace(day=1) - timedelta(days=1))
        settled_month = (first_of_prev.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    else:
        prev = cn_now.date().replace(day=1) - timedelta(days=1)
        settled_month = prev.strftime("%Y-%m")
    return settled_date, settled_month


def merge_models(target: dict, record: dict) -> None:
    """把一条 API 记录按模型累加进 target: {model: {field: value}}。"""
    model = record.get("model", "")
    t = target.setdefault(model, {})
    for field in ("inputHitToken", "inputMissToken", "outputToken", "requestCount"):
        t[field] = t.get(field, 0) + record.get(field, 0)


def flatten_day(models: dict, date_str: str) -> dict:
    """把 {model: tokens} 摊平为一条显示记录（credits 按模型费率分别换算）。"""
    total = {"date": date_str, "model": "", "credits": 0.0,
             "inputHitToken": 0, "inputMissToken": 0, "outputToken": 0, "requestCount": 0}
    for model, t in models.items():
        total["credits"] += convert_to_credits({"model": model, **t})
        for field in ("inputHitToken", "inputMissToken", "outputToken", "requestCount"):
            total[field] += t.get(field, 0)
    return total


def api_get(config: dict, path: str) -> dict:
    """发送 GET 请求到 MiMo API。"""
    url = f"{config['base_url']}/api/v1{path}"
    headers = {
        "Cookie": config["cookie"],
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": config["base_url"] + "/",
        "Origin": config["base_url"],
    }
    resp = requests.get(url, headers=headers, timeout=10)
    # 检查 HTTP 状态码是否为 401
    if resp.status_code == 401:
        raise AuthError("认证失败，请更新 cookie。")
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") == 401:
        raise AuthError("认证失败，请更新 cookie。")
    return data


def get_plan_detail(config: dict) -> dict:
    """获取套餐详情。"""
    return api_get(config, "/tokenPlan/detail")


def get_plan_usage(config: dict) -> dict:
    """获取使用量。"""
    return api_get(config, "/tokenPlan/usage")


def get_balance(config: dict) -> dict:
    """获取余额。"""
    return api_get(config, "/balance")


def get_ph_from_cookie(cookie: str) -> str:
    """从 cookie 中提取 api-platform_ph 值。"""
    for item in cookie.split(";"):
        if "api-platform_ph" in item:
            return item.strip().split("=", 1)[1].strip('"')
    return ""


def api_post(config: dict, path: str, data: dict) -> dict:
    """发送 POST 请求到 MiMo API。"""
    ph = get_ph_from_cookie(config["cookie"])
    url = f"{config['base_url']}/api/v1{path}"
    if ph:
        # 对 ph 值进行 URL 编码，特别是 + 号需要编码为 %2B
        url += f"?api-platform_ph={quote(ph, safe='')}"
    headers = {
        "Cookie": config["cookie"],
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Referer": config["base_url"] + "/",
        "Origin": config["base_url"],
    }
    resp = requests.post(url, headers=headers, json=data, timeout=10)
    # 检查 HTTP 状态码是否为 401
    if resp.status_code == 401:
        raise AuthError("认证失败，请更新 cookie。")
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") == 401:
        raise AuthError("认证失败，请更新 cookie。")
    return result


def get_daily_usage(config: dict, year: int, month: int) -> list[dict]:
    """获取指定月份的每日使用明细。"""
    result = api_post(config, "/usage/token-plan/list", {"year": year, "month": month})
    return result.get("data", [])


def get_monthly_usage(config: dict, months: int = 2, history: dict | None = None) -> list[dict]:
    """获取最近 N 个月的月度使用数据。

    已定稿月份只从历史读取，历史缺失才查 API 并回写历史。
    处理跨年情况：如当前为1月，则还需查询上一年12月。
    """
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    current_year = cn_now.year
    current_month = cn_now.month
    _, settled_month = settled_cutoff()

    months_to_query = []
    for i in range(months):
        y = current_year
        m = current_month - i
        if m <= 0:
            m += 12
            y -= 1
        months_to_query.append((y, m))

    if history is None:
        history = {"daily": {}, "monthly": {}}

    # 需要查 API 的月份：未定稿，或已定稿但历史缺失
    months_to_fetch = [
        (y, m) for y, m in months_to_query
        if f"{y}-{m:02d}" > settled_month or f"{y}-{m:02d}" not in history["monthly"]
    ]

    year_data: dict[tuple[int, int], dict] = {}
    if months_to_fetch:
        # 按年去重查询（同一年只需查一次）
        for y in {y for y, _ in months_to_fetch}:
            result = api_post(config, "/usage/token-plan/list", {"year": y})
            for r in result.get("data", []):
                month_raw = r.get("month") or r.get("date", "")
                # month_raw 可能是 "2025-01"、"2025-01-01" 或 "01"
                if not isinstance(month_raw, str) or not month_raw:
                    continue
                if len(month_raw) >= 7:
                    month_num = int(month_raw[5:7])
                elif len(month_raw) == 2 and month_raw.isdigit():
                    month_num = int(month_raw)
                else:
                    continue
                merge_models(year_data.setdefault((y, month_num), {}), r)

        # 已定稿的月份写入历史
        hist_changed = False
        for (y, m), models in year_data.items():
            key = f"{y}-{m:02d}"
            if key <= settled_month and history["monthly"].get(key) != models:
                history["monthly"][key] = models
                hist_changed = True
        if hist_changed:
            save_history(history)

    # 按最近 N 个月的顺序组装结果
    monthly = []
    for y, m in months_to_query:
        key = f"{y}-{m:02d}"
        if key <= settled_month:
            models = history["monthly"].get(key, {})
        else:
            models = year_data.get((y, m), {})
        total = {"credits": 0.0, "inputHitToken": 0, "inputMissToken": 0,
                 "outputToken": 0, "requestCount": 0}
        for model, t in models.items():
            total["credits"] += convert_to_credits({"model": model, **t})
            for field in ("inputHitToken", "inputMissToken", "outputToken", "requestCount"):
                total[field] += t.get(field, 0)
        monthly.append({"year": y, "month": m, **total})

    return monthly


# Token 到 Credit 转换率：{model: (命中缓存, 未命中缓存, 输出)}
TOKEN_TO_CREDIT = {
    "mimo-v2.6-pro": (2.5, 300, 600),
    "mimo-v2.6-flash": (2, 100, 200),
    "mimo-v2.6-pro-ultraspeed": (25, 3000, 6000),
    "mimo-v2.5-pro": (2.5, 300, 600),
    "mimo-v2.5": (2, 100, 200),
}


def convert_to_credits(record: dict) -> float:
    """将 Token 使用量转换为 Credit。"""
    model = record.get("model", "")
    rates = TOKEN_TO_CREDIT.get(model, (2, 100, 200))  # 默认使用 mimo-v2.5 费率

    hit = record.get("inputHitToken", 0)
    miss = record.get("inputMissToken", 0)
    output = record.get("outputToken", 0)

    return hit * rates[0] + miss * rates[1] + output * rates[2]


def get_recent_days_usage(config: dict, days: int = 3, history: dict | None = None) -> list[dict]:
    """获取最近有有效数据的 N 条使用记录（已转换为 Credit）。

    已定稿日期只从历史读取，历史缺失才查 API 并回写历史。
    MiMo 按北京时间 (UTC+8) 划分每日数据，
    API 返回的 date 字段已按北京时间校对。
    """
    # 使用北京时间计算日期，与 MiMo 平台保持一致
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    mimo_date = cn_now.date()

    # 扩大查询范围以找到有效数据（查询最近 30 天）
    search_range = 30
    dates = [mimo_date - timedelta(days=i) for i in range(search_range)]
    date_set = {d.strftime("%Y-%m-%d") for d in dates}
    settled_date, _ = settled_cutoff()

    if history is None:
        history = {"daily": {}, "monthly": {}}

    # 需要查 API 的月份：含未定稿日期，或有已定稿但历史缺失的日期
    months_needed = {
        (d.year, d.month) for d in dates
        if d > settled_date or d.strftime("%Y-%m-%d") not in history["daily"]
    }

    # 按月查询并合并（key 为 date，value 为 {model: tokens}）
    day_models: dict[str, dict] = {}
    for year, month in months_needed:
        for r in get_daily_usage(config, year, month):
            ds = r.get("date")
            if ds in date_set:
                merge_models(day_models.setdefault(ds, {}), r)

    # 已定稿日期写入历史
    hist_changed = False
    for ds, models in day_models.items():
        if ds <= settled_date.strftime("%Y-%m-%d") and history["daily"].get(ds) != models:
            history["daily"][ds] = models
            hist_changed = True
    if hist_changed:
        save_history(history)

    # 汇总为显示记录（保留有 token 消耗的日期），按日期降序取最近 N 条
    all_usage = []
    for ds in sorted(date_set, reverse=True):
        models = (history["daily"].get(ds) if ds <= settled_date.strftime("%Y-%m-%d")
                  else day_models.get(ds)) or {}
        if not models:
            continue
        rec = flatten_day(models, ds)
        if rec["inputHitToken"] > 0 or rec["inputMissToken"] > 0 or rec["outputToken"] > 0:
            all_usage.append(rec)
    return all_usage[:days]


def format_tokens(tokens: int | float, precision: int = 2) -> str:
    """格式化 token/credit 数量为人类可读格式。"""
    fmt = f".{precision}f"
    if abs(tokens) >= 1_000_000_000:
        return f"{tokens / 1_000_000_000:{fmt}}B"
    if abs(tokens) >= 1_000_000:
        return f"{tokens / 1_000_000:{fmt}}M"
    if abs(tokens) >= 1_000:
        return f"{tokens / 1_000:{fmt}}K"
    if isinstance(tokens, float):
        return f"{tokens:{fmt}}"
    return str(int(tokens))


def format_amount(credits: float, price_per_credit: float) -> str:
    """将 credits 按套餐单价转换为金额格式 ¥x.xx。"""
    return f"¥{credits * price_per_credit:.2f}"


def _format_value(credits: float, month_limit: float, ppc: float, fmt_mode: str, prec: int) -> str:
    """根据显示模式格式化单条消耗数据。"""
    if fmt_mode == "amount":
        return format_amount(credits, ppc)
    if fmt_mode == "percent":
        pct = (credits / month_limit * 100) if month_limit > 0 else 0
        return f"{pct:.{prec}f}%"
    return format_tokens(credits, prec)


def _format_all(credits: float, month_limit: float, ppc: float, prec: int) -> str:
    """同时输出 token、percent、amount 三种格式，用逗号分隔。"""
    pct = (credits / month_limit * 100) if month_limit > 0 else 0
    return f"{format_tokens(credits, prec)}, {pct:.{prec}f}%, {format_amount(credits, ppc)}"


def _table(header: list[str], rows: list[list[str]], widths: list[int] | None = None,
           hcolor: str = "", gray: str = "", gray_cols: tuple = (),
           highlights: dict[tuple[int, int], str] | None = None) -> list[str]:
    """构造右对齐表格行；widths 统一列宽，hcolor 表头色，gray_cols 指定灰色列，
    highlights 以 (行号, 列号) 覆盖单元格颜色，分隔线灰色。"""
    w = widths or [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]
    rs = "\033[0m" if gray else ""	# 灰色非空即彩色模式
    sep = f"{gray}|{rs}" if gray else "|"

    def line(row: list[str], color: str = "", ridx: int = -1) -> str:
        cells = []
        for i, c in enumerate(row):
            cc = (highlights or {}).get((ridx, i)) or (gray if not color and i in gray_cols else color)
            pad = c.rjust(w[i])
            cells.append(f"{cc}{pad}{rs}" if cc else pad)
        return sep.join(cells)

    return [line(header, hcolor)] + [line(r, ridx=n) for n, r in enumerate(rows)]


def _usage_row(r: dict, credit_limit: float, ppc: float, prec: int, label: str) -> list[str]:
    """表格单行：label, Cost, Percent, Total, Hit, Miss, MisRate, Out。"""
    pct = (r["credits"] / credit_limit * 100) if credit_limit > 0 else 0
    hit, miss = r["inputHitToken"], r["inputMissToken"]
    misrate = f"{miss / hit * 100:.{prec}f}%" if hit else "0.000%"
    return [
        label,
        f"{r['credits'] * ppc:.2f}",
        f"{pct:.{prec}f}%",
        format_tokens(r["credits"], prec),
        format_tokens(hit),
        format_tokens(miss),
        misrate,
        format_tokens(r["outputToken"]),
    ]


def format_output(config: dict, detail: dict, usage: dict, recent: list[dict] | None = None, balance: dict | None = None, monthly: list[dict] | None = None, color: bool = False) -> str:
    """格式化多行输出（同时展示 token、percent、amount 三种格式）。"""
    disp = config.get("display", DEFAULT_CONFIG["display"])
    prec = disp.get("precision", 3)

    plan = detail.get("data", {})
    usage_data = usage.get("data", {})

    plan_name = plan.get("planName", "")
    plan_code = plan.get("planCode", "")

    plan_pricing = PLAN_PRICING.get(plan_code, (0, 0))
    ppc = plan_pricing[0] / plan_pricing[1] if plan_pricing[1] > 0 else 0

    balance_amount = 0.0
    if balance:
        balance_amount = float(balance.get("data", {}).get("balance", "0"))

    lines = []

    gray = green = cyan = magenta = red = yellow = bold = reset = ""
    if color:
        gray = "\033[1;90m"
        green = "\033[1;32m"
        cyan = "\033[1;36m"
        magenta = "\033[1;35m"
        red = "\033[1;31m"
        yellow = "\033[1;33m"
        bold = "\033[1m"
        reset = "\033[0m"
    if balance_amount > 0:
        lines.append(f"{green}Balance{reset}: ¥{balance_amount:.2f}")
    if not plan_code:
        lines.append(f"{green}Token Plan{reset}: None")
    else:
        end_date = plan.get("currentPeriodEnd", "")[:10]
        usage_info = usage_data.get("usage", {})
        usage_items = usage_info.get("items", [])
        plan_item = next((i for i in usage_items if i["name"] == "plan_total_token"), None)
        credit_used = plan_item["used"] if plan_item else 0
        credit_limit = plan_item["limit"] if plan_item else 0

        period = "年度" if plan_code.endswith(":year") else "月度"
        lines.append(f"{green}Token Plan{reset}: {gray}{plan_name} {period}套餐, ¥{plan_pricing[0]:.2f}{reset}, {bold}有效期至{reset}: {gray}{end_date}{reset}")
        pct = (credit_used / credit_limit * 100) if credit_limit > 0 else 0
        lines.append(f"{green}Credits usage{reset}:"
               + f" {gray}{format_tokens(credit_used, prec)} / {format_tokens(credit_limit, prec)},{reset}"
               + f" 已使用 {red}{pct:.{prec}f}%{reset}, {format_amount(credit_used, ppc)}")

        # 最近 N 天/月消耗，两表统一列宽；仅 Daily 做异常高亮
        header = ["Daily", "Cost", "Percent", "Total", "Hit", "Miss", "MisRate", "Out"]
        tables = []
        if recent:
            rows = [_usage_row(r, credit_limit, ppc, prec, r["date"][4:].replace("-", "")) for r in recent]
            hl = {}
            for n, r in enumerate(rows):
                cost, mis = float(r[1]), float(r[6].rstrip("%"))
                if mis > 10:
                    hl[(n, 6)] = red
                elif mis > 5:
                    hl[(n, 6)] = yellow
                if cost > 20:
                    hl[(n, 1)] = red
                elif cost > 10:
                    hl[(n, 1)] = yellow
            tables.append((cyan, header, rows, hl))
        if monthly:
            rows = [_usage_row(r, credit_limit, ppc, prec, f"{r['year'] % 100:02d}{r['month']:02d}") for r in monthly]
            tables.append((magenta, ["Monthly", *header[1:]], rows, {}))
        if tables:
            w = [0] * 8
            for _, header, rows, _ in tables:
                for row in [header] + rows:
                    for i, cell in enumerate(row):
                        w[i] = max(w[i], len(cell))
            for color, header, rows, hl in tables:
                # 日期/Total/Hit/Miss/Out 灰，Cost/Percent/MisRate 原色，异常值黄/红
                lines.extend(_table(header, rows, w, color, gray, (0, 3, 4, 5, 7), hl))

    return "\n".join(lines)


import re as _re

_ANSI_RE = _re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义码。"""
    return _ANSI_RE.sub("", text)


def _colorize(text: str, color_code: str) -> str:
    """给文本添加 ANSI 颜色。"""
    return f"\033[{color_code}m{text}\033[0m"


def _tmux_colorize(text: str, fg: str, bold: bool = False) -> str:
    """给文本添加 tmux 状态栏颜色。"""
    attr = "bold," if bold else ""
    return f"#[{attr}fg={fg}]{text}#[fg=default,nobold]"


def format_tmux(config: dict, detail: dict, usage: dict, recent: list[dict] | None = None, balance: dict | None = None, monthly: list[dict] | None = None, color: bool = False) -> str:
    """格式化输出为 tmux 状态栏单行格式。"""
    disp = config.get("display", DEFAULT_CONFIG["display"])
    fmt_mode = disp.get("value_format", "token")
    prec = disp.get("precision", 3)

    plan = detail.get("data", {})
    usage_data = usage.get("data", {})

    plan_code = plan.get("planCode", "")

    parts = [ICON_MIMO]

    # 余额
    balance_amount = 0.0
    if balance:
        balance_amount = float(balance.get("data", {}).get("balance", "0"))
    gray = "#808080"
    if balance_amount > 0:
        if color:
            key = _tmux_colorize("Bal", "#5fff00", bold=True)
            parts.append(f"{key}#[fg={gray}]#[nobold]:¥{balance_amount:.2f}#[fg=default]")
        else:
            parts.append(f"Bal:¥{balance_amount:.2f}")

    if not plan_code:
        parts.append("Crt:-")
        return "[" + " ".join(parts) + "]"

    # 套餐单价（amount 模式需要）
    plan_pricing = PLAN_PRICING.get(plan_code, (0, 0))
    ppc = plan_pricing[0] / plan_pricing[1] if plan_pricing[1] > 0 else 0

    # 套餐使用量
    usage_info = usage_data.get("usage", {})
    usage_items = usage_info.get("items", [])
    plan_item = next((i for i in usage_items if i["name"] == "plan_total_token"), None)
    month_used = plan_item["used"] if plan_item else 0
    month_limit = plan_item["limit"] if plan_item else 0
    month_percent = (month_used / month_limit * 100) if month_limit > 0 else 0

    if color:
        fg = "#ff0000" if month_percent > 90 else ("#ffff00" if month_percent > 80 else "#5fff00")
        key = _tmux_colorize("Crt", fg, bold=True)
        parts.append(f"{key}#[fg={gray}]#[nobold]:{month_percent:.3f}%#[fg=default]")
    else:
        parts.append(f"Crt:{month_percent:.3f}%")

    # 最近 N 天每日消耗（按 config 截取，完整列表供多行输出固定 30 天）
    if recent:
        rec_parts = []
        for r in recent[:disp.get("recent_days", 3)]:
            date_short = r["date"][8:].replace("-", "")     # DD
            val = _format_value(r["credits"], month_limit, ppc, fmt_mode, prec)
            if color:
                rec_parts.append(f"#[fg=#00ffff]{date_short}#[fg={gray}]:#[fg=default]{val}")
            else:
                rec_parts.append(f"{date_short}:{val}")
        if color:
            key = _tmux_colorize("Dai", "#00ffff", bold=True)
            inner = f"#[fg=default] ".join(rec_parts)
            parts.append(f"{key}#[fg={gray}]#[nobold][{inner}#[fg={gray}]#[nobold]]#[fg=default]")
        else:
            parts.append("Dai[" + " ".join(rec_parts) + "]")

    # 最近 N 个月月度消耗（按 config 截取，完整列表供多行输出固定 6 个月）
    if monthly:
        mon_parts = []
        for r in monthly[:disp.get("recent_months", 2)]:
            month_label = f"{r['month']:02d}"
            val = _format_value(r["credits"], month_limit, ppc, fmt_mode, prec)
            if color:
                mon_parts.append(f"#[fg=#ff8fff]{month_label}#[fg={gray}]:#[fg=default]{val}")
            else:
                mon_parts.append(f"{month_label}:{val}")
        if color:
            key = _tmux_colorize("Mon", "#ff8fff", bold=True)
            inner = f"#[fg=default] ".join(mon_parts)
            parts.append(f"{key}#[fg={gray}]#[nobold][{inner}#[fg={gray}]#[nobold]]#[fg=default]")
        else:
            parts.append("Mon[" + " ".join(mon_parts) + "]")

    return " ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="MiMo 平台 token 使用量查询工具")
    parser.add_argument("-t", "--tmux", action="store_true", help="输出适合 tmux 状态栏的单行格式")
    parser.add_argument("-C", "--cookie", help="更新配置文件中的 cookie 值")
    parser.add_argument("-c", "--color", action="store_true", help="启用高亮输出（ANSI 颜色）")
    parser.add_argument("-l", "--login", action="store_true", help="通过浏览器登录获取 cookie")
    args = parser.parse_args()

    # 如果提供了 login 参数，通过浏览器登录获取 cookie
    if args.login:
        login_with_browser()
        return

    # 如果提供了 cookie 参数，更新配置文件并退出
    if args.cookie:
        update_cookie(args.cookie)
        return

    config = load_config()

    # 如果 cookie 为空，自动打开浏览器获取
    if not config.get("cookie"):
        print("Cookie 未配置，正在打开浏览器获取...", file=sys.stderr)
        if not login_with_browser():
            save_cache({"error": "登录失败，请手动获取 Cookie 后运行: mimo-stat -C \"<cookie>\""})
            if args.tmux:
                print(f"{ICON_MIMO}login failed")
            else:
                print("登录失败，请手动获取 Cookie 后运行: mimo-stat -C \"<cookie>\"", file=sys.stderr)
            sys.exit(1)
        config = load_config()

    # 尝试从缓存读取
    cached = load_cache()
    if cached:
        # 如果缓存包含错误信息，自动重新登录
        if "error" in cached:
            print("Cookie 已过期，正在重新登录...", file=sys.stderr)
            if not login_with_browser():
                # 登录失败，缓存错误信息
                save_cache({"error": "登录失败，请手动获取 Cookie 后运行: mimo-stat -C \"<cookie>\""})
                if args.tmux:
                    print(f"{ICON_MIMO}login failed")
                else:
                    print("登录失败，请手动获取 Cookie 后运行: mimo-stat -C \"<cookie>\"", file=sys.stderr)
                sys.exit(1)
            # 重新加载配置
            config = load_config()
            # 清除缓存
            save_cache({"error": "clear"})
        else:
            fmt = format_tmux if args.tmux else format_output
            print(fmt(config, cached["detail"], cached["usage"], cached.get("recent"), cached.get("balance"), cached.get("monthly"), color=args.color))
            return

    # 缓存未命中，请求 API（多行输出固定 30 天/6 个月，tmux 单行由 format_tmux 按 config 截取）
    history = load_history()
    try:
        detail = get_plan_detail(config)
        usage = get_plan_usage(config)
        recent = get_recent_days_usage(config, days=30, history=history)
        monthly = get_monthly_usage(config, months=6, history=history)
        balance = get_balance(config)
        save_cache({"detail": detail, "usage": usage, "recent": recent, "balance": balance, "monthly": monthly})
        fmt = format_tmux if args.tmux else format_output
        print(fmt(config, detail, usage, recent, balance, monthly, color=args.color))
    except AuthError as e:
        # 认证失败，自动重新登录
        print("Cookie 已过期，正在重新登录...", file=sys.stderr)
        if not login_with_browser():
            # 登录失败，缓存错误信息
            save_cache({"error": "登录失败，请手动获取 Cookie 后运行: mimo-stat -C \"<cookie>\""})
            if args.tmux:
                print(f"{ICON_MIMO}login failed")
            else:
                print("登录失败，请手动获取 Cookie 后运行: mimo-stat -C \"<cookie>\"", file=sys.stderr)
            sys.exit(1)
        # 重新加载配置并重试
        config = load_config()
        try:
            detail = get_plan_detail(config)
            usage = get_plan_usage(config)
            recent = get_recent_days_usage(config, days=30, history=history)
            monthly = get_monthly_usage(config, months=6, history=history)
            balance = get_balance(config)
            save_cache({"detail": detail, "usage": usage, "recent": recent, "balance": balance, "monthly": monthly})
            fmt = format_tmux if args.tmux else format_output
            print(fmt(config, detail, usage, recent, balance, monthly, color=args.color))
        except Exception as e2:
            # 重试失败，缓存错误信息
            save_cache({"error": f"重新登录后仍然失败: {e2}"})
            if args.tmux:
                print(f"{ICON_MIMO}login failed")
            else:
                print(f"重新登录后仍然失败: {e2}", file=sys.stderr)
            sys.exit(1)
    except requests.HTTPError as e:
        if args.tmux:
            print(f"{ICON_MIMO}response {e.response.status_code}")
        else:
            print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        if args.tmux:
            print(f"{ICON_MIMO}request error")
        else:
            print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
