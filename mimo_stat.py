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


class AuthError(Exception):
    """认证失败异常（401）。"""
    pass


CONFIG_DIR = Path.home() / ".config" / "mimo-stat"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"
CLAUDE_STATS_FILE = Path.home() / ".claude" / "stats-cache.json"
CLAUDE_CACHE_FILE = CONFIG_DIR / "claude-cache.json"
CACHE_TTL = 30  # MiMo 缓存有效期（秒）
CLAUDE_CACHE_TTL = 30 * 60  # Claude 缓存有效期（30分钟）

DEFAULT_CONFIG = {
    "base_url": "https://platform.xiaomimimo.com",
    "cookie": "",
}


def load_config() -> dict:
    """加载配置文件，不存在则创建默认配置。"""
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))
        print(f"已创建默认配置: {CONFIG_FILE}", file=sys.stderr)
        print("请编辑配置文件填入 cookie 后重试。", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    # 合并默认值
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    return config


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


# Token 到 Credit 转换率：{model: (命中缓存, 未命中缓存, 输出)}
TOKEN_TO_CREDIT = {
    "mimo-v2.5-pro": (2.5, 300, 600),
    "mimo-v2.5": (2, 100, 200),
    "mimo-v2-pro": (2.5, 300, 600),
    "mimo-v2-omni": (2, 100, 200),
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


def format_tokens(tokens: int) -> str:
    """格式化 token 数量为人类可读格式。"""
    if tokens >= 1_000_000_000:
        return f"{tokens / 1_000_000_000:.2f}B"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    return str(tokens)


def load_claude_cache() -> list[dict] | None:
    """加载 Claude 统计缓存，超过 TTL 则返回 None。"""
    if not CLAUDE_CACHE_FILE.exists():
        return None
    try:
        with open(CLAUDE_CACHE_FILE) as f:
            cache = json.load(f)
        if time.time() - cache.get("timestamp", 0) > CLAUDE_CACHE_TTL:
            return None
        return cache.get("data")
    except (json.JSONDecodeError, KeyError):
        return None


def save_claude_cache(data: list[dict]) -> None:
    """保存 Claude 统计数据到缓存。"""
    CLAUDE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache_entry = {"timestamp": time.time(), "data": data}
    with open(CLAUDE_CACHE_FILE, "w") as f:
        json.dump(cache_entry, f, ensure_ascii=False)


def refresh_claude_stats() -> bool:
    """触发 Claude Code 统计数据刷新。"""
    import subprocess
    try:
        result = subprocess.run(
            ["claude", "-p", "/status"],
            capture_output=True,
            timeout=30,
            text=True
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_claude_recent_usage(days: int = 3) -> list[dict]:
    """从 Claude Code stats 缓存获取最近 N 天有 token 消耗的数据。

    使用本地缓存，30分钟刷新一次。缓存过期时会触发 Claude Code 统计数据更新。
    """
    # 尝试从缓存读取
    cached = load_claude_cache()
    if cached is not None:
        return cached[:days]

    # 缓存未命中或已过期，触发刷新
    refresh_claude_stats()

    # 读取 Claude Code 的统计数据
    if not CLAUDE_STATS_FILE.exists():
        return []

    try:
        with open(CLAUDE_STATS_FILE) as f:
            stats = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return []

    daily_tokens = stats.get("dailyModelTokens", [])
    if not daily_tokens:
        return []

    # 转换为列表并计算每天总 token 数
    usage_list = []
    for day in daily_tokens:
        date = day.get("date", "")
        tokens_by_model = day.get("tokensByModel", {})
        total_tokens = sum(tokens_by_model.values())
        if total_tokens > 0:
            usage_list.append({
                "date": date,
                "tokens": total_tokens,
                "models": tokens_by_model,
            })

    # 按日期降序排列
    usage_list.sort(key=lambda r: r["date"], reverse=True)

    # 保存到缓存
    save_claude_cache(usage_list)

    return usage_list[:days]


def format_output(detail: dict, usage: dict, recent: list[dict] | None = None, balance: dict | None = None, claude_recent: list[dict] | None = None) -> str:
    """格式化输出，适合 tmux 状态栏显示。"""
    plan = detail.get("data", {})
    usage_data = usage.get("data", {})

    plan_name = plan.get("planName", "")
    plan_code = plan.get("planCode", "")

    # 余额
    balance_amount = 0.0
    if balance:
        balance_amount = float(balance.get("data", {}).get("balance", "0"))

    lines = []

    # 余额（有订阅或无订阅都显示）
    if balance_amount > 0:
        lines.append(f"Balance: ￥{balance_amount:.2f}")
    # 没有订阅
    if not plan_code:
        lines.append("Token Plan: None")
    else:
        end_date = plan.get("currentPeriodEnd", "")[:10].replace("-", "")[2:]  # YYMMDD

        # 套餐使用量（取 data.usage 下的 plan_total_token）
        usage_info = usage_data.get("usage", {})
        usage_items = usage_info.get("items", [])
        plan_item = next((i for i in usage_items if i["name"] == "plan_total_token"), None)
        month_used = plan_item["used"] if plan_item else 0
        month_limit = plan_item["limit"] if plan_item else 0
        month_percent = (month_used / month_limit * 100) if month_limit > 0 else 0

        lines.append(f"Token Plan: MiMo {plan_name}, exp:{end_date}")
        lines.append(f"Credits usage: {format_tokens(month_used)} / {format_tokens(month_limit)}, {month_percent:.4f}%")

        # 最近 3 天每日消耗
        if recent:
            lines.append("Recent usage:")
            for r in recent:
                credits_used = r["credits"]
                recent_percent = (credits_used / month_limit * 100) if month_limit > 0 else 0
                date_short = r["date"][2:].replace("-", "")  # YYMMDD
                lines.append(
                    f"  - {date_short}: {format_tokens(credits_used)}, {recent_percent:.4f}%, "
                    f"hit:{format_tokens(r['inputHitToken'])}, mis:{format_tokens(r['inputMissToken'])}, out:{format_tokens(r['outputToken'])}"
                )

    # Claude Code 使用量
    if claude_recent:
        lines.append("Claude usage:")
        for r in claude_recent:
            date_short = r["date"][2:].replace("-", "")  # YYMMDD
            lines.append(f"  - {date_short}: {format_tokens(r['tokens'])}")

    return "\n".join(lines)


def format_tmux(detail: dict, usage: dict, recent: list[dict] | None = None, balance: dict | None = None, claude_recent: list[dict] | None = None) -> str:
    """格式化输出为 tmux 状态栏单行格式。"""
    plan = detail.get("data", {})
    usage_data = usage.get("data", {})

    plan_code = plan.get("planCode", "")

    parts = ["MiMo"]

    # 余额
    balance_amount = 0.0
    if balance:
        balance_amount = float(balance.get("data", {}).get("balance", "0"))
    if balance_amount > 0:
        parts.append(f"￥{balance_amount:.2f}")

    # 没有订阅
    if not plan_code:
        parts.append("Cr:-")
        return "[" + " ".join(parts) + "]"

    # 套餐使用量
    usage_info = usage_data.get("usage", {})
    usage_items = usage_info.get("items", [])
    plan_item = next((i for i in usage_items if i["name"] == "plan_total_token"), None)
    month_used = plan_item["used"] if plan_item else 0
    month_limit = plan_item["limit"] if plan_item else 0
    month_percent = (month_used / month_limit * 100) if month_limit > 0 else 0

    parts.append(f"Cr:{month_percent:.4f}%")

    # 最近 3 天每日消耗
    if recent:
        rec_parts = []
        for r in recent:
            credits_used = r["credits"]
            recent_percent = (credits_used / month_limit * 100) if month_limit > 0 else 0
            date_short = r["date"][5:].replace("-", "")  # MMDD
            rec_parts.append(f"{date_short}:{recent_percent:.4f}%")
        parts.append("📊[" + " ".join(rec_parts) + "]")

    # Claude Code 使用量
    if claude_recent:
        claude_parts = []
        for r in claude_recent:
            date_short = r["date"][5:].replace("-", "")  # MMDD
            claude_parts.append(f"{date_short}:{format_tokens(r['tokens'])}")
        parts.append("Claude[" + " ".join(claude_parts) + "]")

    return " ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="MiMo 平台 token 使用量查询工具")
    parser.add_argument("-t", "--tmux", action="store_true", help="输出适合 tmux 状态栏的单行格式")
    args = parser.parse_args()

    config = load_config()

    # 获取 Claude Code 使用量（不依赖 MiMo API）
    claude_recent = get_claude_recent_usage(days=3)

    # 尝试从缓存读取
    cached = load_cache()
    if cached:
        # 如果缓存包含错误信息，显示 Claude 使用量后退出
        if "error" in cached:
            if claude_recent:
                if args.tmux:
                    claude_parts = []
                    for r in claude_recent:
                        date_short = r["date"][5:].replace("-", "")  # MMDD
                        claude_parts.append(f"{date_short}:{format_tokens(r['tokens'])}")
                    print("Claude[" + " ".join(claude_parts) + "]")
                else:
                    print("Claude usage:")
                    for r in claude_recent:
                        date_short = r["date"][2:].replace("-", "")  # YYMMDD
                        print(f"  - {date_short}: {format_tokens(r['tokens'])}")
            if args.tmux:
                print("MiMo: expired")
            else:
                print(cached["error"], file=sys.stderr)
            sys.exit(1)
        fmt = format_tmux if args.tmux else format_output
        print(fmt(cached["detail"], cached["usage"], cached.get("recent"), cached.get("balance"), claude_recent))
        return

    # 缓存未命中，请求 API
    try:
        detail = get_plan_detail(config)
        usage = get_plan_usage(config)
        recent = get_recent_days_usage(config, days=3)
        balance = get_balance(config)
        save_cache({"detail": detail, "usage": usage, "recent": recent, "balance": balance})
        fmt = format_tmux if args.tmux else format_output
        print(fmt(detail, usage, recent, balance, claude_recent))
    except AuthError as e:
        # 认证失败，缓存错误信息
        save_cache({"error": str(e)})
        if args.tmux:
            print("MiMo: expired")
        else:
            print(str(e), file=sys.stderr)
        if claude_recent:
            if args.tmux:
                claude_parts = []
                for r in claude_recent:
                    date_short = r["date"][5:].replace("-", "")  # MMDD
                    claude_parts.append(f"{date_short}:{format_tokens(r['tokens'])}")
                print("Claude[" + " ".join(claude_parts) + "]")
            else:
                print("Claude usage:")
                for r in claude_recent:
                    date_short = r["date"][2:].replace("-", "")  # YYMMDD
                    print(f"  - {date_short}: {format_tokens(r['tokens'])}")
        sys.exit(1)
    except requests.HTTPError as e:
        if args.tmux:
            print(f"MiMo: response {e.response.status_code}")
        else:
            print(f"请求失败: {e}", file=sys.stderr)
        if claude_recent:
            if args.tmux:
                claude_parts = []
                for r in claude_recent:
                    date_short = r["date"][5:].replace("-", "")  # MMDD
                    claude_parts.append(f"{date_short}:{format_tokens(r['tokens'])}")
                print("Claude[" + " ".join(claude_parts) + "]")
            else:
                print("Claude usage:")
                for r in claude_recent:
                    date_short = r["date"][2:].replace("-", "")  # YYMMDD
                    print(f"  - {date_short}: {format_tokens(r['tokens'])}")
        sys.exit(1)
    except requests.RequestException as e:
        if args.tmux:
            print("MiMo: request error")
        else:
            print(f"请求失败: {e}", file=sys.stderr)
        if claude_recent:
            if args.tmux:
                claude_parts = []
                for r in claude_recent:
                    date_short = r["date"][5:].replace("-", "")  # MMDD
                    claude_parts.append(f"{date_short}:{format_tokens(r['tokens'])}")
                print("Claude[" + " ".join(claude_parts) + "]")
            else:
                print("Claude usage:")
                for r in claude_recent:
                    date_short = r["date"][2:].replace("-", "")  # YYMMDD
                    print(f"  - {date_short}: {format_tokens(r['tokens'])}")
        sys.exit(1)


if __name__ == "__main__":
    main()
