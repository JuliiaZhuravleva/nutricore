from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.subscription import Subscription
from app.models.user import User

class CRUDSubscription:
    def get_by_telegram_id(self, db: Session, telegram_id: int) -> Optional[Subscription]:
        """Get subscription by telegram_id."""
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return None
        return user.subscription
    
    def create_subscription(
        self, 
        db: Session, 
        telegram_id: int, 
        granted_by_telegram_id: int,
        months: int = 1
    ) -> Optional[Subscription]:
        """Create new subscription. If user doesn't exist, creates them automatically."""
        # Get or create user
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            user = User(telegram_id=telegram_id)
            db.add(user)
            db.flush()  # Get the user.id without committing

        # Get or create admin
        admin = db.query(User).filter(User.telegram_id == granted_by_telegram_id).first()
        if not admin:
            admin = User(telegram_id=granted_by_telegram_id)
            db.add(admin)
            db.flush()  # Get the admin.id without committing
            
        now = datetime.utcnow()
        end_date = datetime(now.year + ((now.month + months - 1) // 12),
                          ((now.month + months - 1) % 12) + 1,
                          now.day,
                          now.hour, now.minute, now.second)
        
        # Check if user already has a subscription
        existing_subscription = db.query(Subscription).filter(
            Subscription.user_id == user.id
        ).first()
        
        if existing_subscription:
            # Update existing subscription
            existing_subscription.is_active = True
            existing_subscription.start_date = now
            existing_subscription.end_date = end_date
            existing_subscription.granted_by = admin.id
            subscription = existing_subscription
        else:
            # Create new subscription
            subscription = Subscription(
                user_id=user.id,
                is_active=True,
                start_date=now,
                end_date=end_date,
                granted_by=admin.id
            )
            db.add(subscription)
        
        try:
            db.commit()
            db.refresh(subscription)
            return subscription
        except Exception:
            db.rollback()
            return None
    
    def deactivate_subscription(self, db: Session, telegram_id: int) -> bool:
        """Deactivate user subscription."""
        subscription = self.get_by_telegram_id(db, telegram_id)
        if not subscription:
            return False
            
        subscription.is_active = False
        db.commit()
        return True
    
    def is_active_subscription(self, db: Session, telegram_id: int) -> bool:
        """Check if user has active subscription."""
        subscription = self.get_by_telegram_id(db, telegram_id)
        if not subscription:
            return False
            
        return subscription.is_active and subscription.end_date > datetime.utcnow()

crud_subscription = CRUDSubscription()
