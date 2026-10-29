"""Worker 仅领取许可能力；有效租约、attempt 与 fence 三者同时匹配才可提交。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import select

from packages.contracts.validation import validate_payload

from ..errors import ApiError
from ..models import TaskRecord, utcnow
from ..security import aware, project_role
from ..services.common import (
    event,
    get_resource,
    history,
    iso,
    lock_event_projects,
    mutate,
    read,
    sha,
)
from ..services.service_identity import authorize_claim, authorize_task, worker_auth

router = APIRouter(prefix="/api/v1")


def deadline_elapsed(task) -> bool:
    return (
        aware(datetime.fromisoformat(task.envelope["deadline"].replace("Z", "+00:00"))) <= utcnow()
    )


def current_lease(db, job_id, body):
    candidate = db.get(TaskRecord, job_id)
    if candidate is not None and candidate.capability == "acceptance.verify":
        # 验收的入库、失败结束、心跳和工件通道统一采用 run -> task 锁序。
        get_resource(db, "runs", candidate.run_id, candidate.project_id, lock=True)
    task = db.scalar(
        select(TaskRecord)
        .where(TaskRecord.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not task
        or task.state not in ("LEASED", "RUNNING", "CANCEL_REQUESTED")
        or not task.lease_expires_at
        or aware(task.lease_expires_at) <= utcnow()
        or task.worker_id != body["worker_id"]
        or task.attempt != body["attempt"]
        or task.fencing_token != body["fencing_token"]
    ):
        raise ApiError(409, "STALE_LEASE", "租约已过期或 Worker/attempt/fencing token 不匹配")
    return task


def task_view(task):
    return {
        "task_id": task.id,
        "run_id": task.run_id,
        "version": max(1, task.fencing_token + len(task.attempts)),
        "state": task.state,
        "envelope": task.envelope,
        "questions": task.questions,
        "attempts": task.attempts,
        "waiting_reason": "等待用户回答"
        if task.state == "WAITING_INPUT"
        else "租约丢失，等待进程与副作用对账"
        if task.state == "LOST"
        else None,
        "result": task.result,
    }


@router.post("/worker/jobs/claim")
async def claim(request: Request):
    def operation(db, principal, body):
        validate_payload("JobClaim", body)
        authorize_claim(principal, body)
        now = utcnow()
        scope = [TaskRecord.capability.in_(principal.capabilities)]
        if "*" not in principal.project_ids:
            scope.append(TaskRecord.project_id.in_(principal.project_ids))
        expired = db.scalars(
            select(TaskRecord)
            .where(
                TaskRecord.state.in_(("LEASED", "RUNNING"))
                | (
                    (TaskRecord.state == "CANCEL_REQUESTED")
                    & (TaskRecord.capability == "contract.validate")
                ),
                TaskRecord.lease_expires_at < now,
                *scope,
            )
            .order_by(TaskRecord.project_id, TaskRecord.id)
            .with_for_update(skip_locked=True)
        ).all()
        # 保留验证能力不进入生成 Worker 队列，客户端自报 capability 不能取得验收权限。
        query = select(TaskRecord).where(
            TaskRecord.state == "QUEUED",
            *scope,
            TaskRecord.capability.in_(body["capabilities"]),
        )
        candidates = db.scalars(
            query.order_by(
                TaskRecord.project_id, TaskRecord.created_at, TaskRecord.id
            ).with_for_update(skip_locked=True)
        ).all()
        # 先完成行锁选择，再按统一顺序取得整个事务的项目事件锁。
        # 否则“过期项目 B → 新领取项目 A”可能与另一 Worker 反序互等。
        lock_event_projects(db, {task.project_id for task in [*expired, *candidates]})
        cancelled_recovery = set()
        for task in expired:
            was_cancelled = task.state == "CANCEL_REQUESTED"
            task.state = "LOST"
            attempts = list(task.attempts)
            attempts[-1] = {**attempts[-1], "state": "LOST", "finished_at": iso(now)}
            task.attempts = attempts
            event(
                db,
                principal.id,
                "task.lease_lost",
                project_id=task.project_id,
                payload={"task_id": task.id},
            )
            # 仅静态 JSON 检查可自动对账重领；它不执行外部程序，所有输出由 fence 隔离。
            if task.capability == "contract.validate" and task.attempt < 10:
                task.state = "QUEUED"
                if was_cancelled:
                    cancelled_recovery.add(task.id)
                task.worker_id = task.lease_expires_at = None
                if "contract.validate" in body["capabilities"]:
                    candidates.append(task)

        task = None
        for candidate in candidates:
            run = get_resource(db, "runs", candidate.run_id, candidate.project_id)
            if run.internal["snapshot"]["toolchain_sha256"] == body["tool_profile_sha256"]:
                task = candidate
                break
        if task is None:
            return {"job_id": None, "task": None, "lease_until": None}, 200, {}
        if task.attempt >= 10:
            raise ApiError(409, "ATTEMPT_LIMIT", "任务领取次数达到限制")
        task.attempt += 1
        task.fencing_token += 1
        task.worker_id, task.lease_expires_at = body["worker_id"], now + timedelta(seconds=60)
        task.state = "CANCEL_REQUESTED" if task.id in cancelled_recovery else "LEASED"
        task.envelope = {
            **task.envelope,
            "attempt": task.attempt,
            "fencing_token": task.fencing_token,
        }
        task.attempts = [
            *task.attempts,
            {
                "attempt": task.attempt,
                "fencing_token": task.fencing_token,
                "worker_id": task.worker_id,
                "state": "LEASED",
                "started_at": iso(now),
                "finished_at": None,
                "cancel_confirmed": False,
                "result_artifact_id": None,
            },
        ]
        event(
            db,
            principal.id,
            "task.leased",
            project_id=task.project_id,
            payload={"task_id": task.id},
        )
        return (
            {"job_id": task.id, "task": task.envelope, "lease_until": iso(task.lease_expires_at)},
            200,
            {},
        )

    def preauthorize(db, principal, body):
        validate_payload("JobClaim", body)
        authorize_claim(principal, body)

    return await mutate(request, operation, authenticator=worker_auth, preauthorize=preauthorize)


@router.post("/worker/jobs/{job_id}/heartbeat")
async def heartbeat(request: Request, job_id: str):
    def operation(db, principal, body):
        validate_payload("JobHeartbeat", body)
        task = current_lease(db, job_id, body)
        authorize_task(principal, task)
        if deadline_elapsed(task) and task.state != "CANCEL_REQUESTED":
            raise ApiError(409, "TASK_DEADLINE_EXCEEDED", "任务已超出绝对截止时间")
        task.lease_expires_at = utcnow() + timedelta(seconds=60)
        if task.state == "LEASED":
            task.state = "RUNNING"
        event(
            db,
            principal.id,
            "task.heartbeat",
            project_id=task.project_id,
            payload={"task_id": task.id, "message": body["progress_message"][:500]},
        )
        return (
            {"job_id": task.id, "task": task.envelope, "lease_until": iso(task.lease_expires_at)},
            200,
            {},
        )

    return await mutate(request, operation, authenticator=worker_auth)


@router.post("/worker/jobs/{job_id}/complete")
async def complete(request: Request, job_id: str):
    def operation(db, principal, body):
        validate_payload("JobComplete", body)
        task = current_lease(db, job_id, body)
        authorize_task(principal, task)
        result = body["result"]
        if task.capability == "acceptance.verify" and result["status"] not in (
            "cancelled",
            "timeout",
            "failed",
        ):
            raise ApiError(403, "SIGNED_EVIDENCE_REQUIRED", "验收结果必须经独立签名证据入口提交")
        if result["task_id"] != task.id or result["revision_id"] != task.revision_id:
            raise ApiError(409, "STALE_LEASE", "结果不属于当前任务和版本")
        if task.capability == "contract.validate" and "call_id" not in result:
            raise ApiError(422, "RESULT_KIND_MISMATCH", "工具任务不能提交 Agent 自评结果")
        refs = list(result.get("artifact_refs", result.get("artifacts", [])))
        if result.get("log_ref"):
            refs.append(result["log_ref"])
        for ref in refs:
            artifact = get_resource(db, "artifacts", ref["id"], task.project_id)
            if (
                artifact.payload != ref
                or artifact.internal.get("task_id") != task.id
                or artifact.internal.get("fencing_token") != task.fencing_token
                or artifact.internal.get("attempt") != task.attempt
            ):
                raise ApiError(422, "ARTIFACT_SCOPE_MISMATCH", "结果只能引用本任务受控上传工件")
        status = result["status"]
        if task.capability == "contract.validate":
            # 退出码与声明必须相符；输入摘要绑定冻结工件清单，不能接收其他版本的成绩。
            if status == "passed" and result.get("exit_code") != 0:
                raise ApiError(422, "RESULT_INCONSISTENT", "成功结果必须具有零退出码")
            if result.get("input_sha256") != sha(task.envelope["input_artifacts"]):
                raise ApiError(422, "INPUT_HASH_MISMATCH", "结果输入摘要与冻结工件清单不符")
        if task.state == "CANCEL_REQUESTED" and status != "cancelled":
            raise ApiError(
                409, "CANCELLATION_PENDING", "取消后的结果不能提升工件，须先确认进程已停止"
            )
        if deadline_elapsed(task) and status not in ("timeout", "cancelled"):
            raise ApiError(409, "TASK_DEADLINE_EXCEEDED", "截止时间后仅接受超时或取消诊断")
        task.result = result
        task.state = (
            "CANCELLED"
            if status == "cancelled"
            else "SUCCEEDED"
            if status in ("passed", "completed")
            else "WAITING_INPUT"
            if status == "needs_input"
            else "FAILED"
        )
        if task.state == "WAITING_INPUT":
            from ..models import uid

            task.questions = [
                {
                    **q,
                    "id": uid(),
                    "task_id": task.id,
                    "answer": None,
                    "answered_by": None,
                    "answered_at": None,
                }
                for q in result["questions"]
            ]
        attempts = list(task.attempts)
        attempts[-1] = {
            **attempts[-1],
            "state": task.state,
            "finished_at": iso(utcnow()),
            "cancel_confirmed": task.state == "CANCELLED",
        }
        task.attempts, task.lease_expires_at = attempts, None
        run = get_resource(db, "runs", task.run_id, task.project_id, lock=True)
        history(db, run)
        # 静态检查成功只完成这个任务；缺少 RTL/CAD/模型闭环时运行保持 BLOCKED。
        run.state = (
            "CANCELLED"
            if task.state == "CANCELLED"
            else "BLOCKED"
            if task.state == "SUCCEEDED"
            else task.state
        )
        emitted = event(
            db,
            principal.id,
            "task.completed",
            project_id=task.project_id,
            payload={"task_id": task.id, "state": task.state},
        )
        return (
            {
                "resource_id": task.id,
                "state": task.state,
                "run_id": task.run_id,
                "approval_id": None,
                "event_cursor": emitted.id,
            },
            200,
            {},
        )

    return await mutate(request, operation, authenticator=worker_auth)


@router.get("/worker/jobs/{job_id}/cancel-state")
def cancel_state(request: Request, job_id: str):
    with request.app.state.db.transaction() as db:
        worker_auth(db, request)
        task = db.get(TaskRecord, job_id)
        if not task:
            raise ApiError(404, "NOT_FOUND", "任务不存在")
        return {"cancel_requested": task.state == "CANCEL_REQUESTED", "state": task.state}


@router.get("/projects/{project_id}/runs/{run_id}/snapshot")
async def snapshot(request: Request, project_id: str, run_id: str):
    def operation(db, user):
        project_role(db, user, project_id, ("developer", "reviewer", "admin"))
        return get_resource(db, "runs", run_id, project_id).internal["snapshot"]

    return await read(request, operation)


@router.get("/projects/{project_id}/runs/{run_id}/tasks")
async def tasks(request: Request, project_id: str, run_id: str):
    def operation(db, user):
        project_role(db, user, project_id, ("developer", "reviewer", "admin"))
        get_resource(db, "runs", run_id, project_id)
        found = db.scalars(
            select(TaskRecord)
            .where(TaskRecord.run_id == run_id, TaskRecord.project_id == project_id)
            .order_by(TaskRecord.created_at)
        ).all()
        return {"items": [task_view(t) for t in found], "next_cursor": None}

    return await read(request, operation)


@router.get("/projects/{project_id}/runs/{run_id}/tasks/{task_id}")
async def task_detail(request: Request, project_id: str, run_id: str, task_id: str):
    def operation(db, user):
        project_role(db, user, project_id, ("developer", "reviewer", "admin"))
        task = db.get(TaskRecord, task_id)
        if not task or task.project_id != project_id or task.run_id != run_id:
            raise ApiError(404, "NOT_FOUND", "任务不存在")
        return task_view(task)

    return await read(request, operation)


@router.post("/projects/{project_id}/runs/{run_id}/tasks/{task_id}/answers")
async def answer_task(request: Request, project_id: str, run_id: str, task_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, ("developer", "reviewer", "admin"))
        validate_payload("Answer", body)
        task = db.scalar(select(TaskRecord).where(TaskRecord.id == task_id).with_for_update())
        if not task or task.project_id != project_id or task.run_id != run_id:
            raise ApiError(404, "NOT_FOUND", "任务不存在")
        if task.state != "WAITING_INPUT" or task.revision_id != body["revision_id"]:
            raise ApiError(409, "INVALID_STATE", "任务不在当前版本的等待回答状态")
        questions = [dict(q) for q in task.questions]
        question = next((q for q in questions if q["id"] == body["question_id"]), None)
        if not question or question["answer"] is not None:
            raise ApiError(409, "QUESTION_NOT_OPEN", "问题不存在或已经回答")
        question.update(answer=body["value"], answered_by=user.id, answered_at=iso(utcnow()))
        task.questions = questions
        if all(not q["blocking"] or q["answer"] is not None for q in questions):
            task.state = "QUEUED"
            task.worker_id = task.lease_expires_at = None
        event(db, user.id, "task.answered", project_id=project_id, payload={"task_id": task_id})
        return task_view(task), 200, {}

    return await mutate(request, operation)
