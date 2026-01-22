from pydantic import BaseModel
from datetime import date, time
from uuid import UUID

class DailyReportCreate(BaseModel):
  project_id: UUID
  date: date

class DailyReportUpdate(BaseModel):
  start_time: time | None = None
  end_time: time | None = None
  work_performed: str | None = None
  weather: str | None = None

class DailyReportRead(BaseModel):
  id: UUID
  project_id: UUID
  date: date
  start_time: time
  end_time: time
  work_performed: str
  weather: str

  class Config:
    orm_mode = True
