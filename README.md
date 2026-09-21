# mimo-stat

MiMo 平台 Token 使用量查询工具，用于 tmux 状态栏等场景实时显示。

## 功能

- 查询 MiMo Token Plan 套餐信息，显示余额、套餐使用量
- 可配置的最近 N 天每日消耗 + 最近 N 个月月度消耗
- 三种数值显示模式：`token`（智能单位）、`percent`（百分比）、`amount`（金额）
- 详细格式同时展示三种格式，tmux 格式支持选择单一模式
- Token 自动转换为 Credit（按模型费率计算）
- 浏览器自动登录获取 Cookie（Playwright），Cookie 过期自动重新登录
- 登录失败缓存错误信息，避免重复打开浏览器
- 30 秒缓存，避免频繁请求 API

## 安装

```bash
pipx install -e .
```

浏览器自动登录功能需要额外安装 Playwright：

```bash
pip install playwright
playwright install chromium
```

## 使用

```bash
mimo-stat       # 详细格式
mimo-stat -t    # tmux 状态栏单行格式
mimo-stat -l    # 通过浏览器登录获取 Cookie
mimo-stat -c "cookie_string"  # 手动设置 Cookie
mimo-stat -h    # 查看帮助
```

### 详细格式

```log
Balance: ¥31.61
Token Plan: MiMo Lite, exp:270607
Credits usage: 134.27M, 0.273%, ¥3.88 / 49.20B, 100.000%, ¥1420.00
Recent usage:
  - 260608: 162.35M, 0.330%, ¥4.70, hit:17.82M, mis:265.6K, out:63.6K
  - 260607: 2.08M, 0.004%, ¥0.06, hit:512, mis:20.7K, out:25
Monthly usage:
  - 2609: 1.50B, 3.050%, ¥43.33, hit:180.5M, mis:5.20M, out:1.20M
  - 2608: 3.20B, 6.531%, ¥92.70, hit:350.0M, mis:12.0M, out:2.50M
```

### Tmux 格式

```
🍚 Bal:¥31.61 Crt:0.273% Dai[08:4.70 07:0.06] Mon[09:43.33 08:92.70]
```

数值显示模式通过 `value_format` 配置，设为 `token` 或 `percent` 时对应字段会显示为 token 单位或百分比。默认 `amount` 模式显示金额（¥）。

### 错误时

```
🍚response 401
```

## 配置

首次运行会自动创建 `./conf/config.yml`：

```yaml
base_url: https://platform.xiaomimimo.com
cookie: ""
display:
  recent_days: 3       # 最近 N 天每日消耗
  recent_months: 2     # 最近 N 个月月度消耗
  value_format: token  # 显示模式: token | percent | amount
  precision: 3         # 小数点位数（amount 模式固定 2 位）
```

### 获取 Cookie

**方式一：自动获取（推荐）**

```bash
mimo-stat -l
```

会自动打开浏览器并跳转到 MiMo 平台，登录后程序自动提取 Cookie 并保存。首次运行或 Cookie 为空时也会自动触发。

**方式二：手动获取**

1. 打开 https://platform.xiaomimimo.com/console/plan-manage
2. 登录小米账号
3. 打开浏览器开发者工具（F12）→ Network
4. 复制请求头中的 Cookie 值
5. 运行 `mimo-stat -c "<cookie>"` 保存

### 自动续期

Cookie 过期（API 返回 401）时，程序会自动尝试通过浏览器重新登录。如果登录失败，错误信息会被缓存，避免短时间内重复打开浏览器。

## Tmux 集成

在 `~/.tmux.conf` 中添加：

```bash
set -g status-right '#(~/.local/bin/mimo-stat -t)'
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/tokenPlan/detail` | GET | 套餐详情 |
| `/api/v1/tokenPlan/usage` | GET | 月度使用量 |
| `/api/v1/usage/token-plan/list` | POST | 每日使用明细 |
| `/api/v1/balance` | GET | 账户余额 |

## Token 到 Credit 转换

| 模型 | 命中缓存 | 未命中缓存 | 输出 |
|------|----------|------------|------|
| mimo-v2.5-pro | 2.5 | 300 | 600 |
| mimo-v2.5 | 2 | 100 | 200 |
| mimo-v2-pro | 2.5 | 300 | 600 |
| mimo-v2-omni | 2 | 100 | 200 |

## License

MIT
