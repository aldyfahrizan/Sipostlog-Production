"""SOP Penggunaan: content for the in-app page and the downloadable PDF module."""
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from auth import ROLE_ADMIN, ROLE_OPNAME, ROLE_PETUGAS, require_role
from sop_content import ROLE_ACCESS, SECTIONS, SOP_META, sections_for

router = APIRouter(prefix="/api/sop")
STAFF = (ROLE_ADMIN, ROLE_PETUGAS, ROLE_OPNAME)


@router.get("")
async def sop(user=Depends(require_role(*STAFF))):
    return {
        "meta": SOP_META,
        "role": user["role"],
        "access": [{"label": label, "admin": a, "petugas": p, "opname": o} for label, a, p, o in ROLE_ACCESS],
        "sections": sections_for(user["role"]),
        "total_sections": len(SECTIONS),
    }


@router.get("/pdf")
async def sop_pdf(user=Depends(require_role(*STAFF))):
    from starlette.concurrency import run_in_threadpool
    from sop_pdf import build_sop_pdf
    content = await run_in_threadpool(build_sop_pdf, user["name"] or user["email"])
    name = f"Modul_SOP_SIPOSTLOG_v{SOP_META['version']}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}", "Cache-Control": "no-store"})


@router.get("/docx")
async def sop_docx(user=Depends(require_role(*STAFF))):
    from starlette.concurrency import run_in_threadpool
    from sop_docx import build_sop_docx
    content = await run_in_threadpool(build_sop_docx, user["name"] or user["email"])
    name = f"Modul_SOP_SIPOSTLOG_v{SOP_META['version']}_{datetime.now().strftime('%Y%m%d')}.docx"
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}", "Cache-Control": "no-store"})
