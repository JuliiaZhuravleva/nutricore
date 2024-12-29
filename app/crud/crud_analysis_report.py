from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from app.models.analysis_report import AnalysisReport
from app.schemas.analysis_report import AnalysisReportCreate, AnalysisReportUpdate


class CRUDAnalysisReport:
    def get(self, db: Session, report_id: int) -> Optional[AnalysisReport]:
        stmt = select(AnalysisReport).where(AnalysisReport.id == report_id)
        return db.execute(stmt).scalar_one_or_none()

    def get_multi(self, db: Session, skip: int = 0, limit: int = 100) -> List[AnalysisReport]:
        stmt = select(AnalysisReport).offset(skip).limit(limit)
        return list(db.execute(stmt).scalars().all())

    def create(self, db: Session, obj_in: AnalysisReportCreate, user_id: int) -> AnalysisReport:
        db_obj = AnalysisReport(
            user_id=user_id,
            report_type=obj_in.report_type,
            period_start=obj_in.period_start,
            period_end=obj_in.period_end,
            analysis=obj_in.analysis,
            metrics=obj_in.metrics,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: AnalysisReport, obj_in: AnalysisReportUpdate) -> AnalysisReport:
        update_data = obj_in.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, report_id: int) -> AnalysisReport:
        stmt = select(AnalysisReport).where(AnalysisReport.id == report_id)
        obj = db.execute(stmt).scalar_one_or_none()
        if obj:
            db.delete(obj)
            db.commit()
        return obj


crud_analysis_report = CRUDAnalysisReport()