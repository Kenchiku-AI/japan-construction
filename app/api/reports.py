from datetime import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.session import get_db
from app.db.models import DailyReport, Project, User
from app.schemas.report import DailyReportCreate, DailyReportUpdate, DailyReportRead
from app.core.dependencies import (
    get_current_user,
    get_current_user_ws,
    require_company_manager,
    require_company_member,
)
from app.services.openai import transcribe_audio, get_json_from_speech

router = APIRouter(
  prefix="/reports",
  tags=["reports"]
)

@router.post("/daily", response_model=DailyReportRead, status_code=status.HTTP_201_CREATED)
async def create_daily_report(
  payload: DailyReportCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, payload.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_manager(current_user, project.company_id)

  result = await db.execute(
    select(DailyReport).where(
      DailyReport.project_id == payload.project_id,
      DailyReport.date == payload.date,
    )
  )
  existing_report = result.scalar_one_or_none()

  if existing_report:
    raise HTTPException(
      status_code=409,
      detail="A daily report already exists for this project on this date",
    )

  daily_report = DailyReport(**payload.model_dump())

  db.add(daily_report)
  await db.commit()
  await db.refresh(daily_report)

  return daily_report

@router.put("/daily/{report_id}", response_model=DailyReportRead)
async def update_daily_report(
  report_id: UUID,
  payload: DailyReportUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  report = await db.get(DailyReport, report_id)
  if not report:
    raise HTTPException(status_code=404, detail="Report not found")

  if current_user.role != "admin":
    require_company_member(current_user, report.project.company_id)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(report, field, value)

  await db.commit()
  await db.refresh(report)

  return report

@router.delete("/daily/{report_id}", status_code=204)
async def delete_daily_report(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  report = await db.get(DailyReport, report_id)
  if not report:
    raise HTTPException(status_code=404, detail="Daily report not found")

  require_company_manager(current_user, report.project.company_id)

  await db.delete(report)
  await db.commit()
  return None

@router.websocket("/daily/{report_id}/audio")
async def daily_report_audio(
  ws: WebSocket,
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
):
  await ws.accept()

  current_user = await get_current_user_ws(ws)

  report = await db.get(DailyReport, report_id)
  if not report:
    await ws.close(code=1008)
    return

  require_company_manager(current_user, report.project_id)

  async def on_complete(text: str):
    json = await get_json_from_speech(text)

    if "start_time" in json:
      report.start_time = time.fromisoformat(json["start_time"])

    if "end_time" in json:
      report.end_time = time.fromisoformat(json["end_time"])

    if "work_performed" in json:
      report.work_performed = json["work_performed"]

    if "weather" in json:
      report.weather = json["weather"]

    await db.commit()
    await db.refresh(report)

    await ws.send_json({
        "type": "daily_report_updated",
        "payload": {"id": str(report.id)},
    })

  await transcribe_audio(ws, on_complete)