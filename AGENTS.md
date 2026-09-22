# AGENTS.md

## 项目概述

单文件 Python CLI 工具 (`mimo_stat.py`)，用于查询小米 MiMo 平台 (platform.xiaomimimo.com) 的 token 使用量数据，支持 tmux 状态栏集成。支持浏览器自动登录、Cookie 过期自动续期、可配置的数值显示模式。

## 快速开始

```bash
pipx install -e .            # 安装为全局命令
pip install playwright && playwright install chromium  # 可选：浏览器自动登录
mimo-stat                    # 详细输出
mimo-stat -t                 # tmux 状态栏单行格式
mimo-stat -l                 # 通过浏览器登录获取 Cookie
mimo-stat -c "cookie_string" # 手动设置 Cookie
```

## 架构

- `mimo_stat.py` — 完整应用（配置、认证、API、格式化、CLI）
- `setup.py` — setuptools 配置，`psutil` 虽在依赖中但当前代码**未使用**
- `requirements.txt` — Python 依赖
- 配置文件: `./conf/config.yml`（cookie、base_url、display）
- 缓存文件: `./conf/cache.json`（30 秒有效期，错误响应也会缓存）
- 浏览器数据: `./conf/browser-data/`（Playwright 持久化上下文，保存登录状态）

## 配置说明

首次运行会自动创建 `./conf/config.yml`，包含以下配置项：

```yaml
base_url: https://platform.xiaomimimo.com
cookie: ""
display:
  recent_days: 3       # 最近 N 天每日消耗
  recent_months: 2     # 最近 N 个月月度消耗
  value_format: token  # 显示模式: token | percent | amount
  precision: 3         # 小数点位数（amount 模式固定 2 位）
```

Cookie 获取方式：
1. **自动获取**（`mimo-stat -l`）— 打开浏览器，登录后程序自动提取 Cookie
2. **自动触发** — cookie 为空或过期时，程序自动尝试浏览器登录
3. **手动设置**（`mimo-stat -c`）

## 认证与自动续期

- Cookie 过期（API 返回 401）时，程序自动调用 `login_with_browser()` 重新登录
- 登录失败时，错误信息缓存到 `cache.json`，避免短时间内重复打开浏览器
- 缓存中的错误信息格式：`{"error": "错误信息"}`
- `login_with_browser()` 使用 Playwright 持久化上下文（`./conf/browser-data/`），保存登录状态
- 需要图形环境（`DISPLAY` 环境变量），SSH/tmux 下会尝试检测 `/tmp/.X11-unix/X0`

## API 认证特殊处理

POST 请求需要从 cookie 中提取 `api-platform_ph`，作为 URL 查询参数附加，并进行 URL 编码（特别是 `+` → `%2B`）。详见 `get_ph_from_cookie()` 和 `api_post()`。

## 缓存行为

- `load_cache()` 对于缓存的认证失败返回 `{"error": msg}` — 调用方需检查 `if "error" in cached`
- `save_cache()` 将错误存储在顶层，而非 `data` 键中
- 正常数据缓存格式：`{"timestamp": ..., "data": {"detail": ..., "usage": ..., "recent": ..., "balance": ..., "monthly": ...}}`

## 数据获取流程

主函数 `main()` 按以下顺序获取数据：

1. `get_plan_detail()` — 套餐详情（类型、额度、到期时间）
2. `get_plan_usage()` — 月度使用量
3. `get_recent_days_usage()` — 最近 N 天每日消耗（内部按月查询后筛选）
4. `get_monthly_usage()` — 最近 N 个月月度消耗（按年查询后按月聚合）
5. `get_balance()` — 账户余额

全部成功后缓存完整结果，后续 30 秒内直接读缓存。

## 格式化输出

### 详细格式 (`format_output`)
同时展示 token、percent、amount 三种格式，用逗号分隔。包含：余额、套餐信息、Credits 使用量、最近 N 天每日消耗、最近 N 个月月度消耗。

### Tmux 格式 (`format_tmux`)
单行紧凑格式，由 `value_format` 配置决定数值显示模式。结构：`🍚 Bal:¥xx Crt:xx% Dai[DD:val ...] Mon[MM:val ...]`

## 套餐定价（用于 amount 模式）

| plan_code | 月价(¥) | 月度 Credits |
|-----------|---------|-------------|
| lite:month | 39 | 4.1B |
| standard:month | 99 | 11B |
| pro:month | 329 | 38B |
| max:month | 659 | 82B |
| lite:year | 411.84 | 49.2B |
| standard:year | 1045.44 | 132B |
| pro:year | 3474.24 | 456B |
| max:year | 6959.04 | 984B |

单价 = 月价 / 月度 Credits，用于将 credit 转换为金额。

## API 端点

基础路径: `https://platform.xiaomimimo.com/api/v1`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/tokenPlan/detail` | GET | 套餐详情（类型、额度、到期时间） |
| `/tokenPlan/usage` | GET | 月度使用量（已消耗 credits） |
| `/usage/token-plan/list` | POST | 使用明细（每日/月度，需带 `api-platform_ph` 参数） |
| `/balance` | GET | 账户余额 |

认证方式: 小米账号 Cookie，支持浏览器自动获取。POST 请求需在 URL 中带 `api-platform_ph` 查询参数。

## Token 到 Credit 转换率

| 模型 | 命中缓存 | 未命中缓存 | 输出 |
|------|----------|------------|------|
| mimo-v2.6-pro | 2.5 | 300 | 600 |
| mimo-v2.6-flash | 2 | 100 | 200 |
| mimo-v2.6-pro-ultraspeed | 25 | 3000 | 6000 |
| mimo-v2.5-pro | 2.5 | 300 | 600 |
| mimo-v2.5 | 2 | 100 | 200 |

未识别模型默认使用 mimo-v2.5 费率。

## 无测试 / CI

本项目无测试套件、无 linting、无类型检查。修改后直接运行 `mimo-stat` 验证（需要有效 cookie）。
