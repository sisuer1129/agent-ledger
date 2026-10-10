# 信用卡账户合并

此一次性脚本将所有**有效**信用卡账户合并为“信用卡”，但保留通过 `--exclude` 指定的账户独立。合并后余额为各卡余额之和；交易、标签及成对还款记录保留，原卡账户停用。`credit_merge_origins` 表记录每笔迁移交易的原账户 ID，可与停用账户名称核对。

合并后的账户使用自然月，不继承任一银行的账单日、还款日和信用额度。若参与合并的账户存在账户级预算或月度预算，脚本会停止，待预算规则确定后再执行。

在部署该脚本的环境中，先预览：

```sh
python release/app/merge_credit_accounts.py /path/to/agent-ledger.db --exclude '要保留的副卡账户名'
```

核对账户、交易笔数和合计余额后，停止记账本服务，再执行：

```sh
python release/app/merge_credit_accounts.py /path/to/agent-ledger.db --exclude '要保留的副卡账户名' --apply
```

脚本会先用 SQLite 在线备份 API 写入数据库同目录下带时间戳的 `.bak` 文件，再在单个数据库事务中合并。首次执行后再次预览会提示没有待合并账户。不要在服务仍写入数据库时执行。
