# Plutus 安装指南

## 给用户

把下面这句话发给 Hermes：

```text
帮我安装 Plutus：https://raw.githubusercontent.com/zxh2010/plutus/main/docs/install.md
```

Plutus 当前仅支持 macOS。它通过邮箱 IMAP 读取招商银行交易邮件，将账本保存在本机，并使用 Hermes 完成 AI 分类和微信通知。当前支持 QQ 邮箱、163 邮箱和 Gmail。

---

## 给 Hermes Agent 的安装说明

### 安装目标

将 Plutus 安装到 `~/.plutus`，向 Hermes 注册 Plutus MCP，启动 Web 和 daemon 的 launchd 常驻服务，并引导用户完成邮箱授权。

### 安全边界

- 不得使用 `sudo`。
- 不得关闭或削弱 macOS 的安全机制。
- 不得覆盖已有的 `config.toml`、`plutus.db` 或 `secrets/`。
- 不得要求用户在聊天中发送邮箱授权码或应用专用密码。用户必须在 Plutus 本地设置页中填写。
- 除非用户明确要求，否则不得回填历史邮件，也不得发送微信测试消息。
- Plutus 文件只能写入 `~/.plutus`，用户级 LaunchAgent 只能写入 `~/Library/LaunchAgents`，日志只能写入 `~/Library/Logs/plutus`；Hermes MCP 配置只能通过 `hermes` 命令管理。
- 如果操作需要修改系统级配置、安装缺失的系统软件或提升权限，必须先停止并向用户说明，由用户决定是否继续。

### 第一步：安装前检查

依次检查：

```bash
uname -s
python3 --version
git --version
hermes --version
hermes send --list weixin --json
```

安装要求：

- macOS
- Python 3.10 或更高版本
- Git
- 已安装 Hermes，并且至少配置了一个微信目标

如果在 `PATH` 中找不到 `hermes`，继续检查 `~/.local/bin/hermes`。

如果存在多个微信目标，询问用户要使用哪一个。将用户选择的目标通过 `PLUTUS_WEIXIN_TARGET` 传给安装器，不得擅自选择。

### 第二步：克隆或更新项目

默认安装目录固定为 `~/.plutus`。

首次安装时执行：

```bash
git clone https://github.com/zxh2010/plutus.git ~/.plutus
```

如果目录已经存在，先检查工作区：

```bash
git -C ~/.plutus status --short
```

如果工作区干净，执行快进更新：

```bash
git -C ~/.plutus pull --ff-only
```

如果存在本地修改，不得丢弃或覆盖。停止安装，并向用户说明哪些文件被修改。

### 第三步：执行安装

只有一个微信目标时执行：

```bash
bash ~/.plutus/scripts/install.sh
```

用户选择了指定微信目标时执行：

```bash
PLUTUS_WEIXIN_TARGET='weixin:TARGET_ID' bash ~/.plutus/scripts/install.sh
```

安装器可以安全地重复执行，它会：

1. 创建 Python 虚拟环境。
2. 仅在配置不存在时创建默认的本地配置。
3. 仅在数据库不存在时初始化 SQLite 账本。
4. 向 Hermes 注册 Plutus MCP。
5. 安装并启动 Web 和 daemon 的 LaunchAgent。
6. 检查数据库、常驻服务、Web 接口和 MCP 连接。

如果需要先预览操作且不写入任何文件，执行：

```bash
bash ~/.plutus/scripts/install.sh --dry-run
```

### 第四步：完成邮箱授权

让用户打开：

```text
http://127.0.0.1:8973/#config
```

用户需要在设置页选择接收招商银行交易邮件的邮箱类型，并填写邮箱地址和授权码。

当前支持：

- QQ 邮箱：填写 QQ 邮箱地址和 IMAP/SMTP 授权码。
- 163 邮箱：填写 163 邮箱地址和客户端授权码。
- Gmail：填写 Gmail 地址和 Google 应用专用密码。

授权码不是邮箱登录密码。授权码只保存在 `~/.plutus/secrets/`，不得粘贴到聊天中。

提醒用户在招商银行开启以下邮件通知：

- 借记卡：一卡通账户变动通知
- 信用卡：每日信用管家

配置完成后，让用户点击设置页中的邮件自检按钮。

### 第五步：最终检查

执行：

```bash
launchctl print "gui/$(id -u)/ai.plutus.web"
launchctl print "gui/$(id -u)/ai.plutus.daemon"
~/.local/bin/hermes mcp test plutus
sqlite3 -readonly ~/.plutus/plutus.db "PRAGMA integrity_check;"
```

确认以下结果：

- 两个 LaunchAgent 都处于运行状态。
- Web 控制台可以打开。
- MCP 检查通过。
- 数据库完整性检查返回 `ok`。
- 用户完成邮箱授权后，邮件自检通过。

不得自动回填历史邮件。安装完成后可以询问用户是否需要回填；只有用户明确同意并指定起始日期后才能执行。
