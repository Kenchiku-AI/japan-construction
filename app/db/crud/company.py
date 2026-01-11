from sqlalchemy.orm import Session
from app.db.models.user import User
from app.db.models.company import Company

def create_company(db: Session, company_data: CompanyCreate, creator: User):
  db_company = Company(name=company_data.name)
  db_company.members.append(creator)  # add creator as member
  db.add(db_company)
  db.commit()
  db.refresh(db_company)
  return db_company

def get_company(db: Session, company_id: int):
  return db.query(Company).filter(Company.id == company_id).first()

def invite_user(db: Session, company: Company, user: User, role: str = "member"):
  if user not in company.members:
    company.members.append(user)
    # TODO: store role in association table if needed
    db.commit()
    db.refresh(company)
  return company
