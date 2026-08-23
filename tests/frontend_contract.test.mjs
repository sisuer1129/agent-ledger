import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
const html=fs.readFileSync(new URL('../release/frontend/index.html',import.meta.url),'utf8');
const css=fs.readFileSync(new URL('../release/frontend/styles.css',import.meta.url),'utf8');
const appSource=fs.readFileSync(new URL('../release/frontend/app.mjs',import.meta.url),'utf8');
const app=await import(new URL('../release/frontend/app.mjs',import.meta.url));
const charts=await import(new URL('../release/frontend/charts.mjs',import.meta.url));
test('responsive wallet shell contract',()=>{
  for(const id of ['desktopWorkspace','mobileApp','mobileContent','accountList','accountDetail','budgetPage','taxonomyPage','accountDialog','transactionDialog','budgetDialog','ruleDialog']) assert.match(html,new RegExp(`id="${id}"`));
  for(const token of ['data-mobile-tab="home"','data-mobile-tab="ledger"','data-mobile-tab="stats"','data-mobile-tab="settings"','data-rule-target="category"','data-rule-target="tag"','data-rule-target="kind"']) assert.ok(html.includes(token));
  for(const token of ['data-edit-account','data-edit-transaction','periodMode','periodAnchor']) assert.ok(appSource.includes(token));
  assert.match(html,/href="\/styles\.css\?v=20260823-account-create1"/); assert.match(html,/type="module" src="\/app\.mjs\?v=20260823-account-create1"/); assert.match(html,/<svg viewBox="0 0 24 24">/); assert.doesNotMatch(html,/＞?＋|＞?×/);
  for(const term of ['@media (min-width: 900px)','@media (max-width: 680px)','prefers-reduced-motion: reduce','prefers-reduced-transparency: reduce','prefers-contrast: more','saturate(180%)','width: min(440px, calc(100vw - 32px))','height: 320px','height: 240px !important','.mobile-settings']) assert.ok(css.includes(term));
  assert.doesNotMatch(css,/#f5f4ed|Georgia|Inter|Roboto/);
});
test('desktop overview uses aligned master-detail account cards',()=>{
  assert.match(html,/<div class="workspace"><section class="account-panel" aria-label="账户列表">/);
  assert.match(html,/<header class="account-panel-head"><div><p class="eyebrow">账户列表<\/p><h2>账户<\/h2><\/div><button/);
  assert.match(css,/\.account-panel, \.detail-panel\s*\{[^}]*border: 1px solid var\(--hairline\)[^}]*border-radius: var\(--r-panel\)[^}]*box-shadow: var\(--sh-card\)/);
  assert.match(css,/\.workspace\s*\{[^}]*grid-template-columns: minmax\(320px,\.7fr\) minmax\(0,1\.3fr\)/);
  assert.match(css,/@media \(max-width: 899px\) \{[^]*?\.workspace\s*\{\s*display: block/);
});
test('account creation is reachable on desktop and mobile',()=>{
  assert.match(html,/class="account-panel-head"[^]*?data-open="accountDialog"[^]*?添加账户/);
  assert.match(appSource,/class="mobile-account-head"[^]*?data-open="accountDialog"[^]*?添加账户/);
  assert.match(html,/id="accountDialogTitle"/);
  assert.match(html,/data-account-create-field[^>]*>初始余额<input name="initial_balance"/);
  for(const token of ['accountDialogTitle','data-account-create-field','deactivateAccount.hidden','state.selectedAccountId = saved.id']) assert.ok(appSource.includes(token));
});
test('account save helpers distinguish create from edit',()=>{
  assert.deepEqual(app.accountSaveTarget(''), { path: '/accounts', method: 'POST' });
  assert.deepEqual(app.accountSaveTarget('card-1'), { path: '/accounts/card-1', method: 'PUT' });
  assert.deepEqual(app.prepareAccountPayload({
    name: '招行信用卡（副卡）', type: 'credit', initial_balance: '', monthly_budget: '',
    credit_limit: '50000', statement_day: '12', due_day: '1', due_month_offset: '1',
  }, true), {
    name: '招行信用卡（副卡）', type: 'credit', initial_balance: 0, monthly_budget: null,
    credit_limit: 50000, statement_day: 12, due_day: 1, due_month_offset: 1,
  });
  assert.equal('initial_balance' in app.prepareAccountPayload({ initial_balance: '-100' }, false), false);
});
test('ledger export is presented as a dedicated header action',()=>{
  assert.match(html,/<div class="page-head ledger-page-head"><h1>收支明细<\/h1><a id="exportLink" class="export-button"/);
  assert.doesNotMatch(html,/<form id="filterForm" class="filters">[^]*id="exportLink"/);
  assert.match(css,/\.export-button\s*\{[^}]*display:\s*inline-flex/);
  assert.match(css,/\.ledger-page-head\s*\{/);
});
test('ledger filters keep primary controls visible and place low-frequency fields in details',()=>{
  const ledgerForm = html.match(/<form id="filterForm" class="filters">([\s\S]*?)<\/form>/)?.[1] || '';
  for (const name of ['query', 'start', 'end', 'account_id', 'category_id', 'transaction_kind']) {
    assert.match(ledgerForm, new RegExp(`name="${name}"`));
  }
  assert.match(ledgerForm,/class="filter-main"/);
  assert.match(ledgerForm,/<details class="filter-more">/);
  for (const name of ['tag_id', 'min_amount', 'max_amount']) assert.match(ledgerForm, new RegExp(`name="${name}"`));
  assert.match(ledgerForm,/id="resetFilters"/);
  assert.match(css,/\.filter-main\s*\{[^}]*grid-template-columns/);
  assert.match(css,/\.filter-more-fields\s*\{[^}]*grid-template-columns/);
  assert.match(appSource,/resetFilters/);
});
test('frontend state covers mobile, dialogs, retries and chart fallbacks',()=>{
  for(const token of ['async function renderMobile','refreshBudgetTargets','refreshRuleTargets','function safe','showStatus','refreshAfterSave','editTransaction','deactivateAccount','renderCharts(detail, categoryPresentationMap())','mobile-settings','预算 ${formatMoney(account.monthly_budget)}']) assert.ok(appSource.includes(token));
});
test('primary buttons keep a visible blue background on hover',()=>{
  assert.match(css,/button\.primary:hover\s*\{[^}]*background:\s*var\(--accent\)/);
});
test('frontend formatting and query helpers',()=>{assert.equal(app.formatMoney(126.1),'¥126.10');assert.equal(app.formatPeriod('2026-06-13','2026-07-12'),'2026/6/13—2026/7/12');assert.equal(app.buildQuery({account_id:'a 1',tag_id:'family_member_a'}).toString(),'account_id=a+1&tag_id=family_member_a');assert.equal(app.budgetState(79.9),'normal');assert.equal(app.budgetState(80),'warning');assert.equal(app.budgetState(100),'over')});
test('ledger export URL always bypasses an older browser cache',()=>{
  assert.equal(
    app.buildExportPath({account_id:'card'}, 1721986000123),
    '/export.csv?account_id=card&_fresh=1721986000123',
  );
});
test('api rejects a successful response whose body is not valid JSON',async()=>{
  const previousFetch=globalThis.fetch;
  const previousLocalStorage=globalThis.localStorage;
  globalThis.localStorage={getItem:(key)=>key==='apiKey'?'test-key':''};
  globalThis.fetch=async()=>({
    status:200,
    ok:true,
    headers:{get:()=> 'application/json'},
    json:async()=>{throw new SyntaxError('Unexpected token');},
  });
  try {
    await assert.rejects(
      app.api('/budget-summary'),
      (error)=>error.type==='server' && error.message==='服务返回格式异常',
    );
  } finally {
    if(previousFetch===undefined) delete globalThis.fetch; else globalThis.fetch=previousFetch;
    if(previousLocalStorage===undefined) delete globalThis.localStorage; else globalThis.localStorage=previousLocalStorage;
  }
});
test('transaction form submits expenses as negative amounts',()=>{
  assert.equal(app.normalizeTransactionAmount(7,'expense'),-7);
  assert.equal(app.normalizeTransactionAmount(-7,'expense'),-7);
  assert.equal(app.normalizeTransactionAmount(-7,'income'),7);
  assert.equal(app.normalizeTransactionAmount(7,'refund'),7);
});
test('ledger rows expose complete category labels and repayment fields respect hidden state',()=>{
  assert.equal(app.formatTransactionCategory({
    category_primary_name:'日用购物', category_secondary_name:'食品杂货',
  }),'日用购物 · 食品杂货');
  assert.equal(app.formatTransactionCategory({category_primary_name:null, category_secondary_name:null}), '');
  assert.match(appSource,/formatTransactionCategory\(transaction\)/);
  assert.match(css,/\[data-transaction-field\]\[hidden\]\s*\{[^}]*display:\s*none\s*!important/);
});
test('desktop ledger gives transaction categories a dedicated middle column',()=>{
  assert.match(appSource,/class="ledger-row transaction-row"/);
  assert.match(appSource,/class="ledger-category \$\{categoryState\}"/);
  assert.match(css,/@media \(min-width: 900px\) \{[^]*?#ledgerRows \.transaction-row\s*\{[^}]*grid-template-columns/);
  assert.match(css,/@media \(max-width: 899px\) \{[^]*?\.ledger-category\s*\{\s*display:\s*none/);
});
test('ledger category labels use complete neutral tags with semantic dots',()=>{
  for (const token of ['is-expense', 'is-income', 'is-unclassified', 'ledger-category-dot']) assert.ok(appSource.includes(token));
  assert.match(css,/\.ledger-category\s*\{[^}]*background:\s*var\(--hover\)[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css,/\.ledger-category\.is-expense \.ledger-category-dot\s*\{[^}]*background:\s*var\(--heat\)/);
  assert.match(css,/\.ledger-category\.is-income \.ledger-category-dot\s*\{[^}]*background:\s*#2f8f5b/);
  assert.doesNotMatch(css,/#ledgerRows \.ledger-category\s*\{[^}]*border-inline-start/);
});
test('desktop ledger renders one aligned table header with a shifted-left amount column',()=>{
  assert.match(appSource,/class="ledger-column-head"/);
  assert.match(appSource,/<span>消费名称<\/span><span>类别<\/span><span>金额<\/span><span>操作<\/span>/);
  assert.match(css,/#ledgerRows \.ledger-column-head, #ledgerRows \.transaction-row\s*\{[^}]*grid-template-columns/);
  assert.match(css,/#ledgerRows \.transaction-row \.amount\s*\{[^}]*justify-self:\s*start/);
});
test('desktop ledger reserves a no-wrap action column without squeezing edit controls',()=>{
  assert.match(css,/grid-template-columns:\s*minmax\(240px,\s*1\.2fr\) minmax\(160px,\s*\.85fr\) 110px 76px/);
  assert.match(css,/#ledgerRows \.transaction-row > button\s*\{[^}]*min-width:\s*56px[^}]*white-space:\s*nowrap/);
});
test('automatic category selection omits the empty category from the payload',()=>{
  assert.deepEqual(app.prepareTransactionPayload({
    amount: -7, category_id: '', transaction_kind: 'expense', description: '便利店',
  }), {
    amount: -7, transaction_kind: 'expense', description: '便利店',
  });
});
test('editing a transaction exposes a confirmed delete action',()=>{
  assert.match(html,/id="deleteTransaction"/);
  assert.ok(appSource.includes("confirm("));
  assert.ok(appSource.includes("method: 'DELETE'"));
});
test('editing reuses the rendered transaction and handles a missing record safely',()=>{
  const transaction={id:'tx-1',description:'示例交易',tag_ids:['family_member_a']};
  const cached=new Map([[transaction.id,transaction]]);
  assert.equal(app.transactionForEdit(cached,'tx-1'),transaction);
  assert.equal(app.transactionForEdit(cached,'missing'),null);
  assert.match(appSource,/transactionsById\.set\(transaction\.id, transaction\);/);
  assert.match(appSource,/const transaction = transactionForEdit\(transactionsById, editTransaction\);/);
  assert.doesNotMatch(appSource,/api\('\/transactions\?limit=1000'\); await openDialog\('transactionDialog', rows\.find/);
  assert.doesNotMatch(appSource,/rows\.find\(/);
});
test('repayment mode exposes source and credit account controls',()=>{
  assert.match(html,/name="transaction_mode" type="radio" value="credit_repayment"/);
  assert.match(html,/name="repayment_credit_account_id"/);
  assert.match(html,/将自动生成两笔还款记录，不计入消费和预算统计。/);
  assert.ok(appSource.includes("'/credit-card-repayments'"));
  assert.ok(appSource.includes('setTransactionFormMode'));
  assert.deepEqual(app.repaymentSourceAccounts([
    {id:'salary',type:'debit'}, {id:'card',type:'credit'}, {id:'wallet',type:'stored_value'},
  ], 'card').map((account)=>account.id), ['salary','wallet']);
});
test('overview groups real account types with credit cards first',()=>{
  const groups=app.groupAccountsForOverview([
    {id:'shopping-1',name:'购物卡甲',type:'shopping_card'},
    {id:'credit-1',name:'信用账户甲',type:'credit'},
    {id:'debit-1',name:'储蓄账户甲',type:'debit'},
    {id:'wechat-1',name:'微信账户甲',type:'wechat'},
    {id:'credit-2',name:'信用账户乙',type:'credit'},
    {id:'cash-1',name:'现金账户甲',type:'cash'},
  ]);
  assert.deepEqual(groups.map((group)=>[group.title,group.accounts.map((account)=>account.name)]),[
    ['信用卡',['信用账户甲','信用账户乙']],
    ['储蓄卡',['储蓄账户甲','微信账户甲']],
    ['其他账户',['购物卡甲','现金账户甲']],
  ]);
  assert.match(html,/<option value="wechat">微信账户<\/option>/);
  assert.match(html,/<option value="shopping_card">购物卡<\/option>/);
});
test('mobile account detail flow has its own renderer and return action',()=>{
  for(const token of ['async function renderMobileAccountDetail','data-mobile-account-back','mobile-account-detail']) assert.ok(appSource.includes(token));
});
test('mobile home reuses account grouping markup',()=>{
  assert.match(appSource,/groupAccountsForOverview\(state\.accounts\)/);
  assert.ok(appSource.includes('mobile-account-group'));
});
test('category picker keeps automatic suggestion and approved common categories first',()=>{
  assert.deepEqual(app.COMMON_CATEGORY_IDS,[
    'expense_dining_meal','expense_daily_shopping_groceries','expense_vehicle_charging_fuel',
    'expense_transport_temporary_parking','expense_digital_ai','expense_network_service',
  ]);
  assert.deepEqual(app.categoryPickerGroups({categories:[
    {id:'food',name:'餐饮',children:[{id:'meal',name:'午晚餐'}]},
    {id:'ai_network',name:'AI 与网络服务',children:[{id:'ai_subscription',name:'AI 订阅'}]},
  ]}),[
    {id:'food',name:'餐饮',children:[{id:'meal',name:'午晚餐'}]},
    {id:'ai_network',name:'AI 与网络服务',children:[{id:'ai_subscription',name:'AI 订阅'}]},
  ]);
});
test('category picker opens one group at a time so the selected group stays visible',()=>{
  assert.deepEqual(app.nextExpandedCategoryGroupIds(['food'],'social'),['social']);
  assert.deepEqual(app.nextExpandedCategoryGroupIds(['social'],'social'),[]);
  assert.ok(appSource.includes('scrollIntoView'));
});
test('category picker keeps the full category area independently scrollable',()=>{
  assert.match(css,/\.category-picker\s*\{[^}]*grid-template-rows:\s*auto auto auto auto minmax\(0,1fr\)/);
  assert.match(css,/\.category-groups\s*\{[^}]*overflow-y:\s*auto/);
});
test('initial load defers non-essential pages and chart library',()=>{
  const loadSource=appSource.match(/async function load\(\) \{[\s\S]*?\n\}/)?.[0] || '';
  assert.match(loadSource,/Promise\.all\(\[api\('\/accounts'\), refreshOverview\(\)\]\)/);
  assert.doesNotMatch(loadSource,/refreshBudgets\(\)|refreshTaxonomy\(\)|refreshLedger\(\)|selectAccount\(/);
  assert.ok(appSource.includes('async function ensureTaxonomy'));
  assert.ok(appSource.includes('async function loadChartLibrary'));
  assert.doesNotMatch(html,/cdn\.jsdelivr\.net\/npm\/chart\.js/);
});
test('first account selection loads taxonomy before rendering the category chart',()=>{
  const selectSource=appSource.match(/async function selectAccount\([\s\S]*?\n\}/)?.[0] || '';
  assert.match(selectSource,/const \[detail\] = await Promise\.all\(\[[\s\S]*?ensureTaxonomy\(\)[\s\S]*?\]\);/);
  assert.match(selectSource,/renderCharts\(detail, categoryPresentationMap\(\)\)/);
});
test('category charts use configured category presentation in detail and primary modes',()=>{
  const summary=charts.categorySummary([
    {amount:-7,category_id:'shopping',excluded_from_stats:false},
    {amount:-25,category_id:'public_transit',excluded_from_stats:false},
  ],{
    shopping:{label:'食品杂货',primaryLabel:'日用购物',color:'#78A4FA',rootColor:'#5B8FF9'},
    public_transit:{label:'公共交通',primaryLabel:'交通出行',color:'#36B37E',rootColor:'#36B37E'},
  });
  assert.deepEqual(summary,{labels:['食品杂货','公共交通'],values:[7,25],colors:['#78A4FA','#36B37E']});
  assert.deepEqual(charts.categorySummary([
    {amount:-7,category_id:'shopping',excluded_from_stats:false},
  ],{shopping:{label:'食品杂货',primaryLabel:'日用购物',color:'#78A4FA',rootColor:'#5B8FF9'}},'primary'),{
    labels:['日用购物'],values:[7],colors:['#5B8FF9'],
  });
});
test('category charts take configured presentation colours instead of a random palette',async()=>{
  assert.doesNotMatch(await fs.promises.readFile(new URL('../release/frontend/charts.mjs',import.meta.url),'utf8'),/CATEGORY_CHART_COLORS|codePointAt|hsl\(/);
  assert.match(appSource,/function categoryPresentationMap\(\)/);
  assert.match(css,/--category-color/);
  assert.match(appSource,/\.\/charts\.mjs\?v=20260726-category-colors1/);
});
test('taxonomy list uses configured root icons and colours',()=>{
  assert.match(appSource,/class="taxonomy-category"/);
  assert.match(appSource,/category\.icon/);
  assert.match(appSource,/category\.color/);
  assert.match(css,/\.taxonomy-category/);
});
test('budget and rule submissions keep their form reference through async saves',()=>{
  for (const formId of ['budgetForm', 'ruleForm']) {
    assert.match(appSource, new RegExp(`document\\.querySelector\\('#${formId}'\\)\\.addEventListener\\('submit', \\(event\\) => safe\\(async \\(\\) => \\{\\s*event\\.preventDefault\\(\\);\\s*const form = event\\.currentTarget;`));
  }
  assert.doesNotMatch(appSource,/event\.currentTarget\.closest\('dialog'\)\.close\(\)/);
});
test('budget page is driven by the shared monthly summary and supports editing total and root budgets',()=>{
  assert.match(html,/id="budgetSummary"/);
  assert.match(html,/name="scope_type"><option value="total">本月总预算<\/option><option value="category">一级分类<\/option><option value="account">账户（兼容旧设置）<\/option>/);
  assert.match(appSource,/function budgetCategoryOptions\(\)/);
  assert.match(appSource,/const month = today\(\)\.slice\(0, 7\);/);
  assert.match(appSource,/api\(`\/budget-summary\?month=\$\{month\}`\)/);
  assert.match(appSource,/data-edit-budget/);
  assert.match(appSource,/function populateBudgetForm\(budget = \{\}\)/);
  assert.match(appSource,/effective_from: `\$\{today\(\)\.slice\(0, 7\)\}-01`/);
});
test('budget categories render as responsive cards without changing budget data sources',()=>{
  const budgetSource = appSource.match(/async function refreshBudgets\(\) \{[\s\S]*?\n\}/)?.[0] || '';
  assert.match(budgetSource,/budget-category-grid/);
  assert.match(budgetSource,/budget-category-card/);
  assert.match(budgetSource,/category\.budget_amount === null/);
  assert.match(budgetSource,/budgetProgress\(category\)/);
  assert.match(css,/\.budget-category-grid\s*\{[^}]*display:\s*grid/);
  assert.match(css,/@media \(min-width: 900px\)\s*\{[\s\S]*\.budget-category-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0,\s*1fr\)\)/);
  assert.match(css,/\.budget-category-card\.is-unbudgeted\s*\{[^}]*background:\s*var\(--hover\)/);
});
test('overview budget card only consumes the shared summary and links to the budget page',()=>{
  assert.match(html,/id="overviewBudgetCard"/);
  assert.match(appSource,/api\(`\/budget-summary\?month=\$\{month\}`\)/);
  assert.match(html,/id="overviewBudgetCard" class="overview-budget-card" data-page="budget"/);
  assert.match(appSource,/summary\.alerts/);
  assert.match(appSource,/item\.usage_rate \* 100/);
  assert.match(appSource,/budgetProgress\(total, false\)/);
  const overviewSource = appSource.match(/async function refreshOverview\(\) \{[\s\S]*?\n\}/)?.[0] || '';
  assert.doesNotMatch(overviewSource,/\/transactions/);
});
test('desktop overview pairs monthly summary with the shared budget summary',()=>{
  assert.match(html,/class="overview-top-cards"/);
  for (const id of ['overviewBalance','overviewIncome','overviewExpense']) assert.match(html,new RegExp(`id="${id}"`));
  assert.equal(app.monthlyBalance({income: 100, expense: 140, refunds: 5}), -35);
  assert.equal(app.monthlyBalance({income: 100, expense: 40}), 60);
  assert.match(appSource,/overviewBalance.*monthlyBalance\(overview\)/);
  assert.match(appSource,/overview-budget-main/);
  assert.match(appSource,/overview-budget-alerts/);
  assert.match(css,/\.overview-top-cards\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) minmax\(0,\s*1fr\)/);
  assert.match(css,/\.overview-budget-alert\s*\{[^}]*background:\s*transparent/);
  assert.match(css,/@media \(max-width: 899px\)\s*\{[\s\S]*\.overview-top-cards\s*\{[^}]*grid-template-columns:\s*1fr/);
});
test('desktop budget card gives classification alerts a calm, readable side panel',()=>{
  assert.match(appSource,/overview-budget-alert-title">分类提醒/);
  assert.match(appSource,/overview-budget-link"[^>]*>查看预算详情 ›/);
  assert.match(css,/\.overview-budget-content\.has-alerts\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*\.58fr\) minmax\(0,\s*\.42fr\)/);
  assert.match(css,/\.overview-summary-card\s*\{[^}]*padding:\s*18px/);
  assert.match(css,/\.overview-budget-alert-title\s*\{[^}]*color:\s*var\(--text-3\)/);
});
test('overview makes monthly balance primary and limits budget reminders to two items',()=>{
  assert.match(appSource,/summary\.alerts\.slice\(0, 2\)/);
  assert.match(appSource,/overview-budget-alert-more/);
  assert.match(css,/\.overview-summary-card > strong\s*\{[^}]*font-size:\s*32px/);
  assert.match(css,/\.overview-budget-alert-more\s*\{[^}]*color:\s*var\(--accent\)/);
});
test('budget alerts and transaction preview use explicit budget language',()=>{
  assert.equal(app.formatBudgetAlert({name:'餐饮',status:'warning',usage_rate:.9,remaining_amount:10}),'餐饮预算已用 90%');
  assert.equal(app.formatBudgetAlert({name:'餐饮',status:'exceeded',usage_rate:1.2,remaining_amount:-20}),'餐饮预算超支 ¥20.00');
  assert.equal(app.formatBudgetImpactPreview({visible:true,category:{name:'餐饮'},current:{spent_amount:90,budget_amount:100},projected:{remaining_amount:-7,status:'exceeded'}}),'餐饮预算：已用 ¥90.00 / ¥100.00\n记入本笔后将超出预算 ¥7.00');
  assert.equal(app.formatBudgetImpactPreview({visible:false,reason:'unclassified'}),'选择分类后显示预算影响');
  assert.equal(app.formatBudgetImpactPreview({visible:false,reason:'unbudgeted',category:{name:'餐饮'}}),'餐饮本月尚未设置预算');
});
test('transaction budget preview debounces input and prevents stale responses from rendering',()=>{
  assert.match(html,/id="transactionBudgetImpact"/);
  assert.match(appSource,/\/budget-impact-preview/);
  assert.match(appSource,/const \{ kind: _kind, \.\.\.requestPayload \} = payload;/);
  assert.match(appSource,/JSON\.stringify\(requestPayload\)/);
  assert.match(appSource,/setTimeout\(async \(\) => \{[\s\S]*?\}, 300\);/);
  assert.match(appSource,/budgetImpactRequestToken/);
  assert.match(appSource,/if \(requestToken !== budgetImpactRequestToken\) return;/);
  assert.match(css,/\.budget-progress\s*\{[^}]*width:\s*min\(360px,\s*100%\)/);
  assert.match(css,/@media \(max-width: 899px\) \{[^]*?\.budget-progress\s*\{[^}]*width:\s*100%/);
});
test('category budget details use the same monthly summary values and preserve actual usage',()=>{
  assert.deepEqual(app.categoryBudgetDetails({
    name:'餐饮', budget_amount:100, spent_amount:90, usage_rate:.9, remaining_amount:10, status:'warning',
  }), {
    spend:'本月支出 ¥90.00', budget:'预算 ¥100.00', usage:'已用 90%', balance:'剩余 ¥10.00', status:'warning',
  });
  assert.deepEqual(app.categoryBudgetDetails({
    name:'餐饮', budget_amount:100, spent_amount:120, usage_rate:1.2, remaining_amount:-20, status:'exceeded',
  }), {
    spend:'本月支出 ¥120.00', budget:'预算 ¥100.00', usage:'已用 120%', balance:'超支 ¥20.00', status:'exceeded',
  });
  assert.equal(app.categoryBudgetDetails({budget_amount:null}), null);
});
test('desktop taxonomy and mobile stats consume the monthly budget summary without recalculating transactions',()=>{
  assert.match(html,/id="taxonomyMonth" type="month"/);
  assert.match(appSource,/async function refreshTaxonomy\(month = today\(\)\.slice\(0, 7\)\)/);
  assert.match(appSource,/api\(`\/budget-summary\?month=\$\{month\}`\)/);
  assert.match(appSource,/id="mobileStatsMonth"/);
  assert.match(appSource,/summary\.categories/);
  assert.match(appSource,/taxonomy-budget-meta/);
  assert.match(css,/\.taxonomy-budget-meta\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(css,/\.mobile-category-budget\s*\{[^}]*grid-template-columns/);
});
test('month switches ignore stale category and mobile statistics responses',()=>{
  assert.match(appSource,/taxonomyMonthRequestToken/);
  assert.match(appSource,/mobileStatsRequestToken/);
  assert.match(appSource,/if \(requestToken !== taxonomyMonthRequestToken\) return;/);
  assert.match(appSource,/if \(requestToken !== mobileStatsRequestToken\) return;/);
});

test('desktop and mobile navigation expose synchronized active-page state',()=>{
  assert.match(html,/data-page="overview"[^>]*aria-current="page"/);
  assert.match(html,/data-mobile-tab="home"[^>]*aria-current="page"/);
  assert.match(appSource,/function syncNavigationState\(page\)/);
  assert.match(appSource,/syncNavigationState\(page\)/);
  assert.match(appSource,/syncNavigationState\(mobilePageForTab\(tab\)\)/);
  assert.match(css,/\.navlinks button\.is-active\s*\{[^}]*color:\s*var\(--accent\)[^}]*background:\s*#eaf4ff/);
  assert.match(css,/\.bottomnav button\.is-active\s*\{[^}]*color:\s*var\(--accent\)/);
  assert.match(css,/\.bottomnav button\.is-active::before\s*\{/);
});

test('empty and loading content uses shared state containers instead of loose meta text',()=>{
  for (const token of ['list-state', 'panel-state', 'loading-state', 'error-state']) {
    assert.ok(appSource.includes(token));
    assert.ok(css.includes(`.${token}`));
  }
  assert.match(appSource,/function renderState\(kind, title, detail = '', action = ''\)/);
  assert.match(appSource,/renderState\('panel-state', '选择一个账户'/);
  assert.match(appSource,/renderState\('list-state', '暂无交易'/);
  assert.match(css,/\.list-state, \.panel-state\s*\{[^}]*min-height:\s*132px[^}]*padding:\s*24px/);
  assert.match(css,/\.loading-state::before\s*\{/);
});

test('unselected desktop account detail stays compact while loaded details retain their normal panel height',()=>{
  assert.match(css,/\.detail-panel\s*\{[^}]*min-height:\s*380px/);
  assert.match(css,/\.detail-panel:has\(\.panel-state\)\s*\{[^}]*min-height:\s*200px/);
  assert.match(css,/\.detail-panel \.panel-state\s*\{[^}]*align-content:\s*start/);
});

test('desktop account detail has explicit sections and keeps chart titles for empty states',()=>{
  const selectSource=appSource.match(/async function selectAccount\([\s\S]*?\n\}/)?.[0] || '';
  for (const token of ['detail-period-section', 'detail-summary-section', 'detail-analysis-section', 'detail-transactions-section', '周期控制', '指标摘要', '图表分析', '交易明细', '支出趋势', '分类构成']) assert.ok(selectSource.includes(token));
  assert.match(css,/\.chartbox-head\s*\{[^}]*margin-bottom:\s*8px/);
  assert.match(css,/\.detail-transactions-section\s*\{[^}]*margin-top:\s*24px/);
  assert.match(css,/\.chartbox:has\(\.chart-empty:not\(\[hidden\]\)\) canvas\s*\{[^}]*display:\s*none/);
});

test('mobile global status clears the fixed bottom navigation and safe area',()=>{
  assert.match(css,/@media \(max-width: 899px\) \{[^]*?#status\s*\{[^}]*bottom:\s*calc\(var\(--mobile-nav-h\) \+ env\(safe-area-inset-bottom\) \+ 12px\)/);
  assert.match(css,/:root\{[^}]*--mobile-nav-h:\s*72px/);
});

test('transaction dialog keeps its form actions reachable in a constrained mobile viewport',()=>{
  assert.match(css,/#transactionDialog\s*\{[^}]*max-height:\s*calc\(100dvh - 32px\)[^}]*overflow-y:\s*auto/);
  assert.match(css,/#transactionDialog \.dialog-actions\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0/);
  assert.match(css,/@media \(max-width: 360px\) \{[^]*?\.transaction-mode\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css,/#transactionDialog \.category-picker\s*\{[^}]*max-height:/);
});
