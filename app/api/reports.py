from fastapi import APIRouter, UploadFile, File, Form
from app.services import ai_speech, ai_llm, ai_vision, storage, db

router = APIRouter()

@router.post("/submit-audio")
async def submit_audio(
    project_id: int = Form(...),
    report_date: str = Form(...),
    audio_file: UploadFile = File(...)
):
    # save audio to disk
    path = storage.save_file(audio_file, folder="audio")
    # transcribe
    text = ai_speech.transcribe_whisper(path)
    # store raw text
    db.save_voice_text(project_id, report_date, text)
    return {"raw_text": text}


@router.post("/submit-photos")
async def submit_photos(
    project_id: int = Form(...),
    report_date: str = Form(...),
    photos: list[UploadFile] = File(...)
):
    results = []
    for f in photos:
        path = storage.save_file(f, folder="photos")
        # classify
        classification = ai_vision.classify_photo(path)
        db.save_photo(project_id, report_date, path, classification)
        results.append(classification)
    return {"results": results}


@router.post("/generate")
def generate_report(
    project_id: int = Form(...),
    report_date: str = Form(...),
    weather: str = Form(...),
    workers: int = Form(...)
):
    # 1) fetch raw text + previous photos
    raw_notes = db.get_voice_text(project_id, report_date)
    photos = db.get_photos(project_id, report_date)

    # 2) normalize notes
    normalized_notes = ai_llm.normalize_text(raw_notes)

    # 3) summarize photo data
    photo_summary = ai_vision.summarize_photos(photos)

    # 4) structured report
    structured = ai_llm.generate_daily_report(
        date=report_date,
        weather=weather,
        workers=workers,
        notes=normalized_notes,
        photo_summary=photo_summary
    )

    # 5) persist and return
    report = db.save_daily_report(project_id, report_date, weather, workers, structured)
    return report
