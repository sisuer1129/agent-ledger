import { createTransactionPager } from './pagination.mjs?v=20260909-pagination1';
import { renderCharts } from './charts.mjs?v=20260909-pagination1';

export const state = {
  accounts: [], taxonomy: null, budgets: [], budgetSummary: null, selectedAccountId: null,
  taxonomyPromise: null, periodMode: 'billing_cycle', periodAnchor: null, filters: {}, activePage: 'overview',
  taxonomyMonth: null, mobileStatsMonth: null, mobileTab: 'home', mobileAccountId: null,
};

export const formatMoney = (value) => new Intl.NumberFormat('zh-CN', {
  style: 'currency', currency: 'CNY', minimumFractionDigits: 2,
}).format(Number(value || 0));

export const formatPeriod = (start, end) => `${compactDate(start)}—${compactDate(end)}`;
export const localDateString = (date = new Date()) => [
  date.getFullYear(),
  String(date.getMonth() + 1).padStart(2, '0'),
  String(date.getDate()).padStart(2, '0'),
].join('-');
export const buildQuery = (params) => {
  const query = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, value);
  });
  return query;
};
export const buildExportPath = (filters, nonce = Date.now()) => {
  const query = buildQuery(filters);
  query.set('_fresh', String(nonce));
  return `/export.csv?${query}`;
};
export const budgetState = (percent) => (percent >= 100 ? 'over' : percent >= 80 ? 'warning' : 'normal');
export const monthlyBalance = ({ income = 0, expense = 0, refunds = 0 } = {}) => (
  Number(income || 0) - Number(expense || 0) + Number(refunds || 0)
);
export const transactionEditMode = (kind) => (
  ['expense', 'income', 'refund', 'transfer', 'topup_withdrawal', 'balance_adjustment'].includes(kind)
    ? kind : 'expense'
);
export const normalizeTransactionAmount = (value, kind, originalAmount) => {
  const amount = Math.abs(Number(value || 0));
  if (['transfer', 'topup_withdrawal', 'balance_adjustment'].includes(kind) && originalAmount < 0) return -amount;
  return kind === 'expense' ? -amount : amount;
};
export const formatTransactionCategory = ({ category_primary_name, category_secondary_name }) => (
  [category_primary_name, category_secondary_name].filter(Boolean).join(' · ')
);
export const formatBudgetAlert = (item) => (
  item.status === 'exceeded'
    ? `${item.name}预算超支 ${formatMoney(-item.remaining_amount)}`
    : `${item.name}预算已用 ${(item.usage_rate * 100).toFixed(0)}%`
);
export const formatBudgetImpactPreview = (preview) => {
  if (!preview?.visible) {
    if (preview?.reason === 'unclassified') return '选择分类后显示预算影响';
    if (preview?.reason === 'unbudgeted') return `${preview.category?.name || '该分类'}本月尚未设置预算`;
    return '';
  }
  const { category, current, projected } = preview;
  const headline = `${category.name}预算：已用 ${formatMoney(current.spent_amount)} / ${formatMoney(current.budget_amount)}`;
  const outcome = projected.remaining_amount < 0
    ? `记入本笔后将超出预算 ${formatMoney(-projected.remaining_amount)}`
    : `记入后剩余 ${formatMoney(projected.remaining_amount)}`;
  return `${headline}\n${outcome}`;
};
export const categoryBudgetDetails = (category) => {
  if (category?.budget_amount === null || category?.budget_amount === undefined) return null;
  return {
    spend: `本月支出 ${formatMoney(category.spent_amount)}`,
    budget: `预算 ${formatMoney(category.budget_amount)}`,
    usage: `已用 ${(category.usage_rate * 100).toFixed(0)}%`,
    balance: category.remaining_amount < 0 ? `超支 ${formatMoney(-category.remaining_amount)}` : `剩余 ${formatMoney(category.remaining_amount)}`,
    status: category.status,
  };
};
export const repaymentSourceAccounts = (accounts, creditAccountId) => (
  accounts.filter((account) => account.id !== creditAccountId)
);
export const prepareTransactionPayload = (data) => {
  const payload = { ...data };
  if (!payload.category_id) delete payload.category_id;
  return payload;
};
export const accountSaveTarget = (id) => ({
  path: id ? `/accounts/${encodeURIComponent(id)}` : '/accounts',
  method: id ? 'PUT' : 'POST',
});
export const accountPeriodMode = (account, currentMode, preserveMode = false) => {
  const supportsBillingCycle = account.type === 'credit' && Boolean(account.statement_day);
  const allowedModes = supportsBillingCycle
    ? ['billing_cycle', 'last_30_days', 'calendar_month']
    : ['last_30_days', 'calendar_month'];
  if (preserveMode && allowedModes.includes(currentMode)) return currentMode;
  return supportsBillingCycle ? 'billing_cycle' : 'last_30_days';
};
export const prepareAccountPayload = (data, creating) => {
  const payload = { ...data };
  ['monthly_budget', 'credit_limit'].forEach((field) => {
    if (field in payload) payload[field] = payload[field] === '' ? null : Number(payload[field]);
  });
  ['statement_day', 'due_day', 'due_month_offset'].forEach((field) => {
    if (field in payload) payload[field] = payload[field] === '' ? null : Number(payload[field]);
  });
  if (creating) payload.initial_balance = payload.initial_balance === '' ? 0 : Number(payload.initial_balance);
  else delete payload.initial_balance;
  return payload;
};
export const COMMON_CATEGORY_IDS = [
  'expense_dining_meal', 'expense_daily_shopping_groceries', 'expense_vehicle_charging_fuel',
  'expense_transport_temporary_parking', 'expense_digital_ai', 'expense_network_service',
];
export const categoryPickerGroups = (taxonomy) => taxonomy?.categories || [];
export const nextExpandedCategoryGroupIds = (currentIds, groupId) => (
  currentIds.includes(groupId) ? [] : [groupId]
);
export const transactionForEdit = (transactionsById, transactionId) => (
  transactionsById.get(transactionId) || null
);
const OVERVIEW_ACCOUNT_GROUPS = [
  ['信用卡', new Set(['credit'])],
  ['储蓄卡', new Set(['debit', 'wechat'])],
];
export const accountTypeLabel = (type) => ({
  credit: '信用卡', debit: '储蓄卡', wechat: '微信账户', shopping_card: '购物卡',
}[type] || '账户');
export const groupAccountsForOverview = (accounts) => {
  const groups = OVERVIEW_ACCOUNT_GROUPS.map(([title, types]) => ({
    title, accounts: accounts.filter((account) => types.has(account.type)),
  })).filter((group) => group.accounts.length);
  const groupedTypes = new Set(OVERVIEW_ACCOUNT_GROUPS.flatMap(([, types]) => [...types]));
  const others = accounts.filter((account) => !groupedTypes.has(account.type));
  if (others.length) groups.push({ title: '其他账户', accounts: others });
  return groups;
};

class ApiError extends Error {
  constructor(type, message, detail) { super(message); this.type = type; this.detail = detail; }
}

const compactDate = (value) => value.replace(/-(\d{2})/g, '/$1').replace(/\/0/g, '/');
const baseUrl = () => localStorage.getItem('apiUrl') || '';
const apiKey = () => localStorage.getItem('apiKey') || '';
const today = () => localDateString();
let chartLibraryPromise;
let expandedCategoryGroups = new Set();
let budgetImpactTimer;
let budgetImpactRequestToken = 0;
let taxonomyMonthRequestToken = 0;
let mobileStatsRequestToken = 0;
const transactionsById = new Map();

export async function api(path, options = {}) {
  if (!apiKey()) throw new ApiError('configuration', '请先在设置中填写 API Key');
  let response;
  try {
    response = await fetch(baseUrl() + path, {
      ...options,
      headers: { 'X-API-Key': apiKey(), 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
  } catch (cause) {
    throw new ApiError('network', '无法连接账本服务', cause);
  }
  if (response.status === 401) throw new ApiError('authentication', 'API Key 无效');
  const isCsv = response.headers.get('content-type')?.includes('text/csv');
  const body = isCsv ? await response.blob() : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.error;
    throw new ApiError(response.status === 400 ? 'validation' : 'server', detail?.message || '服务暂时不可用', detail);
  }
  if (!isCsv && body === null) throw new ApiError('server', '服务返回格式异常');
  return body;
}

function escapeHtml(value) {
  const element = document.createElement('span');
  element.textContent = value ?? '';
  return element.innerHTML;
}

function showStatus(message, retry) {
  const node = document.querySelector('#status');
  node.className = message ? 'error-state' : '';
  node.textContent = message;
  if (retry) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = '重试'; button.addEventListener('click', retry);
    node.append(' ', button);
  }
  node.style.display = message ? 'block' : 'none';
}

function safe(task) {
  return Promise.resolve(task()).catch((error) => showStatus(error.message, () => safe(task)));
}

function renderState(kind, title, detail = '', action = '') {
  return `<div class="${kind}" role="status"><strong>${escapeHtml(title)}</strong>${detail ? `<p>${escapeHtml(detail)}</p>` : ''}${action}</div>`;
}

const mobilePageForTab = (tab) => ({ home: 'overview', ledger: 'ledger', stats: 'taxonomy', settings: 'settings' })[tab] || 'overview';
const mobileTabForPage = (page) => ({ overview: 'home', ledger: 'ledger', taxonomy: 'stats', settings: 'settings' })[page] || '';

function syncNavigationState(page) {
  document.querySelectorAll('[data-page]').forEach((node) => {
    const active = node.dataset.page === page;
    node.classList.toggle('is-active', active);
    if (active) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
  const mobileTab = mobileTabForPage(page);
  document.querySelectorAll('[data-mobile-tab]').forEach((node) => {
    const active = node.dataset.mobileTab === mobileTab;
    node.classList.toggle('is-active', active);
    if (active) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
}

function renderAccountDetailEmpty() {
  document.querySelector('#accountDetail').innerHTML = renderState('panel-state', '选择一个账户', '查看交易明细与账期信息。');
}

function categoryOptions() {
  const roots = state.taxonomy?.categories || [];
  return roots.flatMap((parent) => [parent, ...(parent.children || [])]);
}

function categoryPresentationMap() {
  const fallbackColor = state.taxonomy?.category_fallback_color;
  const entries = [['_fallback', {
    label: '未分类', primaryLabel: '未分类', color: fallbackColor, rootColor: fallbackColor,
  }]];
  (state.taxonomy?.categories || []).forEach((parent) => {
    entries.push([parent.id, {
      label: parent.name, primaryLabel: parent.name, color: parent.color, rootColor: parent.root_color,
    }]);
    (parent.children || []).forEach((child) => entries.push([child.id, {
      label: child.name, primaryLabel: parent.name, color: child.color, rootColor: child.root_color,
    }]));
  });
  return Object.fromEntries(entries);
}

function rowAccount(account) {
  return `<button class="account-row" data-account="${account.id}" aria-selected="${account.id === state.selectedAccountId}">
    <span><strong>${escapeHtml(account.name)}</strong><br><span class="meta">${accountTypeLabel(account.type)}${account.monthly_budget ? ` · 预算 ${formatMoney(account.monthly_budget)}` : ''}</span></span>
    <span class="amount">${formatMoney(account.current_balance)}</span></button>`;
}

function transactionRow(transaction, editable = true) {
  transactionsById.set(transaction.id, transaction);
  const category = formatTransactionCategory(transaction);
  const details = [transaction.account_name, transaction.timestamp.slice(0, 10)].filter(Boolean);
  const categoryState = !category ? 'is-unclassified'
    : transaction.transaction_kind === 'expense' ? 'is-expense'
      : transaction.transaction_kind === 'income' ? 'is-income'
        : 'is-neutral';
  const categoryDot = category ? '<i class="ledger-category-dot" aria-hidden="true"></i>' : '';
  const categoryTag = `<span class="ledger-category ${categoryState}">${categoryDot}${escapeHtml(category || '未分类')}</span>`;
  const mobileCategory = categoryTag;
  return `<div class="ledger-row transaction-row"><span class="transaction-main"><strong>${escapeHtml(transaction.description)}</strong><br><span class="meta">${details.map(escapeHtml).join(' · ')}<span class="transaction-mobile-category">${mobileCategory}</span></span></span>
    ${categoryTag}
    <span class="amount ${transaction.amount < 0 ? 'negative' : ''}">${formatMoney(transaction.amount)}</span>
    ${editable && transaction.transaction_kind !== 'credit_repayment' ? `<button data-edit-transaction="${transaction.id}" aria-label="编辑交易">编辑</button>` : transaction.repayment_group_id ? `<button data-reverse-repayment="${escapeHtml(transaction.repayment_group_id)}" aria-label="撤销还款">撤销还款</button>` : '<span class="ledger-action-placeholder">—</span>'}</div>`;
}

function ledgerColumnHead() {
  return '<div class="ledger-column-head" aria-hidden="true"><span>消费名称</span><span>类别</span><span>金额</span><span>操作</span></div>';
}

function renderAccountList() {
  document.querySelector('#accountList').innerHTML = groupAccountsForOverview(state.accounts).map((group) =>
    `<section class="account-group"><h3>${group.title}</h3>${group.accounts.map(rowAccount).join('')}</section>`
  ).join('') || renderState('list-state', '尚未添加账户', '添加账户后即可开始记录收支。');
}

function populateTransactionForm(transaction = {}) {
  const form = document.querySelector('#transactionForm');
  form.dataset.transactionId = transaction.id || '';
  form._budgetPreviewOriginal = transaction;
  form._originalTransaction = transaction;
  form.querySelector('#deleteTransaction').hidden = !transaction.id || transaction.transaction_kind === 'credit_repayment';
  form.querySelector('[name=account_id]').innerHTML = state.accounts.map((account) => `<option value="${account.id}">${escapeHtml(account.name)}</option>`).join('');
  form.querySelector('[name=repayment_credit_account_id]').innerHTML = state.accounts
    .filter((account) => account.type === 'credit')
    .map((account) => `<option value="${account.id}">${escapeHtml(account.name)}</option>`).join('');
  form.querySelector('[name=category_id]').innerHTML = '<option value="">自动建议</option>' + categoryOptions().map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  form.querySelector('[name=tag_ids]').innerHTML = (state.taxonomy?.tags || []).map((tag) => `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`).join('');
  form.querySelector('[name=timestamp]').value = transaction.timestamp?.slice(0, 10) || today();
  for (const name of ['account_id', 'category_id', 'transaction_kind', 'description']) if (transaction[name]) form.querySelector(`[name=${name}]`).value = transaction[name];
  form.querySelector('[name=amount]').value = transaction.amount === undefined ? '' : Math.abs(transaction.amount);
  [...form.querySelector('[name=tag_ids]').options].forEach((option) => { option.selected = (transaction.tag_ids || []).includes(option.value); });
  const mode = transactionEditMode(transaction.transaction_kind);
  form.querySelector(`[name=transaction_mode][value=${mode}]`).checked = true;
  setTransactionFormMode(mode);
  renderCategoryPicker();
}

function syncRepaymentAccountChoices(form) {
  const creditId = form.elements.repayment_credit_account_id.value;
  const source = form.elements.account_id;
  [...source.options].forEach((option) => { option.hidden = option.value === creditId; });
  if (source.selectedOptions[0]?.hidden) {
    const replacement = [...source.options].find((option) => !option.hidden);
    if (replacement) source.value = replacement.value;
  }
}

function setTransactionFormMode(mode) {
  const form = document.querySelector('#transactionForm');
  const repayment = mode === 'credit_repayment';
  form.elements.transaction_kind.value = repayment ? 'credit_repayment' : mode;
  form.querySelectorAll('[data-transaction-field="ordinary"]').forEach((node) => { node.hidden = repayment; });
  form.querySelector('[data-transaction-field="repayment-credit"]').hidden = !repayment;
  form.querySelector('[data-transaction-field="repayment-note"]').hidden = !repayment;
  form.querySelector('.transaction-account-label').textContent = repayment ? '付款账户' : '账户';
  form.elements.description.required = !repayment;
  form.querySelector('[data-save-label]').textContent = repayment ? '保存还款' : '保存';
  if (repayment) syncRepaymentAccountChoices(form);
  else [...form.elements.account_id.options].forEach((option) => { option.hidden = false; });
}

function setTransactionBudgetImpact(message = '', status = '') {
  const node = document.querySelector('#transactionBudgetImpact');
  node.hidden = !message;
  node.textContent = message;
  node.className = `transaction-budget-impact${status ? ` budget-${status}` : ''}`;
}

function budgetPreviewPayload(form) {
  const mode = form.elements.transaction_mode.value;
  if (!['expense', 'refund'].includes(mode)) return { kind: 'ignored' };
  const categoryId = form.elements.category_id.value || null;
  if (!categoryId) return { kind: 'unclassified' };
  const rawAmount = Number(form.elements.amount.value);
  const timestamp = form.elements.timestamp.value;
  if (!Number.isFinite(rawAmount) || rawAmount <= 0 || !timestamp) return { kind: 'invalid' };
  const original = form._budgetPreviewOriginal || {};
  return {
    kind: 'request',
    timestamp,
    amount: normalizeTransactionAmount(rawAmount, mode),
    transaction_kind: mode,
    category_id: categoryId,
    excluded_from_stats: form.elements.excluded_from_stats?.checked ?? Boolean(original.excluded_from_stats),
    transaction_id: form.dataset.transactionId || undefined,
  };
}

function scheduleTransactionBudgetPreview() {
  const form = document.querySelector('#transactionForm');
  clearTimeout(budgetImpactTimer);
  const requestToken = ++budgetImpactRequestToken;
  const payload = budgetPreviewPayload(form);
  if (payload.kind === 'unclassified') {
    setTransactionBudgetImpact(formatBudgetImpactPreview({ visible: false, reason: 'unclassified' }));
    return;
  }
  if (payload.kind !== 'request') {
    setTransactionBudgetImpact();
    return;
  }
  const { kind: _kind, ...requestPayload } = payload;
  budgetImpactTimer = setTimeout(async () => {
    try {
      const preview = await api('/budget-impact-preview', { method: 'POST', body: JSON.stringify(requestPayload) });
      if (requestToken !== budgetImpactRequestToken) return;
      setTransactionBudgetImpact(formatBudgetImpactPreview(preview), preview.projected?.status || '');
    } catch (_) {
      if (requestToken !== budgetImpactRequestToken) return;
      setTransactionBudgetImpact();
    }
  }, 300);
}

function categoryName(categoryId) {
  return categoryOptions().find((category) => category.id === categoryId)?.name || '自动建议';
}

function categoryChoice(category, selectedId) {
  return `<button type="button" class="category-choice${category.id === selectedId ? ' selected' : ''}" data-category-choice="${category.id}">${escapeHtml(category.name)}</button>`;
}

function renderCategoryPicker() {
  const form = document.querySelector('#transactionForm');
  const select = form.querySelector('[name=category_id]');
  const selectedId = select.value;
  const picker = document.querySelector('#categoryPicker');
  const search = document.querySelector('#categoryPickerSearch');
  const query = search.value.trim().toLocaleLowerCase();
  document.querySelector('#categoryPickerToggle').textContent = categoryName(selectedId);
  const common = COMMON_CATEGORY_IDS.map((id) => categoryOptions().find((category) => category.id === id)).filter(Boolean);
  document.querySelector('#categoryQuickChoices').innerHTML = `<button type="button" class="category-choice${!selectedId ? ' selected' : ''}" data-category-choice="">自动建议</button>${common.map((category) => categoryChoice(category, selectedId)).join('')}`;
  const groups = categoryPickerGroups(state.taxonomy);
  if (query) {
    const matches = categoryOptions().filter((category) => category.name.toLocaleLowerCase().includes(query));
    document.querySelector('#categoryPickerGroups').innerHTML = `<div class="category-search-results">${matches.map((category) => categoryChoice(category, selectedId)).join('') || '<p class="meta">没有匹配的分类</p>'}</div>`;
  } else {
    document.querySelector('#categoryPickerGroups').innerHTML = groups.map((group) => {
      const children = group.children || [];
      if (!children.length) return `<div class="category-group single">${categoryChoice(group, selectedId)}</div>`;
      const expanded = expandedCategoryGroups.has(group.id);
      return `<section class="category-group"><button type="button" class="category-group-head" data-category-group="${group.id}" aria-expanded="${expanded}"><span>${escapeHtml(group.name)}</span><span>${expanded ? '⌄' : '›'}</span></button><div class="category-group-items"${expanded ? '' : ' hidden'}>${categoryChoice({ id: group.id, name: `全部${group.name}` }, selectedId)}${children.map((category) => categoryChoice(category, selectedId)).join('')}</div></section>`;
    }).join('');
  }
  picker.hidden = document.querySelector('#categoryPickerToggle').getAttribute('aria-expanded') !== 'true';
}

function chooseCategory(categoryId) {
  document.querySelector('#transactionForm [name=category_id]').value = categoryId;
  document.querySelector('#categoryPickerToggle').setAttribute('aria-expanded', 'false');
  document.querySelector('#categoryPickerSearch').value = '';
  renderCategoryPicker();
  scheduleTransactionBudgetPreview();
}

function refreshBudgetTargets() {
  const form = document.querySelector('#budgetForm');
  const type = form.scope_type.value;
  const objectField = form.querySelector('[data-budget-object]');
  if (type === 'total') {
    form.scope_id.innerHTML = '<option value="total">本月总预算</option>';
    objectField.hidden = true;
    return;
  }
  objectField.hidden = false;
  const targets = type === 'account' ? state.accounts : budgetCategoryOptions();
  form.scope_id.innerHTML = targets.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
}

function budgetCategoryOptions() {
  return state.taxonomy?.categories || [];
}

function budgetStatusLabel(status) {
  return ({ normal: '正常', warning: '接近预算', reached: '已达预算', exceeded: '已超支', unbudgeted: '未设置预算' })[status] || '未设置预算';
}

function budgetProgress(record, showMeta = true) {
  if (record.budget_amount === null) return '';
  const visual = Math.min(100, Math.max(0, record.usage_rate * 100));
  const percentage = record.usage_rate * 100;
  const balance = record.remaining_amount >= 0 ? `剩余 ${formatMoney(record.remaining_amount)}` : `超支 ${formatMoney(-record.remaining_amount)}`;
  return `<div class="budget-progress"><span class="budget-progress-track"><i class="budget-progress-fill budget-${record.status}" style="width:${visual}%"></i></span>${showMeta ? `<span class="budget-progress-meta budget-${record.status}">${percentage.toFixed(0)}% · ${balance}</span>` : ''}</div>`;
}

function budgetEditButton(record) {
  return `<button data-edit-budget="${record.scope_type}|${record.scope_id}">编辑</button>`;
}

function populateBudgetForm(budget = {}) {
  const form = document.querySelector('#budgetForm');
  form.reset();
  form.scope_type.value = budget.scope_type || 'total';
  refreshBudgetTargets();
  form.scope_id.value = budget.scope_id || 'total';
  form.amount.value = budget.amount ?? '';
  form.effective_from.value = budget.effective_from?.slice(0, 7) || today().slice(0, 7);
}

function refreshRuleTargets() {
  const form = document.querySelector('#ruleForm');
  const type = form.rule_type.value;
  form.querySelector('[name=category_id]').innerHTML = categoryOptions().map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  form.querySelector('[name=tag_id]').innerHTML = (state.taxonomy?.tags || []).map((tag) => `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`).join('');
  form.querySelectorAll('[data-rule-target]').forEach((node) => node.classList.toggle('visible', node.dataset.ruleTarget === type));
}

function populateFilterOptions() {
  const form = document.querySelector('#filterForm');
  form.querySelector('[name=account_id]').innerHTML = '<option value="">全部账户</option>' + state.accounts.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  form.querySelector('[name=category_id]').innerHTML = '<option value="">全部分类</option>' + categoryOptions().map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  form.querySelector('[name=tag_id]').innerHTML = '<option value="">全部标签</option>' + (state.taxonomy?.tags || []).map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  Object.entries(state.filters).forEach(([name, value]) => { if (form.elements[name]) form.elements[name].value = value; });
}

async function ensureTaxonomy() {
  if (state.taxonomy) return state.taxonomy;
  if (!state.taxonomyPromise) {
    state.taxonomyPromise = api('/taxonomy').then((taxonomy) => {
      state.taxonomy = taxonomy;
      populateFilterOptions();
      return taxonomy;
    }).finally(() => { state.taxonomyPromise = null; });
  }
  return state.taxonomyPromise;
}

async function loadChartLibrary() {
  if (window.Chart) return window.Chart;
  if (!chartLibraryPromise) {
    chartLibraryPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/chart.js';
      script.async = true;
      script.onload = () => resolve(window.Chart);
      script.onerror = () => reject(new Error('图表组件暂时无法加载'));
      document.head.append(script);
    });
  }
  return chartLibraryPromise;
}

async function refreshOverview() {
  const now = new Date();
  const month = today().slice(0, 7);
  const [overview, summary] = await Promise.all([
    api(`/overview?year=${now.getFullYear()}&month=${now.getMonth() + 1}`),
    api(`/budget-summary?month=${month}`),
  ]);
  document.querySelector('#overviewBalance').textContent = formatMoney(monthlyBalance(overview));
  document.querySelector('#overviewIncome').textContent = formatMoney(overview.income);
  document.querySelector('#overviewExpense').textContent = formatMoney(overview.expense);
  document.querySelector('#mobileSummary').textContent = formatMoney(overview.net_assets);
  const total = summary.total;
  const visibleAlerts = summary.alerts.slice(0, 2);
  const alerts = visibleAlerts.map((item) => `<span class="overview-budget-alert budget-${item.status}">${escapeHtml(formatBudgetAlert(item))}</span>`).join('');
  const alertsMore = summary.alerts.length > visibleAlerts.length ? '<span class="overview-budget-alert-more">查看全部</span>' : '';
  const headline = total.budget_amount === null ? '本月尚未设置总预算' : `${formatMoney(total.spent_amount)} / ${formatMoney(total.budget_amount)}`;
  const balance = total.budget_amount === null ? `已设置 ${summary.configured_category_count} 项分类预算` : total.remaining_amount < 0 ? `超支 ${formatMoney(-total.remaining_amount)}` : `剩余 ${formatMoney(total.remaining_amount)}`;
  document.querySelector('#overviewBudgetCard').innerHTML = `<span class="overview-budget-content${alerts ? ' has-alerts' : ''}"><span class="overview-budget-main"><span class="eyebrow">本月预算</span><strong>${headline}</strong><small class="budget-${total.status}">${total.budget_amount === null ? balance : `${(total.usage_rate * 100).toFixed(0)}% · ${balance}`}</small>${total.budget_amount === null ? '' : budgetProgress(total, false)}</span>${alerts ? `<span class="overview-budget-alerts"><span class="overview-budget-alert-title">分类提醒</span>${alerts}${alertsMore}</span>` : ''}</span><span class="overview-budget-link" aria-hidden="true">查看预算详情 ›</span>`;
  return overview;
}

async function selectAccount(id, preserveMode = false) {
  const requestToken = ++desktopAccountRequestToken;
  transactionPagers['desktop-account']?.invalidate();
  const account = state.accounts.find((item) => item.id === id);
  if (!account) return;
  state.selectedAccountId = id;
  state.periodMode = accountPeriodMode(account, state.periodMode, preserveMode);
  renderAccountList();
  const [detail] = await Promise.all([
    api(`/accounts/${encodeURIComponent(id)}/insights?${buildQuery({ mode: state.periodMode, anchor: state.periodAnchor || today(), transaction_limit: 50 })}`),
    ensureTaxonomy(),
  ]);
  if (requestToken !== desktopAccountRequestToken) return;
  const budget = account.monthly_budget || 0;
  const used = budget ? detail.expense / budget * 100 : 0;
  const forecast = budget && detail.period_start ? detail.expense / Math.max(1, new Date().getDate()) * 30 : null;
  const modeOptions = account.type === 'credit' && account.statement_day
    ? '<option value="billing_cycle">账单周期</option><option value="last_30_days">近 30 天</option><option value="calendar_month">自然月</option>'
    : '<option value="last_30_days">近 30 天</option><option value="calendar_month">自然月</option>';
  const periodLabel = formatPeriod(detail.period_start, detail.period_end);
  document.querySelector('#accountDetail').innerHTML = `<div class="page-head"><h2>${escapeHtml(account.name)}</h2><div><button data-edit-account="${account.id}">编辑账户</button></div></div>
    <section class="detail-period-section"><p class="detail-section-label">周期控制</p><div class="detail-controls"><select id="periodMode">${modeOptions}</select><input id="periodAnchor" type="date" value="${state.periodAnchor || today()}"><button id="refreshInsights">查看</button></div></section>
    <section class="detail-summary-section"><p class="detail-section-label">指标摘要</p><p class="meta">${periodLabel}</p><div class="metrics"><strong>支出 ${formatMoney(detail.expense)}</strong>${budget ? `<span class="budget-${budgetState(used)}">预算 ${used.toFixed(0)}% · 剩余 ${formatMoney(Math.max(0, budget - detail.expense))}</span>` : ''}${forecast ? `<span>预计期末 ${formatMoney(forecast)}</span>` : ''}${detail.credit_limit !== undefined ? `<span>授信 ${formatMoney(detail.credit_limit)} · 已占用 ${formatMoney(detail.occupied_credit)} · 可用 ${formatMoney(detail.available_credit)}</span>` : ''}</div></section>
    <section class="detail-analysis-section"><p class="detail-section-label">图表分析</p><div class="charts"><section class="chartbox"><header class="chartbox-head"><h3>支出趋势</h3><p>${periodLabel}</p></header><canvas id="trendChart"></canvas><p id="trendEmpty" class="chart-empty" hidden>本期暂无支出趋势</p></section><section class="chartbox"><header class="chartbox-head"><h3>分类构成</h3><p>${periodLabel}</p></header><canvas id="categoryChart"></canvas><p id="categoryEmpty" class="chart-empty" hidden>本期暂无支出分类</p></section></div></section>
    <section class="detail-transactions-section"><p class="detail-section-label">交易明细</p><div id="accountTransactions" class="unified-list"></div></section>`;
  await seedAccountPager('desktop-account', '#accountTransactions', id, detail);
  document.querySelector('#periodMode').value = state.periodMode;
  await loadChartLibrary().catch(() => null);
  if (requestToken === desktopAccountRequestToken) renderCharts(detail, categoryPresentationMap());
}

async function refreshBudgets() {
  await ensureTaxonomy();
  const month = today().slice(0, 7);
  const [summary, budgets] = await Promise.all([
    api(`/budget-summary?month=${month}`),
    api(`/budgets?month=${month}`),
  ]);
  state.budgetSummary = summary;
  state.budgets = budgets;
  const total = summary.total;
  const totalBudget = total.budget_amount === null ? '本月尚未设置总预算' : `${formatMoney(total.spent_amount)} / ${formatMoney(total.budget_amount)}`;
  const allocation = summary.unallocated_amount === null ? '' : summary.unallocated_amount >= 0
    ? `分类预算尚未分配 ${formatMoney(summary.unallocated_amount)}`
    : `分类预算超出总预算 ${formatMoney(-summary.unallocated_amount)}`;
  document.querySelector('#budgetSummary').innerHTML = `<div class="budget-total-card"><div><p class="eyebrow">本月总预算</p><strong>${totalBudget}</strong><p class="meta">${allocation}</p></div>${total.budget_amount === null ? '<button class="primary" data-open="budgetDialog">设置总预算</button>' : budgetEditButton({ scope_type: 'total', scope_id: 'total' })}</div>${budgetProgress(total)}`;
  const activeBudgetByCategory = new Map(budgets.filter((item) => item.scope_type === 'category').map((item) => [item.scope_id, item]));
  const categoryRows = summary.categories.map((category) => {
    const budget = activeBudgetByCategory.get(category.id);
    const action = budget ? budgetEditButton(budget) : `<button data-open-budget-category="${category.id}">设置预算</button>`;
    if (category.budget_amount === null) {
      return `<section class="budget-category-card is-unbudgeted"><div class="budget-category-card-head"><strong>${escapeHtml(category.name)}</strong>${action}</div><p class="meta">本月支出 ${formatMoney(category.spent_amount)} · 未设置预算</p></section>`;
    }
    return `<section class="budget-category-card"><div class="budget-category-card-head"><strong>${escapeHtml(category.name)}</strong>${action}</div><p class="meta">本月支出 ${formatMoney(category.spent_amount)} / 预算 ${formatMoney(category.budget_amount)}</p>${budgetProgress(category)}</section>`;
  });
  const legacyAccounts = budgets.filter((item) => item.scope_type === 'account').map((budget) => `<div class="budget-row"><span>账户（兼容旧设置） · ${escapeHtml(state.accounts.find((item) => item.id === budget.scope_id)?.name || budget.scope_id)}</span><span class="amount">${formatMoney(budget.amount)}</span>${budgetEditButton(budget)}</div>`).join('');
  document.querySelector('#budgetRows').innerHTML = `<section class="budget-category-grid">${categoryRows.join('')}</section>${legacyAccounts ? `<div class="unified-list budget-legacy-accounts">${legacyAccounts}</div>` : ''}`;
}

async function refreshTaxonomy(month = today().slice(0, 7)) {
  const requestToken = ++taxonomyMonthRequestToken;
  await ensureTaxonomy();
  state.taxonomyMonth = month;
  document.querySelector('#taxonomyMonth').value = month;
  const [rules, transactions, summary] = await Promise.all([
    api('/classification-rules'), api('/transactions?limit=1000'), api(`/budget-summary?month=${month}`),
  ]);
  if (requestToken !== taxonomyMonthRequestToken) return;
  const budgetByCategory = new Map(summary.categories.map((category) => [category.id, category]));
  document.querySelector('#taxonomyRows').innerHTML = (state.taxonomy.categories || []).map((category) => {
    const budget = budgetByCategory.get(category.id);
    const details = categoryBudgetDetails(budget);
    const budgetMarkup = details ? `<div class="taxonomy-budget"><div class="taxonomy-budget-meta budget-${details.status}"><span>${details.spend}</span><span>${details.budget}</span><span>${details.usage}</span><span>${details.balance}</span></div>${budgetProgress(budget, false)}</div>` : '';
    return `<section class="taxonomy-category" style="--category-color:${escapeHtml(category.color || state.taxonomy.category_fallback_color)}"><span class="taxonomy-category-icon">${escapeHtml(category.icon || '•')}</span><div><strong>${escapeHtml(category.name)}</strong><p>${(category.children || []).map((item) => escapeHtml(item.name)).join('、')}</p>${budgetMarkup}</div></section>`;
  }).join('');
  const needsReview = transactions.filter((item) => item.classification_status === 'needs_review');
  document.querySelector('#ruleRows').innerHTML = rules.map((rule) => `<div class="rule-row"><span>${escapeHtml(rule.pattern)}<br><span class="meta">${rule.rule_type} · 优先级 ${rule.priority}</span></span></div>`).join('') + `<div class="rule-row"><span>待整理队列<br><span class="meta">${needsReview.length} 笔待确认分类</span></span></div>`;
}

function pagedRowsMarkup(pager, key, desktop = false) {
  const page = pager.state;
  const rows = page.items.map(item => transactionRow(item)).join('');
  const message = page.loading && !page.items.length ? renderState('loading-state', '正在加载明细')
    : !rows && !page.error ? renderState('list-state', '暂无交易', '调整筛选条件或新增一笔交易。') : '';
  const count = page.loading && !page.items.length ? '' : `<span>已显示 ${page.items.length} / ${page.total} 笔${!page.hasMore && page.total ? ' · 全部加载完成' : ''}</span>`;
  return `${desktop && rows ? ledgerColumnHead() : ''}${rows}${message}<div class="list-pagination" aria-live="polite">${count}${page.error ? `<span class="pagination-error">${escapeHtml(page.error)}</span>` : ''}${page.hasMore ? `<button type="button" data-load-more="${key}"${page.loading ? ' disabled' : ''}>${page.loading ? '正在加载…' : page.error ? '重试' : '加载更多'}</button>` : ''}</div>`;
}

const transactionPagers = {};
function pagerFor(key, selector, desktop = false) {
  if (!transactionPagers[key]) transactionPagers[key] = createTransactionPager(async query => {
    const page = await api(`/transactions?${buildQuery(query)}`);
    return { items: page.transactions, ...page.pagination };
  }, () => {
    const target = document.querySelector(selector);
    if (target) target.innerHTML = pagedRowsMarkup(transactionPagers[key], key, desktop);
  });
  return transactionPagers[key];
}

async function refreshLedger() {
  return pagerFor('desktop-ledger', '#ledgerRows', true).reset(state.filters);
}

function seedAccountPager(key, selector, accountId, detail) {
  return pagerFor(key, selector).reset({ account_id: accountId, start: detail.period_start, end: detail.period_end },
    { items: detail.transactions, ...detail.transaction_pagination });
}

let desktopAccountRequestToken = 0;
let mobileViewRequestToken = 0;

function categoryRootIdMap() {
  const map = new Map();
  (state.taxonomy?.categories || []).forEach((root) => {
    map.set(root.id, root.id);
    (root.children || []).forEach((child) => map.set(child.id, root.id));
  });
  return map;
}

function mobileCategoryBudgetMarkup(category, rows) {
  const details = categoryBudgetDetails(category);
  const budgetMarkup = details ? `<div class="mobile-category-budget"><div><strong>${escapeHtml(category.name)}</strong><span>${details.spend} · ${details.budget}</span><span class="budget-${details.status}">${details.balance}</span></div><strong class="budget-${details.status}">${details.usage}</strong></div>${budgetProgress(category, false)}` : `<h3>${escapeHtml(category.name)}</h3>`;
  const leafRows = rows.map((item) => `<div class="ledger-row category-stat-row"><span>${escapeHtml(item.label)}</span><span class="amount">${formatMoney(item.value)} · ${item.percentage.toFixed(0)}%</span></div>`).join('');
  return `<section class="mobile-category-section">${budgetMarkup}${leafRows ? `<div class="unified-list">${leafRows}</div>` : ''}</section>`;
}

async function renderMobile(tab, selectedMonth) {
  mobileViewRequestToken += 1;
  transactionPagers['mobile-ledger']?.invalidate();
  transactionPagers['mobile-account']?.invalidate();
  state.mobileTab = tab;
  state.mobileAccountId = null;
  const target = document.querySelector('#mobileContent');
  syncNavigationState(mobilePageForTab(tab));
  if (tab !== 'stats') mobileStatsRequestToken += 1;
  if (tab === 'home') {
    const accountHead = '<header class="mobile-account-head"><div><p class="eyebrow">账户列表</p><h1>账户</h1></div><button class="account-add-button" data-open="accountDialog" type="button">添加账户</button></header>';
    const accountGroups = groupAccountsForOverview(state.accounts).map((group) =>
      `<section class="mobile-account-group"><h2>${group.title}</h2><div class="unified-list">${group.accounts.map(rowAccount).join('')}</div></section>`
    ).join('') || renderState('list-state', '尚未添加账户', '添加账户后即可开始记录收支。');
    target.innerHTML = accountHead + accountGroups;
  } else if (tab === 'ledger') {
    target.innerHTML = '<div id="mobileLedgerRows" class="unified-list"></div>';
    await pagerFor('mobile-ledger', '#mobileLedgerRows').reset({});
  } else if (tab === 'stats') {
    const requestToken = ++mobileStatsRequestToken;
    const month = selectedMonth || state.mobileStatsMonth || today().slice(0, 7);
    state.mobileStatsMonth = month;
    await ensureTaxonomy();
    const [year, monthNumber] = month.split('-').map(Number);
    const [overview, categories, summary] = await Promise.all([
      api(`/overview?year=${year}&month=${monthNumber}`),
      api(`/stats/categories?year=${year}&month=${monthNumber}`),
      api(`/budget-summary?month=${month}`),
    ]);
    if (requestToken !== mobileStatsRequestToken) return;
    const rootIds = categoryRootIdMap();
    const rowsByRoot = new Map();
    const ungrouped = [];
    categories.forEach((item) => {
      const rootId = rootIds.get(item.id);
      if (!rootId) ungrouped.push(item);
      else rowsByRoot.set(rootId, [...(rowsByRoot.get(rootId) || []), item]);
    });
    const groups = summary.categories.filter((category) => category.budget_amount !== null || category.spent_amount !== 0 || rowsByRoot.has(category.id));
    const groupMarkup = groups.map((category) => mobileCategoryBudgetMarkup(category, rowsByRoot.get(category.id) || [])).join('');
    const ungroupedMarkup = ungrouped.length ? `<div class="unified-list">${ungrouped.map((item) => `<div class="ledger-row category-stat-row"><span>${escapeHtml(item.label)}</span><span class="amount">${formatMoney(item.value)} · ${item.percentage.toFixed(0)}%</span></div>`).join('')}</div>` : '';
    target.innerHTML = `<div class="page-head mobile-stats-head"><h2>统计</h2><input id="mobileStatsMonth" type="month" value="${month}"></div><p>收入 ${formatMoney(overview.income)} · 支出 ${formatMoney(overview.expense)}</p><div class="mobile-category-stats">${groupMarkup || '<p class="meta">暂无支出</p>'}${ungroupedMarkup}</div>`;
  } else {
    target.innerHTML = `<section class="mobile-settings"><h2>连接设置</h2><label>API 地址<input id="mobileApiUrl" value="${escapeHtml(baseUrl())}"></label><label>API Key<input id="mobileApiKey" type="password" value="${escapeHtml(apiKey())}"></label><button id="saveMobileSettings" class="primary">保存</button></section>`;
  }
}

async function renderMobileAccountDetail(accountId) {
  const requestToken = ++mobileViewRequestToken;
  mobileStatsRequestToken += 1;
  transactionPagers['mobile-ledger']?.invalidate();
  transactionPagers['mobile-account']?.invalidate();
  state.mobileAccountId = accountId;
  const account = state.accounts.find((item) => item.id === accountId);
  if (!account) return renderMobile('home');
  const mode = account.type === 'credit' && account.statement_day ? 'billing_cycle' : 'last_30_days';
  const detail = await api(`/accounts/${encodeURIComponent(accountId)}/insights?${buildQuery({ mode, anchor: today(), transaction_limit: 50 })}`);
  if (requestToken !== mobileViewRequestToken) return;
  document.querySelector('#mobileContent').innerHTML = `<section class="mobile-account-detail">
    <button type="button" data-mobile-account-back>‹ 返回账户</button>
    <h2>${escapeHtml(account.name)}</h2>
    <p class="meta">${formatPeriod(detail.period_start, detail.period_end)}</p>
    <div class="metrics"><strong>支出 ${formatMoney(detail.expense)}</strong>${detail.credit_limit !== undefined ? `<span>可用 ${formatMoney(detail.available_credit)}</span>` : ''}</div>
    <div id="mobileAccountTransactions" class="unified-list"></div>
  </section>`;
  await seedAccountPager('mobile-account', '#mobileAccountTransactions', accountId, detail);
}

async function openDialog(id, data = {}) {
  if (['transactionDialog', 'budgetDialog', 'ruleDialog'].includes(id)) await ensureTaxonomy();
  if (id === 'transactionDialog') populateTransactionForm(data);
  if (id === 'budgetDialog') populateBudgetForm(data);
  if (id === 'ruleDialog') refreshRuleTargets();
  if (id === 'accountDialog') {
    const account = data; const form = document.querySelector('#accountForm'); form.reset();
    const creating = !account.id;
    Object.entries(account).forEach(([field, value]) => { if (form.elements[field] && value !== null && value !== undefined) form.elements[field].value = value; });
    form.querySelector('#accountDialogTitle').textContent = creating ? '添加账户' : '账户设置';
    form.querySelector('[data-account-create-field]').hidden = !creating;
    const deactivateAccount = form.querySelector('#deactivateAccount');
    deactivateAccount.hidden = creating;
    form.querySelector('[data-account-save-label]').textContent = creating ? '添加' : '保存';
    if (creating) {
      form.elements.type.value = 'debit';
      form.elements.budget_period.value = 'calendar_month';
      form.elements.due_month_offset.value = '1';
      form.elements.initial_balance.value = '0';
    }
  }
  document.getElementById(id).showModal();
  if (id === 'transactionDialog') scheduleTransactionBudgetPreview();
}

async function refreshAfterSave() {
  state.accounts = await api('/accounts'); renderAccountList();
  populateFilterOptions();
  await refreshOverview();
  if (state.activePage === 'ledger') await refreshLedger();
  if (state.activePage === 'budget') await refreshBudgets();
  if (state.activePage === 'taxonomy') await refreshTaxonomy();
  if (state.selectedAccountId) await selectAccount(state.selectedAccountId, true);
  if (window.matchMedia('(max-width: 899px)').matches) {
    if (state.mobileAccountId) await renderMobileAccountDetail(state.mobileAccountId);
    else await renderMobile(state.mobileTab, state.mobileStatsMonth);
  } else syncNavigationState(state.activePage);
}

async function load() {
  document.querySelector('#accountList').innerHTML = renderState('loading-state', '正在加载账户');
  renderAccountDetailEmpty();
  const [accounts] = await Promise.all([api('/accounts'), refreshOverview()]);
  state.accounts = accounts;
  renderAccountList();
  if (!state.selectedAccountId) renderAccountDetailEmpty();
  await renderMobile('home'); showStatus('');
}

async function showDesktopPage(page) {
  state.activePage = page;
  syncNavigationState(page);
  document.querySelectorAll('.page').forEach((node) => node.classList.toggle('active', node.id === `${page}Page`));
  if (page === 'ledger') { document.querySelector('#ledgerRows').innerHTML = renderState('loading-state', '正在加载明细'); await ensureTaxonomy(); await refreshLedger(); }
  if (page === 'budget') await refreshBudgets();
  if (page === 'taxonomy') await refreshTaxonomy(state.taxonomyMonth || undefined);
}

function closeDialog(button) { button.closest('dialog').close(); }

function setup() {
  document.querySelector('#apiUrl').value = baseUrl(); document.querySelector('#apiKey').value = apiKey();
  syncNavigationState('overview');
  window.addEventListener('scroll', () => document.querySelector('.topnav').classList.toggle('is-scrolled', window.scrollY > 0), { passive: true });
  document.addEventListener('click', (event) => {
    const more = event.target.closest('[data-load-more]')?.dataset.loadMore;
    if (more) safe(() => transactionPagers[more]?.loadMore());
    const page = event.target.closest('[data-page]')?.dataset.page;
    if (page) safe(() => showDesktopPage(page));
    const accountId = event.target.closest('[data-account]')?.dataset.account;
    if (accountId) safe(() => window.matchMedia('(max-width: 899px)').matches ? renderMobileAccountDetail(accountId) : selectAccount(accountId));
    const editAccount = event.target.closest('[data-edit-account]')?.dataset.editAccount;
    if (editAccount) safe(() => openDialog('accountDialog', state.accounts.find((item) => item.id === editAccount)));
    const editTransaction = event.target.closest('[data-edit-transaction]')?.dataset.editTransaction;
    if (editTransaction) safe(async () => {
      const transaction = transactionForEdit(transactionsById, editTransaction);
      if (!transaction) throw new Error('未找到这条交易，请刷新页面后重试');
      await openDialog('transactionDialog', transaction);
    });
    const repaymentGroupId = event.target.closest('[data-reverse-repayment]')?.dataset.reverseRepayment;
    if (repaymentGroupId) safe(async () => {
      if (!confirm('确定撤销这笔信用卡还款吗？两个账户的余额都会恢复。')) return;
      await api(`/credit-card-repayments/${encodeURIComponent(repaymentGroupId)}`, { method: 'DELETE' });
      await refreshAfterSave();
    });
    const dialog = event.target.closest('[data-open]')?.dataset.open; if (dialog) safe(() => openDialog(dialog));
    const editBudget = event.target.closest('[data-edit-budget]')?.dataset.editBudget;
    if (editBudget) safe(() => {
      const [scope_type, scope_id] = editBudget.split('|');
      const existing = state.budgets.find((item) => item.scope_type === scope_type && item.scope_id === scope_id);
      openDialog('budgetDialog', { ...existing, scope_type, scope_id, effective_from: `${today().slice(0, 7)}-01` });
    });
    const categoryBudget = event.target.closest('[data-open-budget-category]')?.dataset.openBudgetCategory;
    if (categoryBudget) safe(() => openDialog('budgetDialog', { scope_type: 'category', scope_id: categoryBudget }));
    if (event.target.closest('#categoryPickerToggle')) { const toggle = document.querySelector('#categoryPickerToggle'); toggle.setAttribute('aria-expanded', toggle.getAttribute('aria-expanded') === 'true' ? 'false' : 'true'); renderCategoryPicker(); }
    const categoryId = event.target.closest('[data-category-choice]')?.dataset.categoryChoice; if (categoryId !== undefined) chooseCategory(categoryId);
    const categoryGroup = event.target.closest('[data-category-group]')?.dataset.categoryGroup; if (categoryGroup) {
      expandedCategoryGroups = new Set(nextExpandedCategoryGroupIds([...expandedCategoryGroups], categoryGroup));
      renderCategoryPicker();
      requestAnimationFrame(() => document.querySelector(`[data-category-group="${categoryGroup}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
    }
    if (event.target.closest('[data-close]')) closeDialog(event.target.closest('[data-close]'));
    if (event.target.closest('[data-mobile-account-back]')) safe(() => renderMobile('home'));
    const tab = event.target.closest('[data-mobile-tab]')?.dataset.mobileTab; if (tab) safe(() => renderMobile(tab));
    if (event.target.id === 'refreshInsights') safe(async () => { state.periodMode = document.querySelector('#periodMode').value; state.periodAnchor = document.querySelector('#periodAnchor').value; await selectAccount(state.selectedAccountId, true); });
    if (event.target.id === 'saveSettings') { localStorage.setItem('apiUrl', document.querySelector('#apiUrl').value.replace(/\/$/, '')); localStorage.setItem('apiKey', document.querySelector('#apiKey').value); safe(load); }
    if (event.target.id === 'saveMobileSettings') { localStorage.setItem('apiUrl', document.querySelector('#mobileApiUrl').value.replace(/\/$/, '')); localStorage.setItem('apiKey', document.querySelector('#mobileApiKey').value); safe(load); }
    if (event.target.id === 'deactivateAccount') safe(async () => { const id = document.querySelector('#accountForm [name=id]').value; await api(`/accounts/${id}`, { method: 'DELETE' }); document.querySelector('#accountDialog').close(); await refreshAfterSave(); });
  });
  document.addEventListener('input', (event) => {
    if (event.target.id === 'categoryPickerSearch') renderCategoryPicker();
    if (event.target.closest('#transactionForm') && ['amount', 'timestamp'].includes(event.target.name)) scheduleTransactionBudgetPreview();
  });
  document.querySelector('#transactionForm').addEventListener('change', (event) => {
    if (event.target.name === 'transaction_mode') { setTransactionFormMode(event.target.value); scheduleTransactionBudgetPreview(); }
    if (event.target.name === 'repayment_credit_account_id') syncRepaymentAccountChoices(event.currentTarget);
    if (['timestamp', 'category_id', 'excluded_from_stats'].includes(event.target.name)) scheduleTransactionBudgetPreview();
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'taxonomyMonth') safe(() => refreshTaxonomy(event.target.value));
    if (event.target.id === 'mobileStatsMonth') safe(() => renderMobile('stats', event.target.value));
  });
  document.querySelector('#budgetForm [name=scope_type]').addEventListener('change', () => safe(async () => { await ensureTaxonomy(); refreshBudgetTargets(); }));
  document.querySelector('#ruleForm [name=rule_type]').addEventListener('change', () => safe(async () => { await ensureTaxonomy(); refreshRuleTargets(); }));
  document.querySelector('#filterForm').addEventListener('submit', (event) => { event.preventDefault(); state.filters = Object.fromEntries(new FormData(event.currentTarget)); safe(refreshLedger); });
  document.querySelector('#resetFilters').addEventListener('click', () => {
    document.querySelector('#filterForm').reset();
    state.filters = {};
    safe(refreshLedger);
  });
  document.querySelector('#exportLink').addEventListener('click', (event) => safe(async () => { event.preventDefault(); const blob = await api(buildExportPath(state.filters)); const anchor = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'wallet-export.csv' }); anchor.click(); URL.revokeObjectURL(anchor.href); }));
  document.querySelector('#transactionForm').addEventListener('submit', (event) => safe(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    const mode = data.transaction_mode;
    delete data.transaction_mode;
    if (mode === 'credit_repayment') {
      const repayment = {
        source_account_id: data.account_id,
        credit_account_id: data.repayment_credit_account_id,
        amount: Number(data.amount),
        timestamp: data.timestamp,
      };
      if (data.description.trim()) repayment.description = data.description.trim();
      await api('/credit-card-repayments', { method: 'POST', body: JSON.stringify(repayment) });
    } else {
      delete data.repayment_credit_account_id;
      data.transaction_kind = mode;
      const payload = prepareTransactionPayload(data);
      payload.amount = normalizeTransactionAmount(
        payload.amount, payload.transaction_kind,
        form._originalTransaction?.transaction_kind === payload.transaction_kind
          ? form._originalTransaction.amount : undefined,
      );
      payload.tag_ids = [...form.querySelector('[name=tag_ids]').selectedOptions].map((option) => option.value);
      const id = form.dataset.transactionId;
      await api(id ? `/transactions/${id}` : '/transactions', { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    }
    form.closest('dialog').close();
    await refreshAfterSave();
  }));
  document.querySelector('#deleteTransaction').addEventListener('click', () => safe(async () => {
    const form = document.querySelector('#transactionForm');
    const id = form.dataset.transactionId;
    if (!id) return;
    const description = form.elements.description.value.trim() || '这笔交易';
    const amount = form.elements.amount.value;
    if (!confirm(`确定删除“${description}”（¥${amount}）吗？此操作无法撤销。`)) return;
    await api(`/transactions/${id}`, { method: 'DELETE' });
    form.closest('dialog').close();
    await refreshAfterSave();
  }));
  document.querySelector('#accountForm').addEventListener('submit', (event) => safe(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    const raw = Object.fromEntries(new FormData(form));
    delete raw.id;
    const target = accountSaveTarget(id);
    const saved = await api(target.path, {
      method: target.method,
      body: JSON.stringify(prepareAccountPayload(raw, !id)),
    });
    if (!id) state.selectedAccountId = saved.id;
    form.closest('dialog').close();
    await refreshAfterSave();
  }));
  document.querySelector('#budgetForm').addEventListener('submit', (event) => safe(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    data.amount = Number(data.amount);
    data.effective_from += '-01';
    await api('/budgets', { method: 'POST', body: JSON.stringify(data) });
    form.closest('dialog').close();
    await refreshBudgets();
  }));
  document.querySelector('#ruleForm').addEventListener('submit', (event) => safe(async () => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    data.priority = Number(data.priority);
    const target = data.rule_type === 'category' ? 'category_id' : data.rule_type === 'tag' ? 'tag_id' : 'transaction_kind';
    for (const field of ['category_id', 'tag_id', 'transaction_kind']) if (field !== target) delete data[field];
    await api('/classification-rules', { method: 'POST', body: JSON.stringify(data) });
    form.closest('dialog').close();
    await refreshTaxonomy();
  }));
}

if (typeof document !== 'undefined') { setup(); if (apiKey()) safe(load); else showStatus('请在设置中填写 API Key 后开始使用'); }
