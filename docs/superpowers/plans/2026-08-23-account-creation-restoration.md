# Account Creation Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore account creation on desktop and mobile while preserving the existing account-edit flow.

**Architecture:** Reuse `accountDialog` for both modes. A small pure helper selects `POST /accounts` when no account ID exists and `PUT /accounts/{id}` otherwise; another helper normalizes numeric account fields. The dialog renderer controls create-only UI, and the existing post-save refresh path selects the newly created account.

**Tech Stack:** Native HTML/CSS, ES modules, Node test runner, Flask/SQLite API, Playwright browser validation.

---

### Task 1: Lock the account-create contract with failing tests

**Files:**
- Modify: `tests/frontend_contract.test.mjs`

- [ ] **Step 1: Add the desktop/mobile and dialog contract test**

```js
test('account creation is reachable on desktop and mobile',()=>{
  assert.match(html,/class="account-panel-head"[^]*data-open="accountDialog"[^]*添加账户/);
  assert.match(appSource,/class="mobile-account-head"[^]*data-open="accountDialog"[^]*添加账户/);
  assert.match(html,/id="accountDialogTitle"/);
  assert.match(html,/data-account-create-field[^>]*>初始余额<input name="initial_balance"/);
});
```

- [ ] **Step 2: Add request-routing and payload-normalization tests**

```js
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
```

- [ ] **Step 3: Run the focused test and confirm RED**

Run: `node --test tests/frontend_contract.test.mjs`

Expected: FAIL because the add-account entry, `initial_balance`, `accountSaveTarget`, and `prepareAccountPayload` do not exist.

- [ ] **Step 4: Commit the RED test**

```bash
git add tests/frontend_contract.test.mjs
git commit -m "test: cover account creation flow"
```

### Task 2: Implement create mode and both UI entry points

**Files:**
- Modify: `release/frontend/index.html`
- Modify: `release/frontend/app.mjs`
- Modify: `release/frontend/styles.css`

- [ ] **Step 1: Add pure save helpers to `app.mjs`**

```js
export const accountSaveTarget = (id) => ({
  path: id ? `/accounts/${encodeURIComponent(id)}` : '/accounts',
  method: id ? 'PUT' : 'POST',
});
export const prepareAccountPayload = (data, creating) => {
  const payload = { ...data };
  ['monthly_budget', 'credit_limit'].forEach((field) => {
    payload[field] = payload[field] === '' ? null : Number(payload[field]);
  });
  ['statement_day', 'due_day', 'due_month_offset'].forEach((field) => {
    payload[field] = payload[field] === '' ? null : Number(payload[field]);
  });
  if (creating) payload.initial_balance = payload.initial_balance === '' ? 0 : Number(payload.initial_balance);
  else delete payload.initial_balance;
  return payload;
};
```

- [ ] **Step 2: Add desktop and mobile entry points**

In `index.html`, add a `data-open="accountDialog"` button to `.account-panel-head`. In `renderMobile('home')`, always prepend:

```js
const accountHead = '<header class="mobile-account-head"><div><p class="eyebrow">账户列表</p><h1>账户</h1></div><button data-open="accountDialog" type="button">添加账户</button></header>';
```

- [ ] **Step 3: Add create-only dialog UI and mode rendering**

Add `id="accountDialogTitle"`, a create-only `initial_balance` label, and an explicit save label. When `data.id` is absent, show the create field, hide `#deactivateAccount`, set title to `添加账户`, and apply defaults. When it is present, hide the create field, show deactivation, and keep title `账户设置`.

```js
const creating = !account.id;
form.querySelector('#accountDialogTitle').textContent = creating ? '添加账户' : '账户设置';
form.querySelector('[data-account-create-field]').hidden = !creating;
form.querySelector('#deactivateAccount').hidden = creating;
if (creating) {
  form.elements.type.value = 'debit';
  form.elements.budget_period.value = 'calendar_month';
  form.elements.due_month_offset.value = '1';
  form.elements.initial_balance.value = '0';
}
```

- [ ] **Step 4: Route account submits through POST or PUT**

```js
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
```

- [ ] **Step 5: Style the new controls without changing the layout system**

Make `.account-panel-head` and `.mobile-account-head` flex containers with separated title/action areas. Keep the existing button tokens and add a mobile heading size consistent with the current shell. Ensure `[hidden]` remains hidden.

- [ ] **Step 6: Bump the `app.mjs` and `styles.css` query-string versions**

Change the frontend asset versions in `index.html` to a new account-create identifier so Safari does not keep the previous UI from cache.

- [ ] **Step 7: Run the focused test and confirm GREEN**

Run: `node --test tests/frontend_contract.test.mjs`

Expected: all frontend contract tests pass.

- [ ] **Step 8: Commit the implementation**

```bash
git add release/frontend/index.html release/frontend/app.mjs release/frontend/styles.css
git commit -m "feat: restore account creation"
```

### Task 3: Verify behavior and release readiness

**Files:**
- Modify only if a verified defect is found: `release/frontend/index.html`, `release/frontend/app.mjs`, `release/frontend/styles.css`, `tests/frontend_contract.test.mjs`

- [ ] **Step 1: Run the release verification suite**

Run: `PYTHON_BIN=/private/tmp/agent-ledger-test-venv/bin/python bash release/scripts/verify-release.sh`

Expected: all Python and frontend tests, compilation, static checks, `git diff --check`, and secret scan pass.

- [ ] **Step 2: Run browser interaction checks**

Open the local application with an isolated test database and verify desktop and mobile: open add dialog, create `招行信用卡（副卡）`, confirm it appears and becomes selected, then reopen it in edit mode and confirm initial balance is unavailable while deactivation remains available.

- [ ] **Step 3: Inspect the final diff and repository state**

Run: `git diff origin/main...HEAD --check && git status --short --branch`

Expected: only the approved design/plan, frontend implementation, and contract test are changed; working tree is clean.

- [ ] **Step 4: Push the verified branch and fast-forward GitHub main**

Push `codex/restore-account-creation`, verify remote ancestry, then update `origin/main` only after the branch remains based on the current remote main.

- [ ] **Step 5: Build and verify a rollback-capable NAS update archive**

Create the package from the published commit, include the persistent Docker build-context files and deployment script, verify archive contents and SHA-256, and report that NAS production deployment remains unverified until the user runs the deployment commands.
