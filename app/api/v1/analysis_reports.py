from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.schemas.analysis_report import AnalysisReportCreate, AnalysisReportUpdate, AnalysisReport
from app.crud.crud_analysis_report import crud_analysis_report

router = APIRouter()


@router.get("/", response_model=List[AnalysisReport])
def read_analysis_reports(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud_analysis_report.get_multi(db, skip=skip, limit=limit)


@router.get("/{report_id}", response_model=AnalysisReport)
def read_analysis_report(report_id: int, db: Session = Depends(get_db)):
    db_report = crud_analysis_report.get(db, report_id=report_id)
    if not db_report:
        raise HTTPException(status_code=404, detail="Analysis report not found.")
    return db_report


@router.post("/", response_model=AnalysisReport)
def create_analysis_report(report_in: AnalysisReportCreate, user_id: int, db: Session = Depends(get_db)):
    return crud_analysis_report.create(db, report_in, user_id)


@router.put("/{report_id}", response_model=AnalysisReport)
def update_analysis_report(report_id: int, report_in: AnalysisReportUpdate, db: Session = Depends(get_db)):
    db_report = crud_analysis_report.get(db, report_id=report_id)
    if not db_report:
        raise HTTPException(status_code=404, detail="Analysis report not found.")
    return crud_analysis_report.update(db, db_report, report_in)


@router.delete("/{report_id}", response_model=AnalysisReport)
def delete_analysis_report(report_id: int, db: Session = Depends(get_db)):
    db_report = crud_analysis_report.get(db, report_id=report_id)
    if not db_report:
        raise HTTPException(status_code=404, detail="Analysis report not found.")
    return crud_analysis_report.remove(db, report_id)
