from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.subscription import Subscription
from app.models.user import User

class CRUDSubscription:
    def get_by_telegram_id(self, db: Session, telegram_id: int) -> Optional[Subscription]:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if not user:
            return None
        return db.query(Subscription).filter(Subscription.user_id == user.id).first()
    
    def create_subscription(
        self, 
        db: Session, 
        telegram_id: int, 
        granted_by_telegram_id: int,
        months: int = 1
    ) -> Optional[Subscription]:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        admin = db.query(User).filter(User.telegram_id == str(granted_by_telegram_id)).first()
        
        if not user or not admin:
            return None
            
        now = datetime.utcnow()
        end_date = datetime(now.year + ((now.month + months - 1) // 12),
                          ((now.month + months - 1) % 12) + 1,
                          now.day,
                          now.hour, now.minute, now.second)
        
        subscription = Subscription(
            user_id=user.id,
            is_active=True,
            start_date=now,
            end_date=end_date,
            granted_by=admin.id
        )
        
        db.add(subscription)
        db.commit()
        db.refresh(subscription)
        return subscription
    
    def deactivate_subscription(self, db: Session, telegram_id: int) -> bool:
        subscription = self.get_by_telegram_id(db, telegram_id)
        if not subscription:
            return False
            
        subscription.is_active = False
        db.commit()
        return True
    
    def is_active_subscription(self, db: Session, telegram_id: int) -> bool:
        subscription = self.get_by_telegram_id(db, telegram_id)
        if not subscription:
            return False
            
        now = datetime.utcnow()
        return (subscription.is_active and 
                subscription.start_date <= now <= subscription.end_date)

crud_subscription = CRUDSubscription()
