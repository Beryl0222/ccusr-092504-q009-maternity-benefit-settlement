# 生育保障跨域结算账

本项目提供生育保障跨域结算账所需的领域事件交换约定与基础校验库。各接入方使用统一的聚合标识、事件版本和发生时间表达业务事实，避免跨系统交换时丢失来源顺序。

## 目录

- `contracts/domain.schema.json`：领域事件信封、已登记类型与事件-事实登记表。
- `data/sample.json`：单事件联调样例。
- `data/sample_flow.json`：异地分娩全流程联调样例（零自付结算、并发症入组、津贴拨付、重复受理拦截、病案修订后的冲正与分期追回）。
- `src/maternity_benefit_settlement/contracts.py`：不依赖第三方包的交换层校验器。
- `src/maternity_benefit_settlement/ledger.py`：领域规则，全部为可复算的纯函数。
- `tests/`：契约边界与领域规则检查。

## 事实与事件

结算账把以下事实作为独立但可对账的聚合：`insured_person`（参保关系）、`benefit_policy`（政策地区与生效期）、`delivery_episode`（分娩住院）、`service_package`（基本服务包）、`charge_item`（费用项目，含镇痛和药耗）、`complication_group`（并发症病组）、`personal_payment`（个人支付）、`fund_settlement`（基金结算）、`allowance_entitlement`（津贴资格）、`payout_account`（收款账户）、`settlement_case`（跨域结算案）。

事件类型登记在 schema 的 `x-event-aggregates` 中，每种事件只能落在登记的事实上；`CASE_ACCEPTED` 等跨地区请求必须携带 `business_key`。

## 领域机制（ledger.py）

- **稳定业务键**：`derive_business_key` 只依赖参保人、定点机构、住院号、待遇类别等业务事实，两地推导结果一致，重复受理在回放时被幂等拦截。
- **按发生时政策归类**：`classify_charge` 选取费用发生日有效的政策；零自付只覆盖合规范围（`out_of_pocket_fen`），非政策项目必须给出可解释明细。
- **支付不重复**：`find_scope_conflicts` 检出同一项目在基础分娩与并发症两个范围下都被支付的情形。
- **只重算受影响部分**：`affected_fact_types` 沿事实依赖图给出资格变化或病案更正的下游影响范围。
- **调整不覆盖原流水**：`plan_adjustment` 支持补付、冲正、分期追回，均引用原流水；分期计划金额必须合计相等、到期日递增。
- **检查点恢复**：`CheckpointLog` 拒绝重复执行已完成检查点，`remaining` 给出中断后待办步骤。
- **角色可见性**：`project_event` 按医院、参保地、家庭投影，账户等敏感字段剔除或脱敏；`decide_account_change` 要求账户变更经两名不同核验人确认。
- **复算**：`replay` 对同一事件序列必然得到同一账态，`clearing_report` 按地区对与流水类型汇总，供经办复算跨域清分与后续调整；`progress_view` 供家庭逐项查看费用与待遇进度。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
