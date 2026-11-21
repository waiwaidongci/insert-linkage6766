# 002 两厂变更单与交割

新增三张表，应用启动时由 SQLAlchemy 模型自动补建；既有表结构不变。

- `change_orders` 两厂各自连续编号的变更单（新增/更正/撤销规格）。以 `(factory, order_no, payload_hash)` 唯一：内容指纹相同的重发幂等，同厂同号不同内容并存形成待裁定冲突。`state` 取 `held`（缺号暂存）/ `ready`（已就位）/ `applied`（已交割）/ `void`（裁定作废），`ruled` 标记厂方裁定采用的记录。
- `delivery_notices` 厂方交割号及覆盖到的变更单号，以 `(factory, delivery_no, covers_through)` 唯一，状态口径与变更单一致。
- `settlement_cycles` 两厂交割号按顺序配对成的交割周期。`notice_1_id`、`notice_2_id`、`dataset_id` 各自唯一，保证同一交割号只参与一个周期、同一周期只发布一个批次；`state` 取 `collecting` / `switching`（带租约，中断可恢复）/ `published` / `failed`，`attempt` 记录切换尝试次数。

交割批次写入 `datasets`（`author` 为 `settlement`），每行生效规格在 JSON 行内携带 `source_factory` 与 `source_order_no` 溯源字段。
