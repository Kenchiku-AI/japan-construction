from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import date, time

from app.db.session import get_db
from app.db.models import DailyReport, Project, User
from app.schemas.report import DailyReportCreate, DailyReportUpdate, DailyReportRead
from app.core.dependencies import get_current_user, get_current_user_ws, require_company_manager
from app.services.openai import transcribe_audio, get_json_from_speech

router = APIRouter(
  prefix="/reports",
  tags=["reports"]
)

@router.post("/daily", response_model=DailyReportRead, status_code=201)
def create_daily_report(
  payload: DailyReportCreate,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = db.get(Project, payload.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_manager(current_user, project.company_id)

  existing_report = db.execute(
    select(DailyReport).where(
      DailyReport.project_id == payload.project_id,
      DailyReport.date == payload.date,
    )
  ).scalar_one_or_none()

  if existing_report:
    raise HTTPException(
      status_code=409,
      detail="A daily report already exists for this project on this date",
    )

  daily_report = DailyReport(**payload.model_dump())

  db.add(daily_report)
  db.commit()
  db.refresh(daily_report)

  return daily_report

@router.put("/daily/{report_id}", response_model=DailyReportRead)
def update_daily_report(
  report_id: UUID,
  payload: DailyReportUpdate,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  report = db.get(DailyReport, report_id)
  if not report:
    raise HTTPException(status_code=404, detail="Report not found")

  if current_user.role != "admin":
    require_company_member(db, current_user, report.project.company_id)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(report, field, value)

  db.commit()
  db.refresh(report)
  return report

@router.delete("/daily/{report_id}", status_code=204)
def delete_daily_report(
  report_id: UUID,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  report = db.get(DailyReport, report_id)
  if not report:
    raise HTTPException(status_code=404, detail="Daily report not found")

  require_company_manager(current_user, report.project.company_id)

  db.delete(daily_report)
  db.commit()
  return None

@router.websocket("/daily/{report_id}/audio")
async def daily_report_audio(
  ws: WebSocket,
  report_id: UUID,
):
  await ws.accept()

  db = next(get_db())
  current_user = await get_current_user_ws(ws)

  report = db.get(DailyReport, report_id)
  if not report:
    raise HTTPException(status_code=404, detail="Daily report not found")

  require_company_manager(current_user, report.project.company_id)

  async def on_complete(text: str):
    json = await get_json_from_speech(text)

    if json.get("start_time"):
      report.start_time = time.fromisoformat(json["start_time"])

    if json.get("end_time"):
      report.end_time = time.fromisoformat(json["end_time"])

    if json.get("work_performed"):
      report.work_performed = json["work_performed"]

    if json.get("weather"):
      report.weather = json["weather"]

    db.commit()
    db.refresh(report)

    await ws.send_json({
      "type": "daily_report_updated",
      "payload": {"id": str(report.id)},
    })

  await transcribe_audio(ws, on_complete)