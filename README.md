# mimo-console

MiMo 平台 Token 使用量查询工具，用于 tmux 状态栏等场景实时显示。

## 功能

- 查询 MiMo Token Plan 套餐信息
- 显示余额、套餐使用量、最近 3 天消耗
- Token 自动转换为 Credit
- 支持 tmux 状态栏单行格式输出
- 10 秒缓存，避免频繁请求 API

## 安装

```bash
pipx install -e .
```

## 使用

```bash
mimo       # 详细格式
mimo -t    # tmux 状态栏单行格式
mimo -h    # 查看帮助
```

### 详细格式

```
Balance: ￥31.61
Token Plan: MiMo Lite, exp:270607
Credits usage: 134.27M / 49.20B, 0.2729%
Recent usage:
  - 260608: 162.35M, 0.3300%, hit:17.82M, mis:265.6K, out:63.6K
  - 260607: 2.08M, 0.0042%, hit:512, mis:20.7K, out:25
```

### Tmux 格式

```
[MiMo 💰￥31.61 🎫TP:0.2746% 📊Rec[0608:0.3300% 0607:0.0042%]]
```

### 错误时

```
[MiMo: response 401]
```

## 配置

首次运行会创建 `~/.config/mimo-console/config.json`，填入浏览器登录后的 Cookie：

```json
{
  "cookie": "your_cookie_here"
}
```

### 获取 Cookie

1. 打开 https://platform.xiaomimimo.com/console/plan-manage
2. 登录小米账号
3. 打开浏览器开发者工具（F12）→ Network
4. 复制请求头中的 Cookie 值

## Tmux 集成

在 `~/.tmux.conf` 中添加：

```bash
set -g status-right '#(~/.local/bin/mimo -t)'
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/tokenPlan/detail` | GET | 套餐详情 |
| `/api/v1/tokenPlan/usage` | GET | 使用量 |
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
