#!/usr/bin/env python3
"""MiMo 平台 token 使用量查询工具，用于 tmux 状态栏等场景。

API 端点:
- GET  /api/v1/tokenPlan/detail           — 套餐详情
- GET  /api/v1/tokenPlan/usage            — 月度使用量
- POST /api/v1/usage/token-plan/list      — 每日使用明细

认证方式: 小米账号 Cookie，从浏览器登录后获取。
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
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
CACHE_TTL = 30  # MiMo 缓存有效期（秒）

# 图标定义
ICON_MIMO = "🍚"  # MiMo 平台标识

DEFAULT_CONFIG = {
    "base_url": "https://platform.xiaomimimo.com",
    "cookie": "",
    "display": {
        "recent_days": 3,
        "recent_months": 2,
        "value_format": "token",   # token | percent | amount
        "precision": 3,            # 小数点位数（amount 模式固定 2 位）
    },
}

# 套餐价格映射: plan_code -> (月价¥, 月度Credits)
# plan_code 来自 get_plan_detail 返回的 data.planCode
PLAN_PRICING = {
    "lite:month":     (39,    4_100_000_000),
    "standard:month": (99,   11_000_000_000),
    "pro:month":      (329,  38_000_000_000),
    "max:month":      (659,  82_000_000_000),
    "lite:year":      (411.84,  49_200_000_000),
    "standard:year":  (1045.44, 132_000_000_000),
    "pro:year":       (3474.24, 456_000_000_000),
    "max:year":       (6959.04, 984_000_000_000),
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


def get_monthly_usage(config: dict, months: int = 2) -> list[dict]:
    """获取最近 N 个月的月度使用数据。

    不传 month 参数时 API 返回月度汇总数据。
    处理跨年情况：如当前为1月，则还需查询上一年12月。
    """
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    current_year = cn_now.year
    current_month = cn_now.month

    months_to_query = []
    for i in range(months):
        y = current_year
        m = current_month - i
        if m <= 0:
            m += 12
            y -= 1
        months_to_query.append((y, m))

    # 按年去重查询（同一年只需查一次）
    years_needed = {y for y, _ in months_to_query}
    year_data = {}
    for y in years_needed:
        result = api_post(config, "/usage/token-plan/list", {"year": y})
        records = result.get("data", [])
        # 按月份分组聚合（先按记录级别算 credits，再累加）
        for r in records:
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
            year_data[(y, month_num)] = year_data.get((y, month_num), {})
            for field in ("inputHitToken", "inputMissToken", "outputToken", "requestCount"):
                year_data[(y, month_num)][field] = year_data[(y, month_num)].get(field, 0) + r.get(field, 0)
            # credits 按模型分别计算后累加（不同模型费率不同）
            year_data[(y, month_num)]["credits"] = year_data[(y, month_num)].get("credits", 0) + convert_to_credits(r)

    # 按最近2个月的顺序组装结果
    monthly = []
    for y, m in months_to_query:
        key = (y, m)
        if key in year_data:
            d = year_data[key]
            monthly.append({
                "year": y,
                "month": m,
                "credits": d.get("credits", 0),
                "inputHitToken": d.get("inputHitToken", 0),
                "inputMissToken": d.get("inputMissToken", 0),
                "outputToken": d.get("outputToken", 0),
                "requestCount": d.get("requestCount", 0),
            })
        else:
            monthly.append({
                "year": y,
                "month": m,
                "credits": 0,
                "inputHitToken": 0,
                "inputMissToken": 0,
                "outputToken": 0,
                "requestCount": 0,
            })

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


def get_recent_days_usage(config: dict, days: int = 3) -> list[dict]:
    """获取最近有有效数据的 N 条使用记录（已转换为 Credit）。

    MiMo 按北京时间 (UTC+8) 划分每日数据，
    API 返回的 date 字段已按北京时间校对。
    """
    # 使用北京时间计算日期，与 MiMo 平台保持一致
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    mimo_date = cn_now.date()

    # 扩大查询范围以找到有效数据（查询最近 30 天）
    search_range = 30
    dates = [mimo_date - timedelta(days=i) for i in range(search_range)]
    months_needed = {(d.year, d.month) for d in dates}

    # 按月查询并合并
    all_records = []
    for year, month in months_needed:
        all_records.extend(get_daily_usage(config, year, month))

    # 转换为 Credit 并筛选有效数据（至少有一个 token 使用量 > 0）
    date_set = {d.strftime("%Y-%m-%d") for d in dates}
    all_usage = []
    for r in all_records:
        if r.get("date") in date_set:
            hit = r.get("inputHitToken", 0)
            miss = r.get("inputMissToken", 0)
            output = r.get("outputToken", 0)
            # 只保留有实际使用的记录
            if hit > 0 or miss > 0 or output > 0:
                all_usage.append({
                    "date": r["date"],
                    "model": r.get("model", ""),
                    "credits": convert_to_credits(r),
                    "inputHitToken": hit,
                    "inputMissToken": miss,
                    "outputToken": output,
                    "requestCount": r.get("requestCount", 0),
                })

    # 按日期降序排列，返回最近 N 条
    all_usage.sort(key=lambda r: r["date"], reverse=True)
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

    gray = "\033[90m"
    reset = "\033[0m"
    if balance_amount > 0:
        if color:
            lines.append(f"\033[1;32mBalance\033[0m{gray}:¥{balance_amount:.2f}{reset}")
        else:
            lines.append(f"Balance: ¥{balance_amount:.2f}")
    if not plan_code:
        lines.append("Token Plan: None")
    else:
        end_date = plan.get("currentPeriodEnd", "")[:10].replace("-", "")[2:]

        usage_info = usage_data.get("usage", {})
        usage_items = usage_info.get("items", [])
        plan_item = next((i for i in usage_items if i["name"] == "plan_total_token"), None)
        month_used = plan_item["used"] if plan_item else 0
        month_limit = plan_item["limit"] if plan_item else 0

        lines.append(f"Token Plan: MiMo {plan_name}, exp:{end_date}")
        lines.append(f"Credits usage: {_format_all(month_used, month_limit, ppc, prec)} / {_format_all(month_limit, month_limit, ppc, prec)}")

        # 最近 N 天每日消耗
        if recent:
            lines.append(f"\033[1;36mRecent usage:\033[0m" if color else "Recent usage:")
            for r in recent:
                date_short = r["date"][2:].replace("-", "")
                val = _format_all(r["credits"], month_limit, ppc, prec)
                lines.append(
                    f"  - {date_short}: {val}, "
                    f"hit:{format_tokens(r['inputHitToken'])}, mis:{format_tokens(r['inputMissToken'])}, out:{format_tokens(r['outputToken'])}"
                )

        # 最近 N 个月月度消耗
        if monthly:
            lines.append(f"\033[1;35mMonthly usage:\033[0m" if color else "Monthly usage:")
            for r in monthly:
                month_label = f"{r['year'] % 100:02d}{r['month']:02d}"
                val = _format_all(r["credits"], month_limit, ppc, prec)
                lines.append(
                    f"  - {month_label}: {val}, "
                    f"hit:{format_tokens(r['inputHitToken'])}, mis:{format_tokens(r['inputMissToken'])}, out:{format_tokens(r['outputToken'])}"
                )

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

    # 最近 N 天每日消耗
    if recent:
        rec_parts = []
        for r in recent:
            date_short = r["date"][8:].replace("-", "")  # DD
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

    # 最近 N 个月月度消耗
    if monthly:
        mon_parts = []
        for r in monthly:
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

    # 缓存未命中，请求 API
    disp = config.get("display", DEFAULT_CONFIG["display"])
    recent_days = disp.get("recent_days", 3)
    recent_months = disp.get("recent_months", 2)
    try:
        detail = get_plan_detail(config)
        usage = get_plan_usage(config)
        recent = get_recent_days_usage(config, days=recent_days)
        monthly = get_monthly_usage(config, months=recent_months)
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
            recent = get_recent_days_usage(config, days=recent_days)
            monthly = get_monthly_usage(config, months=recent_months)
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
