# server-py/app/services/erp/parser.py
# 自然语言 → 结构化表单：用结构化输出解析用户的口语化描述
import json
import re
from datetime import date, datetime, timedelta
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from app.services.model import create_chat_model

_model = create_chat_model(temperature=0)

_T = TypeVar("_T", bound=BaseModel)


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON 对象。

    兼容以下情况：
    - 纯 JSON：直接解析
    - ```json ... ``` 代码块包裹
    - JSON 前后混有解释文本（取第一个 { 到最后一个 } 的片段）
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"模型输出不是合法 JSON：{text[:300]}")


def _unwrap(data: dict, hint: str) -> dict:
    """递归解包外层键，找到包含目标字段（hint）的结构。

    DeepSeek json_mode 可能把表单包一层或多层外层键，
    例如 {"报销单": {...}} 或 {"data": {"报销单": {...}}}。
    """
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if hint in node:
                return node
            stack.extend(v for v in node.values() if isinstance(v, dict))
    return data


# 报销类型：中文 → 英文枚举（模型可能输出中文）
_EXPENSE_TYPE_MAP = {
    "travel": "travel", "差旅": "travel", "差旅费": "travel", "出差": "travel",
    "meal": "meal", "餐饮": "meal", "餐饮费": "meal", "用餐": "meal",
    "office": "office", "办公": "office", "办公用品": "office",
    "training": "training", "培训": "training",
    "other": "other", "其他": "other",
}

# 请假类型：中文 → 英文枚举
_LEAVE_TYPE_MAP = {
    "annual": "annual", "年假": "annual",
    "personal": "personal", "事假": "personal",
    "sick": "sick", "病假": "sick",
    "compensatory": "compensatory", "调休": "compensatory",
    "marriage": "marriage", "婚假": "marriage",
    "maternity": "maternity", "产假": "maternity",
}

# 报销类型：关键词模糊匹配（兜底，覆盖模型输出的各种中文变体）
_EXPENSE_TYPE_KEYWORDS = [
    ("travel", ("差旅", "出差", "商务", "旅途", "高铁", "火车", "机票", "住宿")),
    ("meal", ("餐饮", "用餐", "餐费", "吃饭", "招待")),
    ("office", ("办公", "文具", "耗材", "打印")),
    ("training", ("培训", "学习", "课程", "考证")),
    ("other", ()),
]

# 请假类型：关键词模糊匹配（兜底）
_LEAVE_TYPE_KEYWORDS = [
    ("annual", ("年假",)),
    ("personal", ("事假",)),
    ("sick", ("病假",)),
    ("compensatory", ("调休",)),
    ("marriage", ("婚假",)),
    ("maternity", ("产假",)),
]


def _fuzzy_map(raw: str, groups: list[tuple[str, tuple[str, ...]]]) -> str | None:
    """按关键词包含关系模糊匹配枚举值。"""
    for value, keywords in groups:
        if any(k in raw for k in keywords):
            return value
    return None

# 中文数字 → 阿拉伯数字（"两晚""三天"等）
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:  # 支持"十一"=11、"二十"=20
        parts = s.split("十")
        tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return None


def _annotate_durations(text: str, data: dict) -> dict:
    """根据原文中的晚数/天数标注住宿/餐饮条目，并计算每晚/每天单价。

    用代码确定性计算，不依赖模型算术：
    - "住宿两晚共1100元" → 条目名"住宿费（2晚）" + unitPrice=550
    - "餐饮三天共420元" → 条目名"餐饮费（3天）" + unitPrice=140
    """
    night_m = re.search(r"([0-9一两二三四五六七八九十]+)\s*晚", text)
    day_m = re.search(r"([0-9一两二三四五六七八九十]+)\s*天", text)
    nights = _to_int(night_m.group(1)) if night_m else None
    days = _to_int(day_m.group(1)) if day_m else None

    for item in data.get("items", []):
        name = item.get("name", "")
        amount = item.get("amount", 0)
        if "住宿" in name and nights:
            item["name"] = f"{name}（{nights}晚）"
            item["unitPrice"] = round(amount / nights, 2)
        elif "餐饮" in name and days:
            item["name"] = f"{name}（{days}天）"
            item["unitPrice"] = round(amount / days, 2)
    return data


async def _extract_structured(model, schema: type[_T], messages: list[dict], hint: str,
                              type_map: dict | None = None,
                              type_keywords: list[tuple[str, tuple[str, ...]]] | None = None) -> _T:
    """手动调用模型并解析 JSON 到 Pydantic 模型，兼容 DeepSeek 各种输出习惯。"""
    resp = await model.ainvoke(messages)
    content = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
    data = _extract_json(content)
    data = _unwrap(data, hint)
    # 容错：模型把 type 输出成中文（如"差旅费""商务出差"），归一化为英文枚举
    if isinstance(data.get("type"), str):
        raw = data["type"].strip()
        mapped = type_map.get(raw) if type_map else None
        if not mapped and type_keywords:
            mapped = _fuzzy_map(raw, type_keywords)
        if mapped:
            data["type"] = mapped
        else:
            # 最终兜底：无法识别时按 other 处理，避免接口 500
            data["type"] = "other"
            data.setdefault("warnings", []).append(f"费用类型“{raw}”无法识别，已按“其他”处理")
    # 容错：可选字段被模型显式输出为 null 时，按缺省处理
    for key in ("warnings", "dept", "emergencyContact", "note", "date"):
        if data.get(key) is None:
            data[key] = [] if key == "warnings" else None
    try:
        return schema.model_validate(data)
    except Exception as err:
        raise ValueError(f"表单校验失败：{err}；原始输出：{content[:500]}") from err


# ── 报销申请解析 ──────────────────────────────────────────────
class ExpenseItem(BaseModel):
    name: str = Field(description='费用项目名称，如"高铁票""住宿费"')
    amount: float = Field(description="金额，单位：元")
    date: str | None = Field(default=None, description="发生日期，格式 YYYY-MM-DD")
    note: str | None = Field(default=None, description="备注")


class ExpenseForm(BaseModel):
    type: Literal["travel", "meal", "office", "training", "other"] = Field(
        description="费用类型：travel=差旅, meal=餐饮, office=办公用品, training=培训, other=其他")
    items: list[ExpenseItem] = Field(description="费用明细列表")
    totalAmount: float = Field(description="总金额，单位：元")
    reason: str = Field(description="报销事由，20字以内")
    dept: str | None = Field(default=None, description="报销部门")
    warnings: list[str] = Field(default_factory=list, description="发现的异常或需要注意的地方，如金额超标、信息不全等")


async def parse_expense_form(text: str) -> dict:
    today = date.today().isoformat()

    result: ExpenseForm = await _extract_structured(
        _model,
        ExpenseForm,
        [
            {
                "role": "system",
                "content": f"""你是报销单填写助手。从用户的自然语言描述中提取报销信息，生成结构化表单。
你必须以 JSON 格式（json）输出，字段直接放在 JSON 顶层，不要包裹在任何外层键中（不要输出类似 {{"报销单": ...}} 的结构）。
字段及类型：type, items, totalAmount, reason, dept, warnings。
今天是 {today}。
规则：
1. 如果用户说"上周"，根据今天日期推算具体日期
2. 金额务必精确，提到"约""大概"时保留原数字
3. 如果描述中有金额超过单笔3000元的项目，在 warnings 里提示
4. 如果报销事由不明确，在 warnings 里提示需要补充
5. totalAmount 等于所有 items 的 amount 之和
6. 按晚/天计费的条目（住宿、餐饮等），如果用户提到了晚数或天数，在 name 里标注，例如 "住宿费（2晚）"、"餐饮费（3天）"，以便审批时按每晚/每天单价判断""",
            },
            {"role": "user", "content": text},
        ],
        hint="totalAmount",
        type_map=_EXPENSE_TYPE_MAP,
        type_keywords=_EXPENSE_TYPE_KEYWORDS,
    )

    data = result.model_dump()
    # 用代码从原文提取晚数/天数，标注条目并计算每晚/每天单价（不依赖模型算术）
    data = _annotate_durations(text, data)

    return data


# ── 请假申请解析 ──────────────────────────────────────────────
class LeaveForm(BaseModel):
    type: Literal["annual", "personal", "sick", "compensatory", "marriage", "maternity"] = Field(
        description="假期类型：annual=年假, personal=事假, sick=病假, compensatory=调休, marriage=婚假, maternity=产假")
    startDate: str = Field(description="开始日期，格式 YYYY-MM-DD")
    endDate: str = Field(description="结束日期，格式 YYYY-MM-DD")
    days: float = Field(description="请假天数（自然日）")
    workdays: float = Field(description="工作日天数（排除周末）")
    reason: str = Field(description="请假原因，30字以内")
    emergencyContact: str | None = Field(default=None, description="紧急联系人（如果提到）")
    warnings: list[str] = Field(default_factory=list, description="需要注意的地方，如超过年假余额、需要双重审批等")


def _count_workdays(start_str: str, end_str: str) -> int:
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


async def parse_leave_form(text: str) -> dict:
    today = date.today().isoformat()

    result: LeaveForm = await _extract_structured(
        _model,
        LeaveForm,
        [
            {
                "role": "system",
                "content": f"""你是请假申请助手。从用户的自然语言描述中提取请假信息。
你必须以 JSON 格式（json）输出，字段直接放在 JSON 顶层，不要包裹在任何外层键中（不要输出类似 {{"请假单": ...}} 的结构）。
字段及类型：type, startDate, endDate, days, workdays, reason, emergencyContact, warnings。
今天是 {today}。
规则：
1. "下周一到周三"等相对日期要换算成具体日期
2. days 是自然日（含周末），workdays 是工作日（不含周末）
3. 请假超过3个工作日时，在 warnings 里提示需要主管和 HR 双重审批
4. 病假要在 warnings 里提示需要提供医院证明
5. 产假/婚假要在 warnings 里提示需要提供相关证明材料""",
            },
            {"role": "user", "content": text},
        ],
        hint="startDate",
        type_map=_LEAVE_TYPE_MAP,
        type_keywords=_LEAVE_TYPE_KEYWORDS,
    )

    data = result.model_dump()
    # 自动计算工作日（补充模型可能算错的情况）
    if data.get("startDate") and data.get("endDate"):
        try:
            data["workdays"] = _count_workdays(data["startDate"], data["endDate"])
        except ValueError:
            pass

    return data


# ── 报销金额合规检查 ──────────────────────────────────────────
# 公司报销标准（模拟数据，实际从数据库读取）
EXPENSE_RULES = {
    "travel": {"hotelPerNight": 800, "mealPerDay": 200, "flightEconomy": True, "maxSingleItem": 5000},
    "meal": {"maxSingleItem": 500},
    "office": {"maxSingleItem": 1000},
    "training": {"maxSingleItem": 10000},
    "other": {"maxSingleItem": 2000},
}


def check_compliance(expense_form: dict) -> list[str]:
    """确定性合规校验：返回不合规项列表。

    住宿/餐饮按 unitPrice（每晚/每天单价）判断，不依赖模型算术。
    """
    alerts = []
    rules = EXPENSE_RULES.get(expense_form.get("type"), EXPENSE_RULES["other"])
    item_total = 0.0

    for item in expense_form.get("items", []):
        name = item.get("name", "")
        amount = item.get("amount", 0)
        item_total += amount

        if amount > rules["maxSingleItem"]:
            alerts.append(f'"{name}" ¥{amount} 超过单笔限额 ¥{rules["maxSingleItem"]}，需要额外说明')

        if expense_form.get("type") == "travel" and "住宿" in name:
            # 有 unitPrice 按每晚单价；未标注晚数则整笔按一晚判断
            unit = item.get("unitPrice") or amount
            if unit > rules["hotelPerNight"]:
                alerts.append(f'住宿"{name}" 单价¥{unit}/晚 超过标准 ¥{rules["hotelPerNight"]}/晚')
        elif "餐饮" in name:
            unit = item.get("unitPrice")
            if unit and unit > rules.get("mealPerDay", 500):
                alerts.append(f'餐饮"{name}" 单价¥{unit}/天 超过标准 ¥{rules["mealPerDay"]}/天')

    # 明细合计与 totalAmount 核对
    declared = expense_form.get("totalAmount")
    if declared is not None and abs(item_total - declared) > 0.01:
        alerts.append(f"明细合计 ¥{item_total} 与申报总额 ¥{declared} 不一致，需要核对")

    return alerts


def build_compliance_report(expense_form: dict) -> str:
    """生成确定性合规报告，供审批 Agent 直接采信（避免模型自行算术误判）。"""
    rules = EXPENSE_RULES.get(expense_form.get("type"), EXPENSE_RULES["other"])
    lines = []
    for item in expense_form.get("items", []):
        name = item.get("name", "")
        amount = item.get("amount", 0)
        unit = item.get("unitPrice")
        if "住宿" in name:
            std = rules["hotelPerNight"]
            judged = unit or amount
            status = "合规" if judged <= std else "超标"
            detail = f"单价¥{judged}/晚（标准¥{std}/晚）"
        elif "餐饮" in name:
            std = rules.get("mealPerDay")
            if unit and std:
                judged = unit
                status = "合规" if judged <= std else "超标"
                detail = f"单价¥{judged}/天（标准¥{std}/天）"
            else:
                std = rules["maxSingleItem"]
                judged = amount
                status = "合规" if judged <= std else "超标"
                detail = f"金额¥{judged}（单笔上限¥{std}）"
        else:
            std = rules["maxSingleItem"]
            judged = amount
            status = "合规" if judged <= std else "超标"
            detail = f"金额¥{judged}（单笔上限¥{std}）"
        lines.append(f"- {name} ¥{amount}：{status}（{detail}）")
    return "\n".join(lines)
