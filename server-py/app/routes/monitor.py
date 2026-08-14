# server-py/app/routes/monitor.py
# 用量看板路由：API 调用统计、Token 消耗、缓存命中率、成本
import math
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.services.cache import cache

router = APIRouter()

_start_time = time.time()
_stats = {"calls": [], "dailyBudget": 50}  # ¥50 日预算


def record_api_call(feature="chat", input_tokens=0, output_tokens=0, latency_ms=0, from_cache=False):
    cost_usd = (input_tokens / 1e6 * 0.27) + (output_tokens / 1e6 * 1.10)
    _stats["calls"].append({
        "time": datetime.now(timezone.utc).isoformat(),
        "feature": feature,
        "inputT": input_tokens,
        "outputT": output_tokens,
        "costUSD": cost_usd,
        "costCNY": cost_usd * 7.2,
        "latencyMs": latency_ms,
        "fromCache": from_cache,
    })
    if len(_stats["calls"]) > 500:
        _stats["calls"].pop(0)


def _percentile(arr, p):
    if not arr:
        return 0
    s = sorted(arr)
    idx = math.ceil(len(s) * p / 100) - 1
    return s[max(0, min(idx, len(s) - 1))]


def _get_last_7_days_stats(calls):
    days = []
    now = datetime.now()
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = d + timedelta(days=1)

        day_calls = [c for c in calls if d <= datetime.fromisoformat(c["time"]).replace(tzinfo=None) < next_day]

        days.append({
            "date": d.strftime("%Y-%m-%d"),
            "label": f"{d.month}/{d.day}",
            "totalCalls": len(day_calls),
            "apiCalls": len([c for c in day_calls if not c["fromCache"]]),
            "inputT": sum(c["inputT"] for c in day_calls),
            "outputT": sum(c["outputT"] for c in day_calls),
            "costCNY": round(sum(c["costCNY"] for c in day_calls if not c["fromCache"]), 4),
        })
    return days


_FEATURE_NAMES = {"chat": "对话助手", "knowledge": "RAG 知识库", "agent": "任务 Agent", "workflow": "内容工作流", "erp": "ERP 审批", "prompt": "Prompt 调试"}


def _get_by_feature(calls):
    features: dict[str, dict] = {}
    for c in calls:
        f = features.setdefault(c["feature"], {"calls": 0, "costCNY": 0, "tokens": 0})
        f["calls"] += 1
        f["costCNY"] += 0 if c["fromCache"] else c["costCNY"]
        f["tokens"] += c["inputT"] + c["outputT"]

    result = [
        {"feature": k, "label": _FEATURE_NAMES.get(k, k), "calls": v["calls"], "costCNY": round(v["costCNY"], 4), "tokens": v["tokens"]}
        for k, v in features.items()
    ]
    result.sort(key=lambda x: x["calls"], reverse=True)
    return result


@router.get("/stats")
async def stats():
    now = time.time()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    today_calls = [c for c in _stats["calls"] if datetime.fromisoformat(c["time"]).replace(tzinfo=None) >= today_start]

    last_7_days = _get_last_7_days_stats(_stats["calls"])
    by_feature = _get_by_feature(today_calls)
    recent_calls = list(reversed(_stats["calls"]))[:50]

    latencies = [c["latencyMs"] for c in today_calls if not c["fromCache"] and c["latencyMs"] > 0]

    today_total_cny = sum(c["costCNY"] for c in today_calls if not c["fromCache"])
    cache_hits = len([c for c in today_calls if c["fromCache"]])
    total_calls = len(today_calls)

    return {
        "overview": {
            "totalCallsToday": total_calls,
            "apiCallsToday": total_calls - cache_hits,
            "cacheHitsToday": cache_hits,
            "cacheHitRate": f"{cache_hits / total_calls * 100:.1f}%" if total_calls else "0%",
            "tokenInputToday": sum(c["inputT"] for c in today_calls),
            "tokenOutputToday": sum(c["outputT"] for c in today_calls),
            "costCNYToday": round(today_total_cny, 4),
            "dailyBudget": _stats["dailyBudget"],
            "budgetUsedPct": min(100, round(today_total_cny / _stats["dailyBudget"] * 100, 1)),
            "uptimeSeconds": int(now - _start_time),
        },
        "latency": {
            "p50": _percentile(latencies, 50),
            "p90": _percentile(latencies, 90),
            "p99": _percentile(latencies, 99),
            "avg": round(sum(latencies) / len(latencies)) if latencies else 0,
        },
        "byFeature": by_feature,
        "last7Days": last_7_days,
        "recentCalls": [
            {
                "time": c["time"], "feature": c["feature"], "inputT": c["inputT"], "outputT": c["outputT"],
                "costCNY": round(c["costCNY"], 5), "latencyMs": c["latencyMs"], "fromCache": c["fromCache"],
            }
            for c in recent_calls
        ],
        "cacheStats": cache.get_stats(),
    }


@router.put("/budget")
async def set_budget(body: dict):
    daily_budget = body.get("dailyBudget")
    if not isinstance(daily_budget, (int, float)) or daily_budget <= 0:
        raise HTTPException(status_code=400, detail={"error": {"message": "预算必须是正数"}})
    _stats["dailyBudget"] = daily_budget
    return {"success": True, "dailyBudget": daily_budget}
