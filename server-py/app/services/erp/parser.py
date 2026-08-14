# server-py/app/services/erp/parser.py
# 自然语言 → 结构化表单：用结构化输出解析用户的口语化描述
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.services.model import create_chat_model

_model = create_chat_model(temperature=0)


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

    extract_model = _model.with_structured_output(ExpenseForm)
    result: ExpenseForm = await extract_model.ainvoke([
        {
            "role": "system",
            "content": f"""你是报销单填写助手。从用户的自然语言描述中提取报销信息，生成结构化表单。
今天是 {today}。
规则：
1. 如果用户说"上周"，根据今天日期推算具体日期
2. 金额务必精确，提到"约""大概"时保留原数字
3. 如果描述中有金额超过单笔3000元的项目，在 warnings 里提示
4. 如果报销事由不明确，在 warnings 里提示需要补充
5. totalAmount 等于所有 items 的 amount 之和""",
        },
        {"role": "user", "content": text},
    ])

    return result.model_dump()


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

    extract_model = _model.with_structured_output(LeaveForm)
    result: LeaveForm = await extract_model.ainvoke([
        {
            "role": "system",
            "content": f"""你是请假申请助手。从用户的自然语言描述中提取请假信息。
今天是 {today}。
规则：
1. "下周一到周三"等相对日期要换算成具体日期
2. days 是自然日（含周末），workdays 是工作日（不含周末）
3. 请假超过3个工作日时，在 warnings 里提示需要主管和 HR 双重审批
4. 病假要在 warnings 里提示需要提供医院证明
5. 产假/婚假要在 warnings 里提示需要提供相关证明材料""",
        },
        {"role": "user", "content": text},
    ])

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
    alerts = []
    rules = EXPENSE_RULES.get(expense_form.get("type"), EXPENSE_RULES["other"])

    for item in expense_form.get("items", []):
        if item["amount"] > rules["maxSingleItem"]:
            alerts.append(f'"{item["name"]}" ¥{item["amount"]} 超过单笔限额 ¥{rules["maxSingleItem"]}，需要额外说明')

        if expense_form.get("type") == "travel":
            if "住宿" in item["name"] and item["amount"] > rules["hotelPerNight"] * 3:
                alerts.append(f"住宿费用偏高，每晚标准为 ¥{rules['hotelPerNight']}")

    return alerts
