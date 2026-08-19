# server-py/app/routes/erp.py
# ERP 路由：智能填单（自然语言→结构化）+ 审批流（Multi-Agent）
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.database import SessionLocal
from app.middleware import rate_limiter
from app.models import ErpApplication, User
from app.services.auth import get_current_user
from app.services.erp.approval import APPROVAL_ROLES, run_approval_flow
from app.services.erp.parser import check_compliance, parse_expense_form, parse_leave_form
from app.utils.logger import logger
from app.utils.sse import sse_stream

router = APIRouter()


# ── 申请记录：MySQL 持久化（服务重启后仍可恢复历史）────────────
def _to_dict(app: ErpApplication) -> dict:
    return {
        "id": app.id,
        "formType": app.form_type,
        "formData": json.loads(app.form_data),
        "status": app.status,
        "approvers": json.loads(app.approvers) if app.approvers else [],
        "messages": json.loads(app.messages) if app.messages else [],
        "result": json.loads(app.result) if app.result else None,
        "createdAt": app.created_at.isoformat() if app.created_at else None,
        "updatedAt": app.updated_at.isoformat() if app.updated_at else None,
    }


def _save_application(data: dict) -> None:
    """按申请 ID 覆盖保存一条申请记录到 MySQL（含归属用户）"""
    db = SessionLocal()
    try:
        app = db.get(ErpApplication, data["id"])
        if app is None:
            app = ErpApplication(
                id=data["id"],
                user_id=data["userId"],
                form_type=data["formType"],
                form_data=json.dumps(data["formData"], ensure_ascii=False),
                status=data["status"],
                approvers=json.dumps(data.get("approvers", []), ensure_ascii=False),
                messages=json.dumps(data.get("messages", []), ensure_ascii=False),
                result=json.dumps(data["result"], ensure_ascii=False) if data.get("result") else "",
            )
            db.add(app)
        else:
            app.form_type = data["formType"]
            app.form_data = json.dumps(data["formData"], ensure_ascii=False)
            app.status = data["status"]
            app.approvers = json.dumps(data.get("approvers", []), ensure_ascii=False)
            app.messages = json.dumps(data.get("messages", []), ensure_ascii=False)
            app.result = json.dumps(data["result"], ensure_ascii=False) if data.get("result") else ""
        db.commit()
    finally:
        db.close()


def _list_from_db(user_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ErpApplication)
            .filter(ErpApplication.user_id == user_id)
            .order_by(ErpApplication.created_at.desc())
            .all()
        )
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


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
async def submit_stream(body: dict, user: User = Depends(get_current_user)):
    form_data = body.get("formData")
    form_type = body.get("formType")
    applicant_name = body.get("applicantName")

    if not form_data or not form_type:
        raise HTTPException(status_code=400, detail={"error": {"message": "缺少表单数据"}})

    app_id = f"APP{int(time.time() * 1000)}"
    application = {
        "id": app_id,
        "userId": user.id,
        "formType": form_type,
        "formData": {**form_data, "applicantName": applicant_name or "申请人"},
        "status": "pending",
        "approvers": [],
        "messages": [],
        "result": None,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    _save_application(application)  # 提交即落库，重启后仍可恢复

    async def generator():
        import asyncio

        yield "start", {"appId": app_id, "formType": form_type}

        queue: asyncio.Queue = asyncio.Queue()

        async def on_event(event_type, data):
            if event_type == "plan":
                application["approvers"] = data.get("approvers", [])
            if event_type == "message":
                application["messages"].append(data)
            # 流程/对话变化时同步落库，保证中途重启也能恢复
            if event_type in ("plan", "message"):
                _save_application(application)
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
        _save_application(application)  # 保存最终结果

        yield "done", {"appId": app_id}

    return sse_stream(generator)


@router.get("/applications")
async def list_applications(user: User = Depends(get_current_user)):
    # 仅返回当前登录用户的申请记录，避免跨账号共享
    items = _list_from_db(user.id)
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
async def get_application(app_id: str, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.get(ErpApplication, app_id)
        if not row:
            raise HTTPException(status_code=404, detail={"error": {"message": "申请不存在"}})
        if row.user_id != user.id:
            raise HTTPException(status_code=403, detail={"error": {"message": "无权查看该申请"}})
        return _to_dict(row)
    finally:
        db.close()


@router.get("/roles")
async def roles():
    return {"roles": list(APPROVAL_ROLES.values())}
