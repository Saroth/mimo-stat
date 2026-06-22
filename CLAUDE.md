# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

mimo-stat — 爬取小米 MiMo 平台 (platform.xiaomimimo.com) 的 token 使用量数据，用于 tmux 状态栏等工具实时显示。

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
mimo-stat       # 详细格式
mimo-stat -t    # tmux 状态栏单行格式
```

首次运行会创建 `~/.config/mimo-stat/config.json`，需填入 cookie 后重试。

**详细格式输出:**
```
Balance: ￥31.61
Token Plan: MiMo Lite, exp:270607
Credits usage: 163.84M / 49.20B, 0.3330%
Recent usage:
  - 260609: 22.44M, 0.0456%, hit:133.6K, mis:68.6K, out:2.5K
  - 260608: 174.67M, 0.3550%, hit:20.72M, mis:273.2K, out:68.2K
  - 260607: 2.08M, 0.0042%, hit:512, mis:20.7K, out:25
Claude usage:
  - 260616: 49.0K
  - 260615: 218.0K
  - 260614: 115.3K
```
根据官网[说明](https://platform.xiaomimimo.com/docs/zh-CN/price/tokenplan/subscription)，
MiMo有夜间 0.8 倍消耗，所以Recent的每日消耗Token换算的Credits可能高于实际用量.

**Tmux 格式输出:**
```
MiMo ￥31.61 Cr:0.3330% 📊[0609:0.0456% 0608:0.3550% 0607:0.0042%] Claude[0616:49.0K 0615:218.0K 0614:115.3K]
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

- `mimo_stat.py` — 入口，包含配置加载、API 请求、格式化输出
- `~/.config/tmux/mimo-stat` — 我的部署目录
- `~/.config/mimo-stat/config.json` — 用户配置（base_url、cookie）
- `~/.config/mimo-stat/cache.json` — 缓存文件（30 秒有效期）

### 缓存规则

- 正常数据缓存 30 秒
- 认证失败（401）也会缓存，避免频繁请求失败的 API
- 缓存格式:
  - 正常: `{"timestamp": ..., "data": {...}}`
  - 错误: `{"timestamp": ..., "error": "错误信息"}`
