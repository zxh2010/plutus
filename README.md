# Plutus · 基于 Hermes Agent 的自动记账软件

Plutus 自动采集招商银行交易邮件，使用 Hermes Agent 解析和分类，生成本地账本，并通过微信完成通知、查询和分类纠正。

## 1. 原理

```text
招商银行交易邮件
        │
        ▼
Gmail IMAP 增量采集
        │
        ├── 信用卡「每日信用管家」→ 交易解析
        └── 借记卡「一卡通账户变动通知」→ Hermes AI 识别支出、转账和进项
        │
        ▼
SQLite 本地账本
        │
        ├── Web 控制台：明细、分类、统计、进项、知识库
        └── Hermes → 微信：新交易通知、查询和分类纠正
```

核心数据只保存在本机：

- Gmail 通过 IMAP 只读采集，并使用消息 ID 和 UID 水位增量去重。
- Hermes 负责解析借记卡通知、建议交易分类和发送微信通知。
- SQLite 是账本的唯一事实来源。
- Web 控制台和 Hermes MCP 通过同一套本地 API 读写账本。
- 用户在微信中的查询或纠正，由 Hermes 调用 Plutus MCP 写回本地数据库。

## 2. 一键安装

前置条件：

- macOS
- Python 3.10+
- Git
- 已安装 Hermes Agent，并已配置微信通道
- 招商银行已开启“一卡通账户变动通知”和“每日信用管家”的 Email 通知

把下面这句话发给 Hermes：

```text
帮我安装 Plutus：https://raw.githubusercontent.com/zxh2010/plutus/main/docs/install.md
```

Hermes 会自动：

1. 将 Plutus 安装到 `~/.plutus`。
2. 创建 Python 虚拟环境和本地配置。
3. 初始化 SQLite 账本。
4. 注册 Plutus MCP。
5. 安装并启动 daemon 与 Web 服务。
6. 检查 Hermes 微信目标、数据库和服务状态。

安装完成后，打开：

```text
http://127.0.0.1:8973/#config
```

在本地设置页填写 Gmail 地址和 Google 应用专用密码，再运行邮件自检即可。密码只保存在 `~/.plutus/secrets/`，无需发送给 Hermes。

完整安装规则和安全边界见 [`docs/install.md`](docs/install.md)。
