"use strict";

const state = { month: null, months: [], categories: [], view: "ledger",
               billingStartDay: 1,
               ledger: { card: "", status: "", q: "" },
               mailProviderDraft: "", mailEmailDraftProvider: "",
               mailEmailDraft: "",
               mailProxyDraftProvider: "", mailProxyEnabledDraft: false,
               mailProxyHostDraft: "", mailProxyPortDraft: "" };

// ---- helpers -----------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body || {}) });

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function money(n) {
  const v = Number(n || 0);
  const s = Math.abs(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (v < 0 ? "−¥" : "¥") + s;
}
function fmtTs(ms) {
  if (!ms) return "";
  const d = new Date(Number(ms));
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
// The real start/end dates of a billing period "YYYY-MM" for a given start day.
// Day 1 yields the plain calendar month (6/1 – 6/30); a later day runs into the
// next month (6/15 – 7/14).
function periodRange(m, day) {
  const [y, mo] = m.split("-").map(Number);
  const start = new Date(y, mo - 1, day);
  const end = new Date(y, mo, day);   // next period's start day...
  end.setDate(end.getDate() - 1);     // ...minus one day
  const f = (d) => `${d.getMonth() + 1}/${d.getDate()}`;
  return `${f(start)} – ${f(end)}`;
}
// Always-on range caption shown beside a period, e.g. "6/15 – 7/14".
const cycleRange = (m) => periodRange(m, state.billingStartDay || 1);
// "2026-06 · 6/15 – 7/14" for places that need month + range on one line.
const monthWithRange = (m) => (m ? `${m} · ${cycleRange(m)}` : m);

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 2600);
}

// Shared hover tooltip, appended to <body> so overflow:hidden ancestors can't
// clip it. Attach to any element carrying data-tip via attachTips(root).
function _tipEl() {
  let t = document.getElementById("tip");
  if (!t) { t = document.createElement("div"); t.id = "tip"; t.className = "tip"; t.hidden = true; document.body.appendChild(t); }
  return t;
}
function attachTips(root, selector = "[data-tip]") {
  const tip = _tipEl();
  root.querySelectorAll(selector).forEach((el) => {
    el.addEventListener("mouseenter", () => { tip.textContent = el.dataset.tip; tip.hidden = false; });
    el.addEventListener("mouseleave", () => { tip.hidden = true; });
    el.addEventListener("mousemove", (e) => {
      const pad = 14, r = tip.getBoundingClientRect();
      let x = e.clientX + pad, y = e.clientY + pad;
      if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
      if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    });
  });
}

// ---- bootstrap ---------------------------------------------------------
async function bootstrap() {
  const b = await api(`/api/bootstrap?month=${state.month || ""}`);
  state.categories = b.categories;
  state.months = b.months;
  state.billingStartDay = b.billing_start_day || 1;
  if (!state.month) state.month = b.month;
  $("#stat-spend").textContent = money(b.summary.spend);
  $("#stat-pending").textContent = b.total_pending;  // global, across all months
}

async function refreshSummary() {
  const b = await api(`/api/bootstrap?month=${state.month}`);
  $("#stat-spend").textContent = money(b.summary.spend);
  $("#stat-pending").textContent = b.total_pending;
}

// ---- shared bits -------------------------------------------------------
function lifeIndicator(t) {
  const steps = [
    { name: "入账", done: !!t.created_at, tip: t.created_at ? "入账时间 " + fmtTs(t.created_at) : "" },
    { name: "通知", done: t.notify_status === "sent",
      tip: t.notify_status === "sent" ? `已通知 ${t.notify_channel || ""} ${fmtTs(t.notified_at)}` : "尚未通知你" },
    { name: "分类", done: !!t.category,
      tip: t.category ? `已分类（来源：${({ manual: "手动", rule: "商户规则", keyword: "关键词规则", hermes: "hermes" }[t.category_src] || t.category_src || "—")}）` : "尚未分类" },
  ];
  return `<span class="life">${steps.map((s) =>
    `<span class="step ${s.done ? "done" : ""}" title="${esc(s.tip)}"><span class="dot"></span>${s.name}</span>`).join("")}</span>`;
}

function categoryOptions(selected) {
  const opts = [`<option value="" ${!selected ? "selected" : ""}>未分类</option>`]
    .concat(state.categories.map((c) =>
      `<option value="${esc(c.name)}" ${c.name === selected ? "selected" : ""}>${esc(c.name)}</option>`));
  return opts.join("");
}

// ---- view: ledger ------------------------------------------------------
async function renderLedger() {
  const f = state.ledger;
  const params = new URLSearchParams({ month: state.month, limit: 500 });
  if (f.card) params.set("card", f.card);
  if (f.status) params.set("status", f.status);
  if (f.q) params.set("q", f.q);
  const { rows } = await api(`/api/transactions?${params}`);

  const filters = `
    <div class="filters">
      <label class="month-pick"><span>月份</span>
        <select id="month-select">${state.months.map((m) => `<option value="${m}" ${m === state.month ? "selected" : ""}>${m}</option>`).join("")}</select>
      </label>
      <div class="seg" id="card-seg">
        ${seg("card", [["", "全部卡"], ["credit", "信用卡"], ["debit", "借记卡"]], f.card)}
      </div>
      <div class="seg" id="status-seg">
        ${seg("status", [["", "全部"], ["pending", "待分类"], ["confirmed", "已确认"], ["void", "已作废"]], f.status)}
      </div>
      <input type="text" id="q" placeholder="搜索商户…" value="${esc(f.q)}" />
    </div>`;

  const voidedView = f.status === "void";
  const body = rows.length ? rows.map((t) => {
    const refund = t.amount < 0;
    const act = voidedView
      ? `<button class="row-act row-restore" data-id="${t.id}" title="恢复，重新计入统计">恢复</button>`
      : `<button class="row-act row-void" data-id="${t.id}" title="不计入统计（软移除，可在「已作废」里恢复）">不计入</button>`;
    return `<tr data-id="${t.id}">
      <td><input type="checkbox" class="row-check" data-id="${t.id}" data-amt="${t.amount}" data-month="${t.txn_time.slice(0, 7)}"></td>
      <td class="t-time">${esc(t.txn_time.slice(5))}</td>
      <td><span class="card-tag ${t.card_type}">${t.card_type === "credit" ? "信用" : "借记"} ${t.card_last4}</span></td>
      <td class="num t-amount ${refund ? "refund" : ""}">${money(t.amount)}</td>
      <td class="t-merchant"><div class="raw">${esc(t.merchant_raw)}</div>${t.channel ? `<div class="chan">${esc(t.channel)}</div>` : ""}</td>
      <td><select class="cat-select ${t.category ? "" : "uncat"}" data-id="${t.id}">${categoryOptions(t.category)}</select></td>
      <td>${lifeIndicator(t)}</td>
      <td><input class="note-input" data-id="${t.id}" value="${esc(t.note || "")}" placeholder="备注…" /></td>
      <td class="t-act">${act}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="9"><div class="empty">这个条件下没有交易。</div></td></tr>`;

  $("#view").innerHTML = `${filters}
    <div class="merge-bar"><span id="lg-merge-info" class="muted">勾选 ≥2 笔可合并/抵消（可跨商户，如买后退、押金退款）</span><button class="btn" id="lg-merge-go" disabled>合并 / 抵消所选</button></div>
    <div class="panel"><table>
      <thead><tr><th></th><th>时间</th><th>卡</th><th>金额</th><th>商户</th><th>分类</th><th title="已入账 · 已通知 · 已分类">进度</th><th>备注</th><th></th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;

  const selected = new Map();
  const updateBar = () => {
    const net = [...selected.values()].reduce((a, v) => a + v.amount, 0);
    const info = $("#lg-merge-info"), btn = $("#lg-merge-go");
    if (selected.size >= 2) { info.textContent = `已选 ${selected.size} 笔，净额 ${money(net)}${Math.abs(net) < 0.01 ? "（将互相抵消）" : ""}`; btn.disabled = false; }
    else { info.textContent = "勾选 ≥2 笔可合并/抵消（可跨商户，如买后退、押金退款）"; btn.disabled = true; }
  };
  $("#view").querySelectorAll(".row-check").forEach((cb) =>
    cb.addEventListener("change", () => {
      if (cb.checked) selected.set(cb.dataset.id, { amount: parseFloat(cb.dataset.amt), month: cb.dataset.month });
      else selected.delete(cb.dataset.id);
      updateBar();
    }));
  $("#lg-merge-go").addEventListener("click", () =>
    openMergeDialog([...selected.entries()].map(([id, v]) => ({ id, ...v })), null, () => renderLedger()));

  $("#view").querySelectorAll(".cat-select").forEach((s) =>
    s.addEventListener("change", onLedgerCategory));
  $("#view").querySelectorAll(".note-input").forEach((inp) =>
    inp.addEventListener("change", async () => {
      await post(`/api/transactions/${inp.dataset.id}/note`, { note: inp.value });
      toast("已记备注");
    }));
  $("#view").querySelectorAll(".row-void").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("把这笔移出统计？（软移除，可在「已作废」筛选里恢复）")) return;
      await post(`/api/transactions/${b.dataset.id}/void`, { voided: true });
      toast("已移出统计");
      await renderLedger(); await refreshSummary();
    }));
  $("#view").querySelectorAll(".row-restore").forEach((b) =>
    b.addEventListener("click", async () => {
      await post(`/api/transactions/${b.dataset.id}/void`, { voided: false });
      toast("已恢复，重新计入统计");
      await renderLedger(); await refreshSummary();
    }));
  $("#month-select").addEventListener("change", async (e) => {
    state.month = e.target.value;
    await refreshSummary();
    renderLedger();
  });
  bindSeg("card-seg", "card");
  bindSeg("status-seg", "status");
  const q = $("#q");
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { state.ledger.q = q.value.trim(); renderLedger(); } });
}

function seg(group, items, current) {
  return items.map(([v, label]) =>
    `<button data-group="${group}" data-val="${v}" class="${v === current ? "on" : ""}">${label}</button>`).join("");
}
function bindSeg(id, key) {
  document.getElementById(id).querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { state.ledger[key] = b.dataset.val; renderLedger(); }));
}

async function onLedgerCategory(e) {
  const id = e.target.dataset.id, category = e.target.value;
  if (!category) return;
  await post(`/api/transactions/${id}/category`, { category });
  toast(`已把这笔归为「${category}」（仅此一笔；批量请用「待分类」）`);
  e.target.classList.remove("uncat");
  await refreshSummary();
}

// ---- view: pending merchants ------------------------------------------
async function renderPending() {
  const [{ rows }, status, { rows: cands }] = await Promise.all([
    api(`/api/pending_merchants`), api(`/api/classify/status`), api(`/api/merge_candidates`)]);
  const total = rows.reduce((a, r) => a + r.n, 0);
  const withAi = rows.filter((r) => r.suggested).length;
  const noAi = rows.length - withAi;
  const showBtn = rows.length > 0 || status.running;
  const head = `<div class="pending-head">
    <p class="section-title" style="margin:0">待分类商户 ${rows.length} 个 · 共 ${total} 笔 · ${withAi} 个已有 AI 建议（确认或改正即可，改正会被记住）</p>
    ${showBtn ? `<button class="btn" id="ai-suggest">${status.running ? "🟢 AI 分类中…（点看过程）" : "🪄 让 AI 评估这 " + rows.length + " 个（含重评旧建议）"}</button>` : ""}
  </div>`;
  const operationLabels = { merge: "合并", offset: "抵消", void: "不计入", split: "拆分" };
  const body = rows.length ? rows.map((r) => {
    let operationAdvice = "";
    if (operationLabels[r.suggested_operation]) {
      let ids = [];
      try { ids = JSON.parse(r.suggested_related_ids || "[]"); } catch (_) { ids = []; }
      const idText = ids.map((id) => `#${id}`).join("、");
      operationAdvice = `<div class="ms ai-tag">操作建议：${operationLabels[r.suggested_operation]}${idText ? ` ${esc(idText)}` : ""}${r.suggested_operation_reason ? ` · ${esc(r.suggested_operation_reason)}` : ""}（需确认）</div>`;
    }
    return `
    <div class="merchant-row" data-mk="${esc(r.merchant_key)}">
      <div class="m-name"><div class="mk">${esc(r.merchant_key)}</div>
        <div class="ms">例：${esc(r.sample)} · ${r.card === "credit" ? "信用卡" : "借记卡"}${r.suggested ? ` · <span class="ai-tag">AI 建议：${esc(r.suggested)}</span>` : ""}</div>
        ${operationAdvice}</div>
      <button class="m-count m-count-btn" title="查看这 ${r.n} 笔明细">${r.n} 笔</button>
      <div class="m-spend">${money(r.spend)}</div>
      <select class="cat-select">${categoryOptions(r.suggested || "")}</select>
      <button class="btn" ${r.suggested ? "" : "disabled"}>${r.suggested ? "确认" : "归类"}</button>
    </div>`; }).join("") : `<div class="empty">全部分类完成 🎉 没有待分类的商户了。</div>`;

  const candBanner = cands.length ? `
    <div class="cand-banner">
      <div class="cand-head">🔗 疑似可合并 ${cands.length} 组（同商户的支出 + 之后退款，多为押金退款/买后退）</div>
      ${cands.map((c, i) => `<div class="cand-row">
        <div class="cand-text">${esc(c.merchant_key)} · 支出 ${money(c.expense.amount)}（${c.expense.time.slice(5, 10)}）+ 退款 ${money(c.refund.amount)}（${c.refund.time.slice(5, 10)}）→ 净 <b>${money(c.net)}</b>${c.cross_month ? ' · <span class="ai-tag">跨月</span>' : ""}</div>
        <button class="btn btn-ghost cand-merge" data-i="${i}">去合并</button>
      </div>`).join("")}
    </div>` : "";

  $("#view").innerHTML = head + candBanner + `<div class="panel">${body}</div>`;
  $("#view").querySelectorAll(".cand-merge").forEach((b) =>
    b.addEventListener("click", () => {
      const c = cands[parseInt(b.dataset.i)];
      openMergeDialog([
        { id: String(c.expense.id), amount: c.expense.amount, month: c.expense.time.slice(0, 7) },
        { id: String(c.refund.id), amount: c.refund.amount, month: c.refund.time.slice(0, 7) },
      ], c.merchant_key);
    }));
  $("#view").querySelectorAll(".merchant-row").forEach((row) => {
    row.querySelector(".m-count-btn").addEventListener("click", () => openTxnModal(row.dataset.mk));
    const sel = row.querySelector("select"), btn = row.querySelector(".btn");
    sel.addEventListener("change", () => { btn.disabled = !sel.value; });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      const res = await post(`/api/merchant/category`, { merchant_key: row.dataset.mk, category: sel.value });
      toast(`「${row.dataset.mk}」→ ${sel.value}，归类了 ${res.applied} 笔`);
      row.style.opacity = ".35";
      await refreshSummary();
    });
  });

  const aiBtn = $("#ai-suggest");
  if (aiBtn) aiBtn.addEventListener("click", async () => {
    await post(`/api/classify`, {});   // starts if idle; no-op if already running
    openClassifyTerminal();
  });
}

let _termTimer;
function openClassifyTerminal() {
  closeModal();
  const ov = document.createElement("div");
  ov.className = "modal-overlay"; ov.id = "modal";
  ov.innerHTML = `<div class="modal term-modal"><div class="modal-head"><span>AI 分类 · 执行过程</span><button class="modal-close">✕</button></div><pre class="term" id="term">连接中…</pre></div>`;
  const stop = () => { closeModal(); clearInterval(_termTimer); };
  ov.addEventListener("click", (e) => { if (e.target === ov) stop(); });
  ov.querySelector(".modal-close").addEventListener("click", stop);
  document.body.appendChild(ov);
  pollTerminal();
}

function pollTerminal() {
  clearInterval(_termTimer);
  let doneHandled = false;
  const render = async () => {
    const s = await api(`/api/classify/status`);
    const term = document.getElementById("term");
    if (!term) { clearInterval(_termTimer); return; }
    const lines = (s.log || []);
    term.innerHTML = lines.length ? lines.map(termLine).join("") : (s.running ? "启动中…" : "（无输出）");
    term.scrollTop = term.scrollHeight;
    if (!s.running && !doneHandled) {
      doneHandled = true;
      clearInterval(_termTimer);
      await refreshSummary();
      if (state.view === "pending") renderPending();  // modal lives on <body>, stays open
    }
  };
  render();
  _termTimer = setInterval(render, 1500);
}

function termLine(l) {
  const t = l.trimStart();
  let cls = "tl";
  if (t.startsWith("$")) cls = "tl tl-cmd";
  else if (l.startsWith("[")) cls = "tl tl-step";
  else if (t.startsWith("←") || t.startsWith("⟳") || t.startsWith("→")) cls = "tl tl-out";
  else if (t.startsWith("✓")) cls = "tl tl-ok";
  else if (t.startsWith("✗")) cls = "tl tl-err";
  else if (l.startsWith("#")) cls = "tl tl-dim";
  return `<span class="${cls}">${esc(l) || " "}</span>`;
}

// ---- transaction detail modal -----------------------------------------
async function openTxnModal(mk) {
  const { rows } = await api(`/api/transactions?merchant_key=${encodeURIComponent(mk)}&limit=500`);
  const sum = rows.reduce((a, t) => a + t.amount, 0);
  const body = rows.map((t) => `<tr data-id="${t.id}">
      <td><input type="checkbox" class="row-check" data-id="${t.id}" data-amt="${t.amount}" data-month="${t.txn_time.slice(0, 7)}"></td>
      <td class="t-time">${esc(t.txn_time)}</td>
      <td><span class="card-tag ${t.card_type}">${t.card_type === "credit" ? "信用" : "借记"} ${t.card_last4}</span></td>
      <td class="num t-amount ${t.amount < 0 ? "refund" : ""}">${money(t.amount)}</td>
      <td class="muted">${esc(t.channel || "—")}</td>
      <td><select class="cat-select txn-cat ${t.category ? "" : "uncat"}">${categoryOptions(t.category)}</select></td>
      <td><input class="note-input" data-id="${t.id}" value="${esc(t.note || "")}" placeholder="备注…" /></td>
      <td><button class="link-del split-btn" data-id="${t.id}" data-amt="${t.amount}">拆分</button></td>
    </tr>`).join("");
  const pendingN = rows.filter((t) => t.status === "pending" || t.status === "suggested").length;
  showModal(`${esc(mk)} · 共 ${rows.length} 笔${pendingN < rows.length ? `（待处理 ${pendingN}）` : ""} · 合计 ${money(sum)} · 逐笔改分类/记备注/拆分/合并`,
    `<div class="merge-bar"><span id="merge-info" class="muted">勾选 ≥2 笔可合并（如押金+退款）</span><button class="btn" id="merge-go" disabled>合并所选</button></div>
     <div class="panel"><table>
      <thead><tr><th></th><th>时间</th><th>卡</th><th>金额</th><th>渠道</th><th>分类（可逐笔改）</th><th>备注</th><th></th></tr></thead>
      <tbody>${body}</tbody></table></div>`, "wide-modal");

  const selected = new Map();
  const updateMergeBar = () => {
    const net = [...selected.values()].reduce((a, v) => a + v.amount, 0);
    const info = document.getElementById("merge-info");
    const btn = document.getElementById("merge-go");
    if (selected.size >= 2) { info.textContent = `已选 ${selected.size} 笔，净额 ${money(net)}`; btn.disabled = false; }
    else { info.textContent = "勾选 ≥2 笔可合并（如押金+退款）"; btn.disabled = true; }
  };
  document.querySelectorAll("#modal .row-check").forEach((cb) =>
    cb.addEventListener("change", () => {
      if (cb.checked) selected.set(cb.dataset.id, { amount: parseFloat(cb.dataset.amt), month: cb.dataset.month });
      else selected.delete(cb.dataset.id);
      updateMergeBar();
    }));
  document.getElementById("merge-go").addEventListener("click", () =>
    openMergeDialog([...selected.entries()].map(([id, v]) => ({ id, ...v })), mk));

  document.querySelectorAll("#modal .split-btn").forEach((b) =>
    b.addEventListener("click", () => openSplitDialog(b.dataset.id, Math.abs(parseFloat(b.dataset.amt)), mk)));
  document.querySelectorAll("#modal .txn-cat").forEach((sel) => {
    sel.addEventListener("change", async () => {
      if (!sel.value) return;
      const id = sel.closest("tr").dataset.id;
      await post(`/api/transactions/${id}/category`, { category: sel.value });
      sel.classList.remove("uncat");
      toast(`#${id} → 「${sel.value}」（仅此一笔）`);
      await refreshSummary();
      if (state.view === "pending") renderPending();  // keep list behind the modal in sync
    });
  });
  document.querySelectorAll("#modal .note-input").forEach((inp) =>
    inp.addEventListener("change", async () => {
      await post(`/api/transactions/${inp.dataset.id}/note`, { note: inp.value });
      toast("已记备注");
    }));
}

function openMergeDialog(items, mk, onDone) {
  const net = items.reduce((a, it) => a + it.amount, 0);
  const isOffset = Math.abs(net) < 0.01;
  const months = [...new Set(items.map((i) => i.month))].sort();
  const monthRow = (!isOffset && months.length > 1)
    ? `<label class="mg-row">归属月份 <select id="mg-month">${months.map((m) => `<option>${m}</option>`).join("")}</select></label>`
    : "";
  const formExtra = isOffset ? "" : `
      ${monthRow}
      <label class="mg-row">分类 <select id="mg-cat"><option value="">不指定（留待分类）</option>${state.categories.map((c) => `<option>${esc(c.name)}</option>`).join("")}</select></label>
      <input id="mg-note" placeholder="备注，如 住院押金（已退部分）" />`;
  showModal(isOffset ? `抵消 ${items.length} 笔 · 净额 ¥0.00` : `合并 ${items.length} 笔 · 净额 ¥${net.toFixed(2)}`, `
    <div class="split-form">
      <p>${isOffset
        ? `这 ${items.length} 笔净额为 0，将<b>互相抵消并作废</b>（适合买后全额退、跨商户也可）。`
        : `这 ${items.length} 笔会合并成 1 笔净额 <b>¥${net.toFixed(2)}</b>，原始几笔作废。${months.length > 1 ? "它们跨月，请选算在哪个月。" : ""}`}</p>
      ${formExtra}
      <button class="btn" id="mg-go">${isOffset ? "确认抵消" : "确认合并"}</button>
    </div>`);
  document.getElementById("mg-go").addEventListener("click", async () => {
    const r = await post(`/api/transactions/merge`, {
      ids: items.map((i) => i.id),
      category: isOffset ? "" : (document.getElementById("mg-cat").value),
      note: isOffset ? "" : (document.getElementById("mg-note").value),
      month: (!isOffset && months.length > 1) ? document.getElementById("mg-month").value : months[0],
    });
    if (!r.ok) { toast(r.error || "操作失败"); return; }
    toast(r.offset ? `已抵消 ${r.count} 笔（净额 0）` : `已合并 ${r.count} 笔 → 净额 ${money(r.net)}（归属 ${r.month}）`);
    await refreshSummary();
    if (onDone) onDone(); else openTxnModal(mk);
  });
}

function openSplitDialog(txnId, amount, mk) {
  showModal(`拆分 #${txnId} · 原额 ¥${amount.toFixed(2)}`, `
    <div class="split-form">
      <p>把这笔拆成几份，金额用空格或逗号分隔，合计需等于原额。拆出的份额里，若有与某笔退款同额同商户的，会自动抵消。</p>
      <input id="split-input" placeholder="例如 906 88.97" value="${amount.toFixed(2)}" />
      <div class="split-hint" id="split-hint"></div>
      <button class="btn" id="split-go">确认拆分</button>
    </div>`);
  const inp = document.getElementById("split-input");
  const hint = document.getElementById("split-hint");
  const parse = () => inp.value.split(/[\s,，]+/).filter(Boolean).map(Number);
  const refresh = () => {
    const parts = parse();
    const s = parts.reduce((a, b) => a + (b || 0), 0);
    const ok = parts.length >= 2 && parts.every((x) => x > 0) && Math.abs(s - amount) < 0.01;
    hint.textContent = `${parts.length} 份，合计 ${s.toFixed(2)} / 需 ${amount.toFixed(2)}${ok ? " ✓" : ""}`;
    hint.className = "split-hint " + (ok ? "ok" : "bad");
  };
  inp.addEventListener("input", refresh);
  refresh();
  document.getElementById("split-go").addEventListener("click", async () => {
    const r = await post(`/api/transactions/${txnId}/split`, { amounts: parse() });
    if (!r.ok) { toast(r.error || "拆分失败"); return; }
    toast(`已拆成 ${r.parts} 笔${r.offset_pairs ? `，自动抵消 ${r.offset_pairs} 对` : ""}`);
    await refreshSummary();
    openTxnModal(mk);
  });
}

function showModal(title, html, extraClass = "") {
  closeModal();
  const ov = document.createElement("div");
  ov.className = "modal-overlay"; ov.id = "modal";
  ov.innerHTML = `<div class="modal ${extraClass}"><div class="modal-head"><span>${title}</span><button class="modal-close">✕</button></div><div class="modal-body">${html}</div></div>`;
  ov.addEventListener("click", (e) => { if (e.target === ov) closeModal(); });
  ov.querySelector(".modal-close").addEventListener("click", closeModal);
  document.body.appendChild(ov);
}
function closeModal() { const m = document.getElementById("modal"); if (m) m.remove(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// The billing-cycle bar that sits atop the monthly view: states the rule like
// a statement header, and lets you set the start day inline (saves live).
function cycleBar() {
  const day = state.billingStartDay || 1;
  const opts = Array.from({ length: 28 }, (_, i) => i + 1)
    .map((d) => `<option value="${d}" ${d === day ? "selected" : ""}>${d}</option>`).join("");
  const rule = day === 1
    ? "自然月 · 1 日 – 月底"
    : `每期 ${day} 日 – 次月 ${day - 1} 日`;
  return `<div class="cycle-bar">
    <span class="cycle-eyebrow">账单周期</span>
    <span class="cycle-set">每月 <select id="cycle-day">${opts}</select> 日起算</span>
    <span class="cycle-rule">${rule}</span>
  </div>`;
}

// ---- view: stats (月度/年度) -------------------------------------------
// A category-spend bar list grouped by period; identical for both modes, so it's
// built once here and the toggle just swaps the data source + grouping.
function _statCard(list, periodAttr) {
  const catName = (c) => (c === "__uncat__" ? "未分类" : c);
  const sorted = list.slice().sort((a, b) => b.spend - a.spend);
  const total = sorted.reduce((a, c) => a + (c.spend || 0), 0) || 1;
  const rows = sorted.filter((c) => c.spend > 0).map((c) => {
    const pct = (c.spend / total) * 100, uncat = c.c === "__uncat__";
    return `<div class="bar-row clickable" ${periodAttr} data-c="${esc(c.c)}" data-cn="${esc(catName(c.c))}" title="点击查看明细">
      <div class="cat">${esc(catName(c.c))}</div>
      <div class="bar-track"><div class="bar-fill ${uncat ? "uncat" : ""}" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="bar-amt">${money(c.spend)}<span class="pct">${pct.toFixed(0)}%</span></div>
    </div>`;
  }).join("");
  return { total, rows };
}

async function renderStats() {
  const mode = state.statsMode === "annual" ? "annual" : "monthly";
  const seg = `<div class="seg stats-seg">
    <button data-mode="monthly"${mode === "monthly" ? ' class="on"' : ""}>月度</button>
    <button data-mode="annual"${mode === "annual" ? ' class="on"' : ""}>年度</button>
  </div>`;

  let body;
  if (mode === "monthly") {
    const { cells } = await api(`/api/monthly`);
    const byMonth = {};
    cells.forEach((c) => { (byMonth[c.m] = byMonth[c.m] || []).push(c); });
    // Only the latest month is expanded; older months collapse (native <details>).
    const cards = Object.keys(byMonth).sort().reverse().map((m, i) => {
      const { total, rows } = _statCard(byMonth[m], `data-m="${esc(m)}"`);
      return `<details class="panel month-card"${i === 0 ? " open" : ""}>
        <summary><span class="cyc-head">${m}<i class="cyc-range">${cycleRange(m)}</i></span><span class="total">${money(total)}</span></summary>${rows}</details>`;
    }).join("");
    body = cycleBar() + (cards || `<div class="empty">还没有数据。</div>`);
  } else {
    const { cells } = await api(`/api/annual`);
    const byYear = {};
    cells.forEach((c) => { (byYear[c.y] = byYear[c.y] || []).push(c); });
    const cards = Object.keys(byYear).sort().reverse().map((y) => {
      const { total, rows } = _statCard(byYear[y], `data-y="${esc(y)}"`);
      return `<div class="panel month-card"><h3>${y} 年<span class="total">${money(total)}</span></h3>${rows}</div>`;
    }).join("");
    body = cards || `<div class="empty">还没有 2026 年起的数据。</div>`;
  }

  $("#view").innerHTML = `<div class="stats-head" style="margin-bottom:16px">${seg}</div>` + body;

  $("#view").querySelectorAll(".stats-seg button").forEach((b) =>
    b.addEventListener("click", () => { state.statsMode = b.dataset.mode; renderStats(); }));

  $("#view").querySelectorAll(".bar-row.clickable").forEach((row) =>
    row.addEventListener("click", () => row.dataset.m
      ? openCategoryModal(`month=${row.dataset.m}`, monthWithRange(row.dataset.m), row.dataset.c, row.dataset.cn)
      : openCategoryModal(`year=${row.dataset.y}`, `${row.dataset.y} 年`, row.dataset.c, row.dataset.cn)));

  const daySel = $("#cycle-day");
  if (daySel) daySel.addEventListener("change", async (e) => {
    const r = await post("/api/settings/billing_start_day", { day: Number(e.target.value) });
    state.billingStartDay = r.billing_start_day;
    toast(`账单周期改为每月 ${r.billing_start_day} 日起`);
    await renderStats();      // re-bucket cards + ranges under the new cycle
    await refreshSummary();   // sidebar "本月支出" follows the new cycle too
  });
}

// ---- view: income (进项) ----------------------------------------------
async function renderIncome() {
  const { rows } = await api(`/api/deposits`);
  if (!rows || !rows.length) {
    $("#view").innerHTML = `<div class="empty">还没有进项记录。AI 会从招行一卡通邮件里自动识别工资/报销/汇款/理财到账等。</div>`;
    return;
  }
  const byMonth = {};
  rows.forEach((r) => { const m = r.txn_time.slice(0, 7); (byMonth[m] = byMonth[m] || []).push(r); });
  const months = Object.keys(byMonth).sort().reverse();

  $("#view").innerHTML = months.map((m, i) => {
    const list = byMonth[m];
    const total = list.reduce((a, r) => a + (r.amount || 0), 0);
    const lines = list.map((r) => `
      <div class="inc-row"${r.raw_text ? ` data-tip="${esc(r.raw_text)}"` : ""}>
        <div class="inc-time">${esc(r.txn_time.slice(5))}</div>
        <div class="inc-main"><div class="inc-kind">${esc(r.kind || "进项")}${r.payer ? ` · ${esc(r.payer)}` : ""}</div>${r.note ? `<div class="inc-note">${esc(r.note)}</div>` : ""}</div>
        <div class="inc-amt">${money(r.amount)}</div>
      </div>`).join("");
    return `<details class="panel month-card"${i === 0 ? " open" : ""}>
      <summary><span class="cyc-head">${m}<i class="cyc-range">${list.length} 笔</i></span><span class="total">${money(total)}</span></summary>${lines}</details>`;
  }).join("");
  attachTips($("#view"));
}

async function openCategoryModal(scopeQS, scopeLabel, rawCat, displayCat) {
  const url = `/api/transactions?${scopeQS}&category=${encodeURIComponent(rawCat)}&limit=500`;
  let rows = (await api(url)).rows;
  let sortKey = "amount";  // "amount" | "time"
  let desc = true;
  const render = () => {
    rows.sort((a, b) => {
      const av = sortKey === "time" ? a.txn_time : a.amount;
      const bv = sortKey === "time" ? b.txn_time : b.amount;
      const d = av < bv ? -1 : av > bv ? 1 : 0;
      return desc ? -d : d;
    });
    const sum = rows.reduce((a, t) => a + t.amount, 0);
    const head = (k, label, extra = "") => {
      const ind = sortKey === k ? (desc ? "↓" : "↑") : "↕";
      return `<th class="sortable ${extra} ${sortKey === k ? "is-active" : ""}" id="sort-${k}">${label}<span class="sort-ind">${ind}</span></th>`;
    };
    const body = rows.map((t) => `<tr data-id="${t.id}">
        <td class="t-time">${esc(t.txn_time)}</td>
        <td><span class="card-tag ${t.card_type}">${t.card_type === "credit" ? "信用" : "借记"} ${t.card_last4}</span></td>
        <td class="num t-amount ${t.amount < 0 ? "refund" : ""}">${money(t.amount)}</td>
        <td class="muted">${esc(t.channel || "—")}</td>
        <td>${esc(t.merchant_raw)}</td>
        <td><select class="cat-select txn-cat" data-id="${t.id}">${categoryOptions(t.category)}</select></td>
        <td><input class="note-input" data-id="${t.id}" value="${esc(t.note || "")}" placeholder="备注…" /></td>
      </tr>`).join("");
    showModal(`${esc(displayCat)} · ${esc(scopeLabel)} · ${rows.length} 笔 · 合计 ${money(sum)}`, `
      <div class="panel"><table>
        <thead><tr>${head("time", "时间")}<th>卡</th>${head("amount", "金额", "num")}<th>渠道</th><th>商户</th><th>分类（可改）</th><th>备注</th></tr></thead>
        <tbody>${body}</tbody></table></div>`, "wide-modal");
    const onSort = (k) => () => { if (sortKey === k) desc = !desc; else { sortKey = k; desc = true; } render(); };
    document.getElementById("sort-amount").addEventListener("click", onSort("amount"));
    document.getElementById("sort-time").addEventListener("click", onSort("time"));
    document.querySelectorAll("#modal .txn-cat").forEach((sel) =>
      sel.addEventListener("change", async () => {
        if (!sel.value) return;
        await post(`/api/transactions/${sel.dataset.id}/category`, { category: sel.value });
        toast(`#${sel.dataset.id} → 「${sel.value}」`);
        rows = (await api(url)).rows;  // item may leave this category
        await refreshSummary();
        render();
      }));
    document.querySelectorAll("#modal .note-input").forEach((inp) =>
      inp.addEventListener("change", async () => {
        await post(`/api/transactions/${inp.dataset.id}/note`, { note: inp.value });
        toast("已记备注");
      }));
  };
  render();
}

// ---- view: knowledge ---------------------------------------------------
async function renderKnowledge() {
  const [{ rows: kn }, { text: context }, boot] = await Promise.all([
    api(`/api/knowledge`), api(`/api/context`), api(`/api/bootstrap?month=`)]);
  const stats = boot.category_stats || {};
  const catOptions = state.categories.map((c) => `<option>${esc(c.name)}</option>`).join("");

  // This page is about the classification system, not stats — show only name +
  // description. (Per-category counts come from `stats` when gating deletion.)
  const tiles = state.categories.map((c) =>
    `<button class="coa-tile" data-key="${esc(c.key)}" title="点击编辑">
      <div class="coa-top"><span class="coa-name">${esc(c.name)}</span></div>
      <div class="coa-desc">${c.descr ? esc(c.descr) : '<span class="coa-desc-empty">未填说明 — 点此补上，AI 会更准</span>'}</div>
    </button>`).join("");

  const memRows = kn.length ? kn.map((k) =>
    `<tr><td>${esc(k.text)}${k.category ? " → " + esc(k.category) : ""}</td><td class="num mem-actions">` +
    `<button class="link-edit" data-kid="${k.id}">编辑</button>` +
    `<button class="link-del" data-type="kn" data-kid="${k.id}">忘掉</button></td></tr>`).join("")
    : `<tr><td colspan="2"><div class="empty">还没有记忆。在上面教它一句即可。</div></td></tr>`;

  $("#view").innerHTML = `
    <p class="kn-intro">这一页是 <b>AI 分类的依据</b>：只有你在这里教的知识会进记忆、喂给 AI。在「待分类」确认只是一次性的、<b>不进记忆</b>；持续打磨记忆，AI 会越来越准。</p>

    <p class="section-title">① 分类体系 <span class="section-hint">点卡片改名/改描述；空分类可删除</span></p>
    <div class="coa-grid">${tiles}
      <button class="coa-tile coa-add" id="coa-add"><span>＋ 新分类</span></button>
    </div>

    <p class="section-title" style="margin-top:24px">② AI 的记忆（你教过它的事，一句话即可 — 都是分类依据）</p>
    <div class="panel kn-add mem-form">
      <input type="text" id="mem-content" placeholder="教 AI 一句话，如：盒马算吃吃喝喝 / 商户名含拉扎斯的都算吃吃喝喝 / 给人名转账多是人情" />
      <span class="kw-lead">归</span>
      <select id="mem-cat"><option value="">不指定分类</option>${catOptions}</select>
      <button class="btn" id="mem-add">记住</button>
    </div>
    <details class="cat-fold" style="margin-top:14px" ${kn.length ? "open" : ""}>
      <summary>已教 ${kn.length} 条记忆（点击折叠/展开）</summary>
      <div class="panel" style="margin-top:10px"><table><tbody>${memRows}</tbody></table></div>
    </details>

    <details class="cat-fold" style="margin-top:24px">
      <summary>③ 预览 AI 看到的完整 context（点击展开）</summary>
      <pre class="ctx-preview">${esc(context)}</pre>
    </details>`;

  $("#view").querySelectorAll(".coa-tile[data-key]").forEach((tile) =>
    tile.addEventListener("click", () => {
      const c = state.categories.find((x) => x.key === tile.dataset.key);
      openCategoryEdit(c, stats[c.name] ? stats[c.name].n : 0);
    }));
  $("#coa-add").addEventListener("click", () => openCategoryEdit(null, 0));
  $("#view").querySelectorAll(".link-del").forEach((b) => {
    b.addEventListener("click", async () => {
      await post(`/api/knowledge/delete`, { id: b.dataset.kid });
      toast("已忘掉");
      renderKnowledge();
    });
  });
  $("#view").querySelectorAll(".link-edit").forEach((b) =>
    b.addEventListener("click", () =>
      openKnowledgeEdit(kn.find((k) => String(k.id) === b.dataset.kid))));

  $("#mem-add").addEventListener("click", async () => {
    const content = $("#mem-content").value.trim();
    const cat = $("#mem-cat").value;
    if (!content) { toast("教它一句话"); return; }
    await post(`/api/knowledge`, { scope: "global", text: content, category: cat || null });
    toast("已记住");
    renderKnowledge();
  });
}

function openKnowledgeEdit(k) {
  if (!k) return;
  const opts = `<option value="">不指定分类</option>` +
    state.categories.map((c) => `<option ${c.name === k.category ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  showModal("编辑记忆", `
    <div class="coa-edit">
      <label class="coa-field"><span>教 AI 的话</span>
        <textarea id="kn-text" rows="3">${esc(k.text)}</textarea></label>
      <label class="coa-field"><span>归到分类（可不指定）</span>
        <select id="kn-cat">${opts}</select></label>
      <div class="coa-edit-foot"><button class="btn" id="kn-do">保存</button></div>
    </div>`, "edit-modal");
  document.getElementById("kn-do").addEventListener("click", async () => {
    const text = document.getElementById("kn-text").value.trim();
    const category = document.getElementById("kn-cat").value || null;
    if (!text) { toast("内容不能为空"); return; }
    const r = await post(`/api/knowledge/update`, { id: k.id, text, category });
    if (r && r.ok === false) { toast(r.error || "保存失败"); return; }
    toast("记忆已更新");
    closeModal();
    renderKnowledge();
  });
}

function openCategoryEdit(cat, n = 0) {
  const isNew = !cat;
  const delBtn = (!isNew && n === 0)
    ? `<button class="btn-danger" id="ce-del">删除分类</button>`
    : "";
  showModal(isNew ? "新建分类" : `编辑「${esc(cat.name)}」`, `
    <div class="coa-edit">
      <label class="coa-field"><span>分类名</span>
        <input id="ce-name" value="${isNew ? "" : esc(cat.name)}" placeholder="如：吃吃喝喝" autofocus /></label>
      <label class="coa-field"><span>这一类包含什么 · 这就是 AI 判断的依据</span>
        <textarea id="ce-descr" rows="3" placeholder="如：外食、外卖、盒马、山姆、天猫/淘宝及各类网购食品">${isNew ? "" : esc(cat.descr || "")}</textarea></label>
      <div class="coa-edit-foot">${delBtn}<button class="btn" id="ce-do">${isNew ? "添加" : "保存"}</button></div>
      ${(!isNew && n > 0) ? `<p class="coa-edit-note">已有 ${n} 笔交易归在此类，不能删除；先把它们改到别的分类。</p>` : ""}
    </div>`, "edit-modal");
  document.getElementById("ce-do").addEventListener("click", async () => {
    const name = document.getElementById("ce-name").value.trim();
    const descr = document.getElementById("ce-descr").value;
    if (!name) { toast("填个分类名"); return; }
    const r = isNew
      ? await post(`/api/categories/add`, { name, descr })
      : await post(`/api/categories/update`, { key: cat.key, name, descr });
    if (r && r.ok === false) { toast(r.error || "保存失败"); return; }
    toast(isNew ? `已添加「${name}」` : "分类已更新");
    closeModal();
    await bootstrap();
    renderKnowledge();
  });
  const del = document.getElementById("ce-del");
  if (del) del.addEventListener("click", async () => {
    if (!confirm(`删除分类「${cat.name}」？此操作不可撤销。`)) return;
    const r = await post(`/api/categories/delete`, { key: cat.key });
    if (r && r.ok === false) { toast(r.error || "删除失败"); return; }
    toast(`已删除「${cat.name}」`);
    closeModal();
    await bootstrap();
    renderKnowledge();
  });
}

// ---- view: config / 设置 ----------------------------------------------
// A health desk for the ledger's intake. Two source "pipelines" (debit / credit)
// each carry a 方式→授权→连通 rail and a status chop (印章); two ledger cards cover
// how spending is booked and how income is recognized. The page self-diagnoses
// on open by running both connectivity checks.
function _railNode(step, val, state, lead) {
  const cls = ["rail-node", state, lead === "on" ? "lead-on" : lead === "off" ? "lead-off" : ""]
    .filter(Boolean).join(" ");
  return `<div class="${cls}"><span class="rail-dot"></span>` +
    `<span class="rail-step">${step}</span><span class="rail-val">${val}</span></div>`;
}

// In-page mail authorization wizard. Provider-specific links help the user
// create an app password / authorization code; "保存并连接" validates via IMAP.
function _mailHelp(provider) {
  const help = {
    gmail: {
      label: "Google 应用专用密码",
      url: "https://myaccount.google.com/apppasswords",
      cta: "打开 Google 应用专用密码 ↗",
      hint: "16 位应用专用密码",
      placeholder: "粘贴 16 位应用专用密码",
    },
    qq: {
      label: "QQ 邮箱授权码",
      url: "https://help.mail.qq.com/detail/0/985",
      cta: "打开 QQ 邮箱授权说明 ↗",
      hint: "IMAP/SMTP 授权码",
      placeholder: "粘贴 QQ 生成的授权码",
      path: "登录 QQ 邮箱后：右上角头像 → 设置 → 账号与安全 → 安全设置 → 开启服务 → 生成授权码。",
    },
    "163": {
      label: "163 邮箱授权码",
      url: "https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac2f4d4bd43fa3aaee0",
      hint: "客户端授权码",
      placeholder: "粘贴 163 新增的授权密码",
      cta: "打开 163 邮箱授权说明 ↗",
      path: "登录 163 邮箱后：设置 → POP3/SMTP/IMAP → 新增授权密码。也可在网易邮箱大师：我 → 邮箱管理 → 第三方登录管理 → 通用授权码 → 新增授权码。",
    },
  };
  return help[provider] || help.gmail;
}

function _mailWizard(cfg) {
  const link = (href, label) => `<a class="aw-go" href="${href}" target="_blank" rel="noopener">${label}</a>`;
  const providers = cfg.mail_providers || [{ key: "gmail", label: "Gmail" }];
  const configuredProvider = cfg.mail_provider || "gmail";
  const active = state.mailProviderDraft || configuredProvider;
  const help = _mailHelp(active);
  const configuredEmail = cfg.mail_email || cfg.gmail_email || "";
  const email = state.mailEmailDraftProvider === active
    ? state.mailEmailDraft
    : (active === configuredProvider ? configuredEmail : "");
  const opts = providers.map((p) =>
    `<option value="${esc(p.key)}" ${p.key === active ? "selected" : ""}>${esc(p.label)}</option>`).join("");
  const cta = help.cta || "去查看 ↗";
  const proxyDraft = state.mailProxyDraftProvider === active;
  const proxyEnabled = proxyDraft
    ? state.mailProxyEnabledDraft
    : (active === configuredProvider ? !!cfg.mail_proxy_enabled : false);
  const proxyHost = proxyDraft
    ? state.mailProxyHostDraft
    : (active === configuredProvider ? (cfg.mail_proxy_host || "127.0.0.1") : "127.0.0.1");
  const proxyPort = proxyDraft
    ? state.mailProxyPortDraft
    : (active === configuredProvider ? (cfg.mail_proxy_port || 8118) : 8118);
  const proxyRow = active === "gmail" ? `<div class="aw-row"><div class="aw-main">
      <label class="aw-check"><input id="aw-proxy-enabled" type="checkbox" ${proxyEnabled ? "checked" : ""}>
        <b>网络代理</b><span class="sub">（仅 Gmail 可选）</span></label>
      <div class="sub">只影响 Gmail 邮箱连接；QQ、163 邮箱始终直连。</div>
      <div class="aw-proxy-fields" ${proxyEnabled ? "" : "hidden"}>
        <input id="aw-proxy-host" class="aw-input" type="text" placeholder="127.0.0.1" value="${esc(proxyHost)}">
        <input id="aw-proxy-port" class="aw-input aw-port" type="number" min="1" max="65535" placeholder="8118" value="${esc(proxyPort)}">
      </div>
    </div></div>` : "";
  return `<div class="aw">
    <div class="aw-row"><div class="aw-main"><b>选择接收招行邮件的邮箱类型</b>
      <select id="aw-provider" class="aw-input">${opts}</select></div></div>
    <div class="aw-row"><div class="aw-main"><b>填写邮箱地址</b>
      <input id="aw-email" class="aw-input" type="email" placeholder="your@example.com" value="${esc(email)}"></div></div>
    <div class="aw-row"><div class="aw-main">生成该邮箱的<b>${esc(help.label)}</b>
      ${help.path ? `<div class="sub">${esc(help.path)}</div>` : ""}</div>
      ${link(help.url, cta)}</div>
    ${proxyRow}
    <div class="aw-row"><div class="aw-main"><b>粘贴密码并连接</b>
      <div class="aw-save"><input id="aw-pw" class="aw-input" type="password" placeholder="${esc(help.placeholder)}" autocomplete="off">
        <button class="btn" id="aw-save">保存并连接</button></div>
      <div class="sub">这里填的是${esc(help.hint)}，不是邮箱登录密码。</div>
      <div id="aw-out" class="aw-out"></div></div></div>
  </div>
  <div class="aw-foot">密码只存在本机 <span class="num">secrets/mail_auth.json</span>（git 忽略），不外发。</div>`;
}

function _mailEditing() {
  return !!state.mailProviderDraft || !!state.mailEmailDraftProvider;
}

function _mailSourceRow(c) {
  const recheck = c.canCheck
    ? `<button class="btn btn-ghost" data-recheck style="padding:5px 12px;font-size:12.5px">重新自检</button>`
    : "";
  return `<div class="mail-source-row" data-pipe="${c.cardType}">
    <div class="mail-source-main">
      <div class="mail-source-name">${esc(c.sourceName)}</div>
      <div class="mail-source-sub">${esc(c.sourceUse)}</div>
      <div class="mail-source-recent">最近一笔 <b>${c.fresh || "—"}</b></div>
      ${c.reminderTip ? `<details class="guide">
        <summary>${c.reminderTip.summary}</summary>
        <div class="guide-body">${c.reminderTip.body}</div>
      </details>` : ""}
    </div>
    <div class="mail-source-health">
      <div class="intake-rail" data-rail>
        ${_railNode("① 方式", c.method, "done", "on")}
        ${_railNode("② 授权", c.auth.val, c.auth.state, c.auth.lead)}
        ${_railNode("③ 连通", c.conn.val, c.conn.state, "")}
      </div>
      <div class="pipe-foot">${recheck}<span class="check-out" data-out>${c.outInit || ""}</span></div>
    </div>
  </div>`;
}

function _mailCollectionCard(cfg, debit, credit) {
  const ready = !!cfg.mail_configured || !!cfg.gmail_configured;
  const providerLabel = cfg.mail_provider_label || "Gmail";
  const email = cfg.mail_email || cfg.gmail_email || "未配置";
  const editing = _mailEditing();
  const stateLine = ready
    ? `当前生效：${esc(providerLabel)}<span class="sub"> · ${esc(email)}</span>`
    : `<span style="color:var(--red)">未授权</span>`;
  const editLine = editing
    ? `<div class="check-out">正在配置新邮箱，保存成功后生效；当前自检仍以已保存邮箱为准。</div>`
    : "";
  return `<div class="pipe mail-collection" data-mail-collection>
    <div class="pipe-head">
      <div class="pipe-title"><div class="pipe-name">招商银行邮件采集</div>
        <div class="pipe-sub">一个邮箱账号接收招行通知邮件，Plutus 自动识别借记卡、信用卡和进项。</div></div>
    </div>
    <div class="mail-account-strip">
      <div><span class="ledger-k">邮箱账号</span><span class="mail-current">${stateLine}</span></div>
      ${editLine}
    </div>
    <details class="guide" ${ready && !editing ? "" : "open"}>
      <summary>${ready ? "更换邮箱账号 / 重新授权" : "如何授权 · 3 步页内完成"}</summary>
      <div class="guide-body">${_mailWizard(cfg)}</div>
    </details>
    <div class="mail-source-list">
      ${_mailSourceRow(debit)}
      ${_mailSourceRow(credit)}
    </div>
  </div>`;
}

function _mailAuthHint(err, provider) {
  const e = String(err);
  if (/AUTHENTICATIONFAILED|Invalid credentials|credential/i.test(e))
    return `认证失败：请确认填的是${_mailHelp(provider).hint}（不是登录密码）、邮箱无误，且该邮箱已开启 IMAP。`;
  return e;
}

// Build one email intake card. A card type without an email parser degrades to
// a pending state rather than pretending that collection is active.
function _intakeCard(cardType, name, cfg) {
  const support = cfg.channel_support[cardType] || { email: false };
  const fresh = cardType === "credit" ? cfg.last_credit_txn : cfg.last_debit_txn;
  const method = "邮箱";
  const reminderTip = cardType === "debit"
    ? {
        summary: "如何开通邮箱提醒？",
        body: "招商银行 App → 全部 → 设置 → 银行卡 → 更多功能 → 招行短信服务 → " +
          "账户变动通知 → <b>Email 通知（免费）</b>",
      }
    : {
        summary: "如何开通每日信用管家？",
        body: "掌上生活 App → 我的 → 设置 / 设置与资料管理 → 通知管理 → 订阅管理 → " +
          "<b>每日信用管家</b>",
      };
  const base = { cardType, name, method, fresh, endpoint: "/api/config/check",
                 canCheck: !!support.email, reminderTip };

  if (!support.email) {
    return Object.assign(base, {
      sourceName: cardType === "credit" ? "每日信用管家" : "一卡通账户变动通知",
      sourceUse: cardType === "credit" ? "用于信用卡消费识别" : "用于借记卡消费、工资/报销/汇款/理财到账识别",
      sub: "邮箱 · 暂无解析器",
      seal: { cls: "warn", ch: "待" },
      auth: { val: "暂无解析器", state: "warn", lead: "off" },
      conn: { val: "—", state: "pending" }, autoRun: false,
      outInit: "该银行的邮箱通知暂无解析器",
      guideOpen: true, guideSummary: "为什么暂无解析器？",
      guide: `邮箱需为具体银行格式注册解析器；当前无法采集「${name}」通知。`,
    });
  }
  const ready = !!cfg.mail_configured || !!cfg.gmail_configured;
  const providerLabel = cfg.mail_provider_label || "Gmail";
  const email = cfg.mail_email || cfg.gmail_email || "未配置";
  const editing = _mailEditing();
  return Object.assign(base, {
    sourceName: cardType === "credit" ? "每日信用管家" : "一卡通账户变动通知",
    sourceUse: cardType === "credit" ? "用于信用卡消费识别" : "用于借记卡消费、工资/报销/汇款/理财到账识别",
    sub: `${cardType === "credit" ? "每日信用管家" : "一卡通账户变动通知"} · ${esc(providerLabel)}` +
      `<span class="sub"> · ${esc(email)}</span>`,
    seal: ready && !editing ? { cls: "checking", ch: "检" } : { cls: "warn", ch: "待" },
    auth: ready ? { val: "邮箱授权码", state: "checking", lead: "on" }
                : { val: "未授权", state: "warn", lead: "off" },
    conn: ready ? (editing ? { val: "待保存", state: "pending" } : { val: "检测中…", state: "checking" })
                : { val: "待授权", state: "pending" },
    autoRun: ready && !editing,
  });
}

async function renderConfig() {
  const cfg = await api(`/api/config`);
  const mono = (s) => `<span class="num">${esc(s)}</span>`;
  const debit = _intakeCard("debit", "借记卡", cfg);
  const credit = _intakeCard("credit", "信用卡", cfg);

  const notifyTxt = cfg.notify_configured
    ? `${esc(cfg.notify_channel)} · 已推 ${mono(cfg.notify_sent)} 笔 <span class="sub">· 最近 ${fmtTs(cfg.notify_last) || "—"}</span>`
    : `<span style="color:var(--red)">未配置</span>`;

  $("#view").innerHTML = `
    <div class="set-head"><h1>设置</h1>
      <span class="set-thesis">钱从哪来，又怎么记 —— 四处配齐，流水才进得对。</span></div>

    <div class="set-group">进料 · 钱从哪来</div>
    <div class="intake-grid mail-intake-grid">${_mailCollectionCard(cfg, debit, credit)}</div>

    <div class="set-group">记账 · 钱怎么记</div>
    <div class="ledger-grid">
      <div class="ledger">
        <div class="ledger-title">消费</div>
        <div class="ledger-row"><span class="ledger-k">分类体系</span>
          <span class="ledger-v">${mono(cfg.categories_n)} 个分类</span>
          <button class="ledger-act" data-go="knowledge">去知识库 →</button></div>
        <div class="ledger-row"><span class="ledger-k">AI 记忆</span>
          <span class="ledger-v">${mono(cfg.knowledge_n)} 条 <span class="sub">· 分类依据</span></span>
          <button class="ledger-act" data-go="knowledge">去知识库 →</button></div>
        <div class="ledger-row"><span class="ledger-k">记账周期</span>
          <span class="ledger-v" id="bill-disp">每月 ${mono(cfg.billing_start_day)} 日起</span>
          <button class="ledger-act" id="bill-btn">改</button></div>
        <div class="ledger-row"><span class="ledger-k">通知</span>
          <span class="ledger-v" id="notify-check-status">${notifyTxt}</span>
          <button class="ledger-act" id="notify-check">发送测试</button></div>
      </div>
      <div class="ledger">
        <div class="ledger-title">进项</div>
        <div class="ledger-row"><span class="ledger-k">识别来源</span>
          <span class="ledger-v">一卡通邮件 → AI 解析 <span class="sub">· 工资/报销/汇款/理财到账</span></span><span></span></div>
        <div class="ledger-row"><span class="ledger-k">已识别</span>
          <span class="ledger-v">${mono(cfg.deposits_n)} 笔 <span class="sub">· 最近 ${cfg.deposits_last ? esc(cfg.deposits_last) : "—"}</span></span>
          <button class="ledger-act" data-go="income">去进项 →</button></div>
        <div class="ledger-row"><span class="ledger-k">通知</span>
          <span class="ledger-v">暂不推送 <span class="sub">· 进项仅记账，不发微信</span></span><span></span></div>
      </div>
    </div>`;

  // -- live self-check wiring --------------------------------------------
  const setNode = (rail, idx, { state, val, lead }) => {
    const node = rail.children[idx];
    node.className = "rail-node " + state + (lead === "on" ? " lead-on" : lead === "off" ? " lead-off" : "");
    if (val != null) node.querySelector(".rail-val").textContent = val;
  };
  const setSeal = (el, cls, ch) => {
    const s = el.querySelector("[data-seal]");
    if (!s) return;
    s.className = "seal " + cls; s.textContent = ch;
  };
  const setOut = (el, html, cls) => { const o = el.querySelector("[data-out]"); o.className = "check-out " + (cls || ""); o.innerHTML = html; };

  async function runCheck(el, endpoint, cardType) {
    const rail = el.querySelector("[data-rail]");
    const btn = el.querySelector("[data-recheck]");
    btn.disabled = true;
    setSeal(el, "checking", "检");
    setNode(rail, 2, { state: "checking", val: "检测中…" });
    setOut(el, "检测中…", "");
    try {
      const r = await post(endpoint, { card_type: cardType });
      if (r.ok) {
        setSeal(el, "ok", "通");
        setNode(rail, 1, { state: "done", val: "已授权", lead: "on" });
        const n = r.recent_email_7d;
        setNode(rail, 2, { state: "done", val: `${n} 封/7天` });
        const detail = `近 7 天 ${n} 封招行邮件`;
        setOut(el, `✓ 通 · ${detail} · ${r.elapsed_ms}ms`, "ok");
      } else {
        // Email failures can be caused by credentials, mailbox resolution, or
        // connectivity, so keep authorization neutral and mark connectivity.
        setNode(rail, 1, { state: "", lead: "off" });
        setSeal(el, "bad", "断");
        setNode(rail, 2, { state: "bad", val: "连不上" });
        setOut(el, "✗ " + esc(r.error), "bad");
        const g = el.querySelector(".guide"); if (g) g.open = true;
      }
    } catch (e) {
      setSeal(el, "bad", "断");
      setNode(rail, 2, { state: "bad", val: "连不上" });
      setOut(el, "✗ " + esc(e.message), "bad");
    } finally { btn.disabled = false; }
  }

  [debit, credit].forEach((c) => {
    const el = $(`[data-pipe="${c.cardType}"]`);
    const recheck = el.querySelector("[data-recheck]");
    if (recheck) {
      recheck.addEventListener("click", () =>
        runCheck(el, c.endpoint, c.cardType));
    }
    if (c.autoRun && c.canCheck) {
      runCheck(el, c.endpoint, c.cardType);
    }
  });

  // -- Mail authorization wizard ----------------------------------------
  const awSave = $("#aw-save");
  if (awSave) awSave.addEventListener("click", async () => {
    const provider = ($("#aw-provider").value || "gmail").trim();
    const email = ($("#aw-email").value || "").trim();
    const pw = ($("#aw-pw").value || "").trim();
    const out = $("#aw-out");
    if (!email || !pw) { out.className = "aw-out bad"; out.textContent = "邮箱和授权码都要填"; return; }
    awSave.disabled = true; out.className = "aw-out"; out.textContent = "保存并连接中…";
    try {
      const proxyEnabled = !!$("#aw-proxy-enabled")?.checked;
      const proxyHost = ($("#aw-proxy-host")?.value || "127.0.0.1").trim();
      const proxyPort = ($("#aw-proxy-port")?.value || "8118").trim();
      const payload = { provider, email, app_password: pw };
      if (provider === "gmail") {
        payload.proxy_enabled = proxyEnabled;
        payload.proxy_host = proxyHost;
        payload.proxy_port = proxyPort;
      }
      const r = await post(`/api/gmail_auth`, payload);
      const c = r.check || {};
      if (r.ok && c.ok) {
        state.mailProviderDraft = "";
        state.mailEmailDraftProvider = "";
        state.mailEmailDraft = "";
        state.mailProxyDraftProvider = "";
        toast(`已连接 ${_mailHelp(provider).label.replace("授权码", "")} · 授权成功`);
        renderConfig();
        return;
      }
      out.className = "aw-out bad"; out.textContent = "✗ " + _mailAuthHint(c.error || r.error || "保存失败", provider);
    } catch (e) {
      out.className = "aw-out bad"; out.textContent = "✗ " + e.message;
    } finally { awSave.disabled = false; }
  });
  const providerSel = $("#aw-provider");
  if (providerSel) providerSel.addEventListener("change", () => {
    state.mailProviderDraft = providerSel.value;
    state.mailEmailDraftProvider = "";
    state.mailEmailDraft = "";
    state.mailProxyDraftProvider = "";
    renderConfig();
  });
  const emailInput = $("#aw-email");
  if (emailInput) emailInput.addEventListener("input", () => {
    const provider = ($("#aw-provider")?.value || "gmail").trim();
    state.mailEmailDraftProvider = provider;
    state.mailEmailDraft = emailInput.value;
  });
  const proxyEnabled = $("#aw-proxy-enabled");
  const proxyHost = $("#aw-proxy-host");
  const proxyPort = $("#aw-proxy-port");
  const rememberProxyDraft = () => {
    const provider = ($("#aw-provider")?.value || "gmail").trim();
    state.mailProxyDraftProvider = provider;
    state.mailProxyEnabledDraft = !!proxyEnabled?.checked;
    state.mailProxyHostDraft = proxyHost?.value || "127.0.0.1";
    state.mailProxyPortDraft = proxyPort?.value || "8118";
  };
  if (proxyEnabled) proxyEnabled.addEventListener("change", () => {
    rememberProxyDraft();
    renderConfig();
  });
  if (proxyHost) proxyHost.addEventListener("input", rememberProxyDraft);
  if (proxyPort) proxyPort.addEventListener("input", rememberProxyDraft);

  // -- Hermes -> WeChat end-to-end delivery check -----------------------
  const notifyCheck = $("#notify-check");
  if (notifyCheck) notifyCheck.addEventListener("click", async () => {
    const out = $("#notify-check-status");
    notifyCheck.disabled = true;
    notifyCheck.textContent = "发送中…";
    out.innerHTML = `微信 · <span class="sub">正在校验 Hermes 目标并发送测试消息…</span>`;
    try {
      const r = await post(`/api/config/check_notify`, {});
      if (r.ok) {
        out.innerHTML = `微信 · <span style="color:var(--green)">通</span>` +
          ` <span class="sub">· 测试消息发送成功 · ${r.elapsed_ms}ms</span>`;
        toast("Hermes → 微信通道正常");
      } else {
        out.innerHTML = `<span style="color:var(--red)">微信 · 断</span>` +
          ` <span class="sub">· ${esc(r.error || "发送失败")}</span>`;
      }
    } catch (e) {
      out.innerHTML = `<span style="color:var(--red)">微信 · 断</span>` +
        ` <span class="sub">· ${esc(e.message)}</span>`;
    } finally {
      notifyCheck.disabled = false;
      notifyCheck.textContent = "发送测试";
    }
  });

  // -- ledger actions ----------------------------------------------------
  $("#view").querySelectorAll("[data-go]").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.go)));

  const billBtn = $("#bill-btn");
  billBtn.onclick = () => {
    const disp = $("#bill-disp");
    const opts = Array.from({ length: 28 }, (_, i) => i + 1)
      .map((d) => `<option ${d === cfg.billing_start_day ? "selected" : ""}>${d}</option>`).join("");
    disp.innerHTML = `<span class="bill-edit">每月 <select id="bill-sel">${opts}</select> 日起</span>`;
    billBtn.textContent = "保存";
    billBtn.onclick = async () => {
      const d = parseInt($("#bill-sel").value, 10);
      const r = await post(`/api/settings/billing_start_day`, { day: d });
      state.billingStartDay = r.billing_start_day || d;
      toast(`记账周期已设为每月 ${state.billingStartDay} 日起`);
      renderConfig();
    };
  };
}

// ---- router ------------------------------------------------------------
const views = { ledger: renderLedger, pending: renderPending, stats: renderStats, income: renderIncome, knowledge: renderKnowledge, config: renderConfig };
async function setView(name) {
  state.view = name;
  // Reflect the view in the URL hash so it's deep-linkable / bookmarkable.
  if (("#" + name) !== location.hash) history.replaceState(null, "", "#" + name);
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  $("#view").innerHTML = `<div class="empty">载入中…</div>`;
  try { await views[name](); } catch (e) { $("#view").innerHTML = `<div class="empty">出错了：${esc(e.message)}</div>`; }
  // Keep the sidebar "本月支出" in sync with live data on every view switch
  // (new transactions may have been ingested since the page loaded).
  refreshSummary().catch(() => {});
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#nav").addEventListener("click", (e) => {
    const b = e.target.closest(".nav-item"); if (b) setView(b.dataset.view);
  });
  await bootstrap();
  const initial = (location.hash || "").slice(1);
  await setView(views[initial] ? initial : "ledger");
});
