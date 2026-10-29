"""知识 ACL 继承到原始工件，避免检索被拒后绕过下载入口。"""

from sqlalchemy import select

from ..models import Member, ResourceRecord, UploadRecord


def collection_visible(db, user, collection) -> bool:
    if not collection or collection.state == "DELETED":
        return False
    membership = db.scalar(
        select(Member).where(Member.project_id == collection.project_id, Member.user_id == user.id)
    )
    allowed = collection.payload.get("acl_member_ids", [])
    return bool(membership and (user.id in allowed or membership.id in allowed))


def document_visible(db, user, document, upload=None) -> bool:
    if document.state == "DELETED" or not document.project_id:
        return False
    membership = db.scalar(
        select(Member).where(Member.project_id == document.project_id, Member.user_id == user.id)
    )
    upload = upload or db.get(UploadRecord, document.payload["upload_id"])
    if (
        not membership
        or not upload
        or upload.id != document.payload["upload_id"]
        or upload.project_id != document.project_id
        or not upload.owner_id
    ):
        return False
    # 任务附件没有知识集合；先校验项目与上传归属，再仅向上传者开放。
    if document.payload.get("scope") == "task_only":
        return upload.owner_id == user.id
    collection_id = document.payload.get("collection_id")
    if not collection_id:
        return False
    collection = db.get(ResourceRecord, collection_id)
    if (
        not collection
        or collection.kind != "collections"
        or collection.project_id != document.project_id
        or not collection_visible(db, user, collection)
    ):
        return False
    if document.state == "PUBLISHED":
        return True
    # 未发布或已撤销的原文只保留给上传者处理，不再作为项目共享资料。
    return upload.owner_id == user.id


def resource_visible(db, user, record) -> bool:
    if record.kind == "collections":
        return collection_visible(db, user, record)
    if record.kind == "documents":
        return document_visible(db, user, record)
    if record.kind != "artifacts":
        return True
    uploads = db.scalars(
        select(UploadRecord).where(
            UploadRecord.artifact_id == record.id, UploadRecord.project_id == record.project_id
        )
    ).all()
    if not uploads:
        return True
    # Worker 输出是项目工件，不继承上传进程的“私有文档上传者”身份。
    if any(upload.job_id and record.internal.get("task_id") == upload.job_id
           and record.internal.get("fencing_token") == upload.fencing_token for upload in uploads):
        return True
    documents = db.scalars(
        select(ResourceRecord).where(
            ResourceRecord.kind == "documents", ResourceRecord.project_id == record.project_id
        )
    ).all()
    associated = [(u, d) for u in uploads for d in documents if d.payload["upload_id"] == u.id]
    if associated:
        return any(document_visible(db, user, document, upload) for upload, document in associated)
    return any(upload.owner_id == user.id for upload in uploads)


def visible_ids(db, user, kind, project_id) -> list[str]:
    records = db.scalars(
        select(ResourceRecord).where(
            ResourceRecord.kind == kind, ResourceRecord.project_id == project_id
        )
    ).all()
    return [record.id for record in records if resource_visible(db, user, record)]
