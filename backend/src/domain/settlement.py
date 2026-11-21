"""两厂变更单与交割的编号、冲突与批次切换口径。"""

import hashlib
import json

from .spec import SPEC

FACTORIES = [factory["key"] for factory in SPEC["factories"]]
FACTORY_LABEL = {factory["key"]: factory["label"] for factory in SPEC["factories"]}
ACTIONS = tuple(SPEC["change_actions"])


def check_factory(factory):
    if factory not in FACTORIES:
        raise ValueError("厂别须为两厂之一，请重新选择。")
    return factory


def normalize_content(action, spec_id, name, fit_traits):
    """校验并规范化变更单内容，返回可指纹比较的规范副本。"""
    action = str(action)
    if action not in ACTIONS:
        raise ValueError("变更动作须为新增、更正或撤销。")
    spec_id = (spec_id or "").strip()
    if not 0 < len(spec_id) <= SPEC["fields"][0]["max_length"]:
        raise ValueError("规格编号须为 1 至 80 个字符。")
    name = (name or "").strip()
    traits = (fit_traits or "").strip()
    if action == "revoke":
        return {"action": action, "spec_id": spec_id, "name": "", "fit_traits": ""}
    if not 0 < len(name) <= SPEC["fields"][1]["max_length"]:
        raise ValueError("新增或更正规格时，内衬名称须为 1 至 120 个字符。")
    if not 0 < len(traits) <= SPEC["fields"][2]["max_length"]:
        raise ValueError("新增或更正规格时，必需适配特征须为 1 至 600 个字符。")
    parts = {part.strip() for part in traits.split("|")}
    if "" in parts:
        raise ValueError(f"规格「{spec_id}」含空特征，请检查 | 分隔符两侧。")
    return {"action": action, "spec_id": spec_id, "name": name,
            "fit_traits": "|".join(sorted(parts))}


def payload_hash(content):
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def sequence_held(numbers):
    """给定一厂已收到的单号（升序去重），返回因前面缺号而暂存的单号集合。

    编号从 1 起连续才就就位；已交割区段的单号视为已就位。
    """
    held = set()
    expected = 1
    for number in numbers:
        if number < expected:
            continue
        if number == expected:
            expected += 1
        else:
            held.add(number)
    return held


def conflict_numbers(numbers):
    """同一编号出现多条内容不同的记录时，返回待裁定的编号集合。"""
    counts = {}
    for number in numbers:
        counts[number] = counts.get(number, 0) + 1
    return {number for number, count in counts.items() if count > 1}


def missing_numbers(present, through):
    """1 至 through 内尚未收到的单号。"""
    received = set(present)
    return [number for number in range(1, through + 1) if number not in received]


def compact_numbers(numbers):
    """把编号列表压缩成便于阅读的文本，如「4、6」或「4、6、9 等 5 个」。"""
    ordered = sorted(numbers)
    if len(ordered) <= 5:
        return "、".join(str(number) for number in ordered)
    return "、".join(str(number) for number in ordered[:5]) + f" 等 {len(ordered)} 个"


def apply_orders(base_rows, orders):
    """把变更单按序应用到基线批次，返回带来源标记的新批次行。

    orders 为 (factory, order_no, action, spec_id, name, fit_traits) 元组，
    调用方已按 (order_no, factory) 排序。新增与更正按规格编号（忽略大小写）
    覆盖写入并记录来源厂与变更单号，撤销移除对应规格，规格缺失时忽略。
    """
    rows = {row["spec_id"].casefold(): dict(row) for row in base_rows}
    for factory, order_no, action, spec_id, name, fit_traits in orders:
        key = spec_id.casefold()
        if action == "revoke":
            rows.pop(key, None)
            continue
        row = rows.get(key, {"spec_id": spec_id})
        row.update(name=name, fit_traits=fit_traits,
                   source_factory=factory, source_order_no=order_no)
        rows[key] = row
    return [rows[key] for key in sorted(rows, key=lambda key: rows[key]["spec_id"].casefold())]
