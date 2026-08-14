# server-py/app/routes/erp.py
# ERP 路由：智能填单（自然语言→结构化）+ 审批流（Multi-Agent）
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.middleware import rate_limiter
from app.services.erp.approval import APPROVAL_ROLES, run_approval_flow
from app.services.erp.parser import check_compliance, parse_expense_form, parse_leave_form
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()

# 申请记录（生产换数据库）
_applications: dict[str, dict] = {}


@router.post("/parse", dependencies=[Depends(rate_limiter)])
async def parse(body: dict):
    text = (body.get("text") or "").strip()
    form_type = body.get("formType")

    if not text:
        raise HTTPException(status_code=400, detail={"error": {"message": "描述不能为空"}})
    if form_type not in ("expense", "leave"):
        raise HTTPException(status_code=400, detail={"error": {"message": "formType 必须是 expense 或 leave"}})

    try:
        if form_type == "expense":
            form = await parse_expense_form(text)
            compliance_alerts = check_compliance(form)
            form["warnings"] = [*(form.get("warnings") or []), *compliance_alerts]
        else:
            form = await parse_leave_form(text)

        return {"success": True, "form": form, "formType": form_type}
    except Exception as err:
        logger.error("erp: parse error", {"error": str(err)})
        raise HTTPException(status_code=500, detail={"error": {"message": "解析失败，请检查输入内容"}})


@router.post("/submit/stream", dependencies=[Depends(rate_limiter)])
async def submit_stream(body: dict):
    form_data = body.get("formData")
    form_type = body.get("formType")
    applicant_name = body.get("applicantName")

    if not form_data or not form_type:
        raise HTTPException(status_code=400, detail={"error": {"message": "缺少表单数据"}})

    app_id = f"APP{int(time.time() * 1000)}"
    application = {
        "id": app_id,
        "formType": form_type,
        "formData": {**form_data, "applicantName": applicant_name or "申请人"},
        "status": "pending",
        "messages": [],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    _applications[app_id] = application

    async def generator():
        import asyncio

        yield "start", {"appId": app_id, "formType": form_type}

        queue: asyncio.Queue = asyncio.Queue()

        async def on_event(event_type, data):
            if event_type == "message":
                application["messages"].append(data)
            await queue.put((event_type, data))

        run_task = asyncio.create_task(run_approval_flow(application["formData"], form_type, on_event))

        while True:
            event_type, data = await queue.get()
            yield event_type, data
            if event_type == "final":
                break

        result = await run_task
        application["status"] = result["status"]
        application["result"] = result
        application["updatedAt"] = datetime.now(timezone.utc).isoformat()

        yield "done", {"appId": app_id}

    return sse_stream(generator)


@router.get("/applications")
async def list_applications():
    items = sorted(_applications.values(), key=lambda a: a["createdAt"], reverse=True)
    return {"applications": [
        {
            "id": a["id"],
            "formType": a["formType"],
            "status": a["status"],
            "amount": a["formData"].get("totalAmount"),
            "reason": a["formData"].get("reason"),
            "days": a["formData"].get("workdays"),
            "createdAt": a["createdAt"],
        }
        for a in items
    ]}


@router.get("/applications/{app_id}")
async def get_application(app_id: str):
    app = _applications.get(app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"error": {"message": "申请不存在"}})
    return app


@router.get("/roles")
async def roles():
    return {"roles": list(APPROVAL_ROLES.values())}
