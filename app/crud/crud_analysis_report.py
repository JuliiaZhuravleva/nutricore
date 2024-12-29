from typing import Optional, List
from sqlalchemy.orm import Session

from app.models.analysis_report import AnalysisReport
from app.schemas.analysis_report import AnalysisReportCreate, AnalysisReportUpdate


class CRUDAnalysisReport:
    def get(self, db: Session, report_id: int) -> Optional[AnalysisReport]:
        return db.query(AnalysisReport).filter(AnalysisReport.id == report_id).first()

    def get_multi(self, db: Session, skip: int = 0, limit: int = 100) -> List[AnalysisReport]:
        return db.query(AnalysisReport).offset(skip).limit(limit).all()

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
        if obj_in.report_type is not None:
            db_obj.report_type = obj_in.report_type
        if obj_in.period_start is not None:
            db_obj.period_start = obj_in.period_start
        if obj_in.period_end is not None:
            db_obj.period_end = obj_in.period_end
        if obj_in.analysis is not None:
            db_obj.analysis = obj_in.analysis
        if obj_in.metrics is not None:
            db_obj.metrics = obj_in.metrics
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, report_id: int) -> AnalysisReport:
        obj = db.query(AnalysisReport).get(report_id)
        db.delete(obj)
        db.commit()
        return obj


crud_analysis_report = CRUDAnalysisReport()
