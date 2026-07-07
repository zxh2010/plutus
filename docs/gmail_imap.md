# 让 Plutus 能读你的 Gmail（IMAP + 应用专用密码）

不用 Google Cloud。总共两步：开两步验证 → 生成一个 16 位密码。约 3 分钟。

## 第 1 步：确认「两步验证」已开启

应用专用密码只有在开了两步验证后才能生成。

1. 打开 https://myaccount.google.com/security
2. 找到「两步验证 / 2-Step Verification」：
   - 已显示「已开启」→ 跳到第 2 步。
   - 显示「已关闭」→ 点进去按提示用手机开启（需要收一次验证码）。

## 第 2 步：生成应用专用密码

1. 打开 https://myaccount.google.com/apppasswords
   （如果提示再次登录，正常登录即可。）
2. 在「应用名称 / App name」输入框里填一个好认的名字，例如：
   ```
   plutus-macmini
   ```
3. 点「创建 / Create」。
4. 弹出一串 **16 位密码**，形如：
   ```
   abcd efgh ijkl mnop
   ```
   这串只会显示一次。

## 第 3 步：把密码交给 Plutus

二选一：

- **方式 A（你自己放）**：在项目里新建文件 `secrets/app_password.txt`，
  把这 16 位粘进去（去掉空格也行），保存。然后把你的邮箱填进 `config.toml`。
- **方式 B（发我）**：直接把这 16 位密码贴给我，我帮你写进 `secrets/app_password.txt`
  （该目录已被 git 忽略，不会进版本库）。

## 安全说明

- 这个密码**只是给 Plutus 读邮件用**，和你的 Google 登录密码不同。
- 任何时候都能在 https://myaccount.google.com/apppasswords 一键「撤销」，
  撤销后 Plutus 立即失去访问，不影响你正常登录。
- 它存在本机 `secrets/app_password.txt`，已被 `.gitignore` 排除，不会被提交。

## 备注

- Gmail 的 IMAP 现在默认开启，一般无需在 Gmail 设置里再打开。
- Plutus 用 IMAP 的 `X-GM-RAW` 直接套用 Gmail 搜索语法（如 `from:cmbchina.com`），
  并按邮件 UID 增量拉取、断点续读。
