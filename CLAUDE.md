# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

mimo-console — 爬取小米 MiMo 平台 (platform.xiaomimimo.com) 的 token 使用量数据，用于 tmux 状态栏等工具实时显示。

## Tech Stack

- Python 3
- requests — HTTP 请求
- psutil — 系统监控

## Setup

```bash
pipx install -e .  # 安装为全局命令
```

## Usage

```bash
mimo       # 详细格式
mimo -t    # tmux 状态栏单行格式
```

首次运行会创建 `~/.config/mimo-console/config.json`，需填入 cookie 后重试。

**详细格式输出:**
```
Balance: ￥31.61
Token Plan: MiMo Lite, exp:270607
Credits usage: 125.35M / 49.20B, 0.2548%
Recent usage:
  - 260608: 152.28M, 0.3095%, hit:15.42M, mis:259.8K, out:59.6K
  - 260607: 2.08M, 0.0042%, hit:512, mis:20.7K, out:25
```

**Tmux 格式输出:**
```
[MiMo: ￥31.61 TP:0.2541% Rec[0608:0.3095% 0607:0.0042%]]
```

## API

基础路径: `https://platform.xiaomimimo.com/api/v1`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/tokenPlan/detail` | GET | 套餐详情（类型、额度、到期时间） |
| `/tokenPlan/usage` | GET | 使用量（已消耗 credits） |
| `/tokenPlan/list` | GET | 套餐列表 |
| `/tokenPlan/subscription/status` | GET | 订阅状态 |
| `/usage/token-plan/list` | POST | 每日使用明细（需带 `api-platform_ph` 参数） |
| `/balance` | GET | 账户余额 |

认证: 小米账号 Cookie，通过 `account.xiaomi.com` 登录获取。POST 请求需在 URL 中带 `api-platform_ph` 查询参数。

## Token to Credit Conversion

| 模型 | 命中缓存 | 未命中缓存 | 输出 |
|------|----------|------------|------|
| mimo-v2.5-pro | 2.5 | 300 | 600 |
| mimo-v2.5 | 2 | 100 | 200 |
| mimo-v2-pro | 2.5 | 300 | 600 |
| mimo-v2-omni | 2 | 100 | 200 |

## Architecture

- `mimo.py` — 入口，包含配置加载、API 请求、格式化输出
- `~/.config/mimo-console/config.json` — 用户配置（base_url、cookie）
- `~/.config/mimo-console/cache.json` — 缓存文件（10 秒有效期）
