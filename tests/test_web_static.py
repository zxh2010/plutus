"""Static web behavior tests.

These tests execute small slices of the browser JavaScript with Node so UI state
regressions can be reproduced without a real browser.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_app_js(assertions: str) -> str:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is required for static web behavior tests")
    source = (ROOT / "plutus/web/static/app.js").read_text(encoding="utf-8")
    script = """
global.document = { addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; } };
global.location = { hash: "" };
global.history = { replaceState() {} };
global.fetch = async () => { throw new Error("fetch should not run in this test"); };
""" + source + "\n" + assertions
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


class WebStaticTest(unittest.TestCase):
    def test_mail_collection_is_one_connected_module(self):
        _run_app_js(
            """
const cfg = {
  mail_provider: "gmail",
  mail_provider_label: "Gmail",
  mail_email: "user@gmail.com",
  gmail_email: "user@gmail.com",
  mail_configured: true,
  gmail_configured: true,
  channel_support: { debit: { email: true }, credit: { email: true } },
  mail_providers: [
    { key: "gmail", label: "Gmail" },
    { key: "qq", label: "QQ 邮箱" },
    { key: "163", label: "163 邮箱" },
  ],
};
const debit = _intakeCard("debit", "借记卡", cfg);
const credit = _intakeCard("credit", "信用卡", cfg);
const html = _mailCollectionCard(cfg, debit, credit);
if (!html.includes("招商银行邮件采集")) throw new Error("mail collection module title is missing");
if (!html.includes("一个邮箱账号接收招行通知邮件")) throw new Error("shared mailbox relationship is missing");
if (!html.includes("一卡通账户变动通知")) throw new Error("debit source row is missing");
if (!html.includes("每日信用管家")) throw new Error("credit source row is missing");
if ((html.match(/当前生效：Gmail/g) || []).length !== 1) {
  throw new Error("current mailbox should be shown once inside the collection module");
}
for (const id of ["aw-provider", "aw-email", "aw-pw", "aw-save", "aw-out"]) {
  const count = (html.match(new RegExp(`id="${id}"`, "g")) || []).length;
  if (count !== 1) throw new Error(`${id} should be rendered once, got ${count}`);
}
"""
        )

    def test_mail_provider_switch_clears_legacy_email_address(self):
        _run_app_js(
            """
const cfg = {
  mail_provider: "gmail",
  mail_provider_label: "Gmail",
  mail_email: "user@gmail.com",
  gmail_email: "user@gmail.com",
  channel_support: { debit: { email: true }, credit: { email: true } },
  mail_providers: [
    { key: "gmail", label: "Gmail" },
    { key: "qq", label: "QQ 邮箱" },
    { key: "163", label: "163 邮箱" },
  ],
};
for (const [provider, expectedText] of [
  ["qq", "粘贴 QQ 生成的授权码"],
  ["163", "粘贴 163 新增的授权密码"],
]) {
  state.mailProviderDraft = provider;
  const html = _mailWizard(cfg);
  if (!html.includes(expectedText)) throw new Error(`${provider} help text was not rendered`);
  if (provider === "qq" && !html.includes("右上角头像 → 设置 → 账号与安全 → 安全设置")) {
    throw new Error("QQ authorization should include the official security settings path");
  }
  if (provider === "163" && !html.includes("设置 → POP3/SMTP/IMAP → 新增授权密码")) {
    throw new Error("163 authorization should include the official POP3/SMTP/IMAP path");
  }
  if (html.includes('value="user@gmail.com"')) {
    throw new Error(`switching to ${provider} must not keep the configured Gmail address`);
  }
}

state.mailProviderDraft = "gmail";
const gmailHtml = _mailWizard(cfg);
if (!gmailHtml.includes('value="user@gmail.com"')) {
  throw new Error("switching back to the configured provider should restore its email");
}
if (!gmailHtml.includes("网络代理")) {
  throw new Error("Gmail authorization should expose optional proxy settings");
}
if (_mailCollectionCard(cfg, _intakeCard("debit", "借记卡", cfg), _intakeCard("credit", "信用卡", cfg)).includes("gmail_auth.json")) {
  throw new Error("UI should show the provider-neutral mail_auth.json secret file");
}
if (!_mailCollectionCard(cfg, _intakeCard("debit", "借记卡", cfg), _intakeCard("credit", "信用卡", cfg)).includes("mail_auth.json")) {
  throw new Error("UI should show mail_auth.json");
}
"""
        )

    def test_proxy_settings_are_gmail_only(self):
        _run_app_js(
            """
const cfg = {
  mail_provider: "gmail",
  mail_provider_label: "Gmail",
  mail_email: "user@gmail.com",
  gmail_email: "user@gmail.com",
  mail_proxy_enabled: true,
  mail_proxy_host: "127.0.0.1",
  mail_proxy_port: 8118,
  mail_providers: [
    { key: "gmail", label: "Gmail" },
    { key: "qq", label: "QQ 邮箱" },
    { key: "163", label: "163 邮箱" },
  ],
};
state.mailProviderDraft = "gmail";
let html = _mailWizard(cfg);
if (!html.includes("网络代理")) throw new Error("Gmail should show proxy settings");
if (!html.includes('id="aw-proxy-enabled"')) throw new Error("proxy checkbox is missing");
if (!html.includes('value="127.0.0.1"')) throw new Error("proxy host should use configured value");

state.mailProviderDraft = "qq";
html = _mailWizard(cfg);
if (html.includes("网络代理") || html.includes("aw-proxy-enabled")) {
  throw new Error("QQ should not show proxy settings");
}

state.mailProviderDraft = "163";
html = _mailWizard(cfg);
if (html.includes("网络代理") || html.includes("aw-proxy-enabled")) {
  throw new Error("163 should not show proxy settings");
}
"""
        )

    def test_mail_editing_pauses_intake_auto_checks(self):
        _run_app_js(
            """
const cfg = {
  mail_provider: "gmail",
  mail_provider_label: "Gmail",
  mail_email: "user@gmail.com",
  gmail_email: "user@gmail.com",
  mail_configured: true,
  gmail_configured: true,
  channel_support: { debit: { email: true }, credit: { email: true } },
};
state.mailProviderDraft = "qq";
const debit = _intakeCard("debit", "借记卡", cfg);
const credit = _intakeCard("credit", "信用卡", cfg);
if (debit.autoRun || credit.autoRun) {
  throw new Error("editing a new mailbox provider must pause automatic self-checks");
}
if (debit.conn.val !== "待保存" || credit.conn.val !== "待保存") {
  throw new Error("editing state should show pending save instead of checking");
}
const settings = _mailCollectionCard(cfg, debit, credit);
if (!settings.includes("正在配置新邮箱，保存成功后生效")) {
  throw new Error("editing state should explain that the new mailbox is not active yet");
}
"""
        )


if __name__ == "__main__":
    unittest.main()
