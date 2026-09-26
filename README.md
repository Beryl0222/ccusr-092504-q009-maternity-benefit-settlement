# 生育保障跨域结算账

本项目提供生育保障跨域结算账的领域核心：十类独立但可对账的事实、按发生时政策的费用归类、只增不改的结算流水账，以及分角色视图。交换层沿用统一的事件信封契约，各接入方使用统一的聚合标识、事件版本和发生时间表达业务事实，避免跨系统交换时丢失来源顺序。

## 目录

- `contracts/domain.schema.json`：领域事件信封和已登记类型。
- `data/sample.json`：事件信封联调样例。
- `data/case_sample.json`：灵活就业人员异地分娩的完整事实包样例。
- `src/maternity_benefit_settlement/contracts.py`：不依赖第三方包的事件校验器。
- `src/maternity_benefit_settlement/facts.py`：十类事实与稳定业务键。
- `src/maternity_benefit_settlement/classification.py`：费用归类与支付去重。
- `src/maternity_benefit_settlement/ledger.py`：只增不改的流水账与调整。
- `src/maternity_benefit_settlement/recalculation.py`：局部重算。
- `src/maternity_benefit_settlement/pipeline.py`：支付检查点与中断恢复。
- `src/maternity_benefit_settlement/accounts.py`：收款账户双重核验。
- `src/maternity_benefit_settlement/views.py`：分角色视图与清分复算。
- `src/maternity_benefit_settlement/casefile.py`：事实包样例加载器。
- `tests/`：契约边界检查与各模块单元测试、端到端联调测试。

## 领域规则与模块对应

- 参保关系、政策地区与生效期、分娩住院、基本服务包、镇痛和药耗、并发症病组、个人支付、基金结算、津贴资格、收款账户是独立但可对账的事实（`facts.py`、`accounts.py`），金额一律以“分”计。
- 费用项目按发生时政策归类（`classification.classify_charge`）；零自付只覆盖基本服务包合规范围；非政策项目给出可解释明细。
- 基础分娩与并发症支付不得重复：同一费用项目只进入一个支付范围，基本服务包优先，重叠部分写入说明（`classification.resolve_payable`）。
- 跨地区请求使用稳定业务键（`facts.business_key`），流水按键幂等入账：同键同内容视为重试，同键不同内容拒绝并暴露冲突（`ledger.Ledger.post`）。
- 资格变化或病案更正只重算受影响费用，差额按支付范围汇总后以补付或冲正落账（`recalculation.py`）。
- 已到账资金通过补付、冲正或分期追回调整，均为引用原流水的新流水，原流水永不覆盖（`ledger.py`）。
- 支付步骤中断后从不可重复的检查点恢复，已完成步骤不重做（`pipeline.py`）。
- 收款账户变更需两名不同核验人、两个不同渠道确认（`accounts.apply_account_change`）。
- 家庭看到每项费用和待遇的处理进度；医院、参保地各见所需；经办可复算跨域清分并与流水核对（`views.py`）。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
