import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import queries
from .chat_agent import handle_chat_message
from .deps import get_db
from .schemas import (
    AssignmentOut,
    ChatRequestIn,
    ChatResponseOut,
    CourseOut,
    DashboardSummaryOut,
    DocumentOut,
    ResourceOut,
    SyncStatusOut,
)

router = APIRouter(prefix="/api")


@router.get("/courses", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    return [queries.course_out(c) for c in queries.fetch_courses(db)]


@router.get("/courses/{course_id}", response_model=CourseOut)
def get_course(course_id: int, db: Session = Depends(get_db)):
    course = queries.fetch_course(db, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return queries.course_out(course)


@router.get("/courses/{course_id}/assignments", response_model=list[AssignmentOut])
def list_course_assignments(course_id: int, db: Session = Depends(get_db)):
    course = queries.fetch_course(db, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return [queries.assignment_out(a, course) for a in queries.fetch_course_assignments(db, course_id)]


@router.get("/courses/{course_id}/resources", response_model=list[ResourceOut])
def list_course_resources(course_id: int, db: Session = Depends(get_db)):
    course = queries.fetch_course(db, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return [queries.resource_out(r, course) for r in queries.fetch_course_resources(db, course_id)]


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(db: Session = Depends(get_db)):
    return [queries.assignment_out(a, c) for a, c in queries.fetch_all_assignments(db)]


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return [
        queries.document_out(doc, course, latest_at)
        for doc, _resource, course, latest_at in queries.fetch_all_documents(db)
    ]


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, db: Session = Depends(get_db)):
    document = queries.fetch_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not os.path.isfile(document.file_path):
        raise HTTPException(status_code=404, detail="File is no longer available on disk")
    return FileResponse(document.file_path, filename=document.name)


@router.get("/sync-status", response_model=SyncStatusOut)
def get_sync_status(db: Session = Depends(get_db)):
    return queries.fetch_sync_status(db)


@router.get("/dashboard/summary", response_model=DashboardSummaryOut)
def get_dashboard_summary(db: Session = Depends(get_db)):
    courses_count, assignments_count, documents_count = queries.fetch_dashboard_counts(db)
    return DashboardSummaryOut(
        coursesCount=courses_count,
        assignmentsCount=assignments_count,
        documentsCount=documents_count,
        sync=queries.fetch_sync_status(db),
    )


@router.post("/chat", response_model=ChatResponseOut)
def chat(payload: ChatRequestIn, db: Session = Depends(get_db)):
    return handle_chat_message(db, payload.message)
