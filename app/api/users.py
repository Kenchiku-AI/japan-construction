from datetime import datetime, timedelta, timezone
import uuid
import secrets

from fastapi import (
  HTTPException,
  status,
  APIRouter,
  Depends,
  BackgroundTasks,
)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload

from uuid import uuid4, UUID

from app.core.dependencies import get_current_user
from app.core.config import settings
from app.core.security import hash_token

from app.db.models.custom_field import (
  CustomField,
  CustomFieldUserLink,
)
from app.db.models.custom_field_definition import CustomFieldDefinition
from app.db.models.custom_relationship import CustomRelationship
from app.db.models.custom_relationship_definition import CustomRelationshipDefinition
from app.db.models.user import User
from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.project import Project
from app.db.models.project_guest_link import ProjectGuestLink

from app.db.session import get_db

from app.schemas.user import (
  UserWithCompanyAndProjects,
  UserBase,
  UserWithCompanyIdAndRole,
  UserUpdate,
)
from app.schemas.custom_field import CustomFieldRead
from app.schemas.custom_relationship import CustomRelationshipRead
from app.services.users import build_user_with_company_and_projects
from app.services.email import send_password_reset_email


router = APIRouter(
  prefix="/users",
  tags=["users"],
)


@router.get(
  "/me",
  response_model=UserWithCompanyAndProjects,
)
async def read_current_user(
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  return await build_user_with_company_and_projects(
    current_user,
    db,
  )


@router.get(
  "/{user_id}",
  response_model=UserWithCompanyIdAndRole,
)
async def get_user(
  user_id: UUID,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):

  # -------------------------------------------------------------------------
  # Authorization
  # -------------------------------------------------------------------------

  if current_user.role != "admin" and current_user.id != user_id:

    target_company_id = (
      await db.execute(
        select(User.company_id)
        .where(User.id == user_id)
      )
    ).scalar_one_or_none()

    is_same_company = (
      current_user.company_id is not None
      and current_user.company_id == target_company_id
    )

    if not is_same_company:

      shared_project = (
        await db.execute(
          select(Project.id)
          .join(
            ProjectGuestLink,
            ProjectGuestLink.project_id == Project.id,
          )
          .where(
            or_(
              and_(
                Project.company_id == current_user.company_id,
                ProjectGuestLink.user_id == user_id,
              ),
              and_(
                ProjectGuestLink.user_id == user_id,
                Project.id.in_(
                  select(ProjectGuestLink.project_id)
                  .where(
                    ProjectGuestLink.user_id == current_user.id
                  )
                ),
              ),
              and_(
                ProjectGuestLink.user_id == current_user.id,
                Project.company_id == target_company_id,
              ),
            )
          )
          .limit(1)
        )
      ).first()

      if not shared_project:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="Not authorized to access this user",
        )

  # -------------------------------------------------------------------------
  # Load user and existing custom fields
  # -------------------------------------------------------------------------

  result = await db.execute(
    select(User)
    .where(User.id == user_id)
    .options(
      selectinload(
        User.custom_field_links
      ).selectinload(
        CustomFieldUserLink.custom_field
      ).selectinload(
        CustomField.definition
      ),
    )
  )

  user = result.scalar_one_or_none()

  if not user:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  # -------------------------------------------------------------------------
  # Custom fields
  #
  # get_user_custom_fields() queries the definitions and combines them
  # with any existing values, so definitions without values are included.
  # -------------------------------------------------------------------------

  custom_fields = await get_user_custom_fields(
    user,
    db,
  )

  # -------------------------------------------------------------------------
  # Custom relationships
  #
  # Query all relationship definitions for this company where the user
  # is the source entity.
  # -------------------------------------------------------------------------

  custom_relationships = await get_user_custom_relationships(
    user,
    db,
  )

  # -------------------------------------------------------------------------
  # Response
  # -------------------------------------------------------------------------

  return UserWithCompanyIdAndRole(
    email=user.email,
    first_name=user.first_name,
    last_name=user.last_name,
    company_id=user.company_id,
    role=user.role,
    custom_fields=custom_fields,
    custom_relationships=custom_relationships,
  )


@router.patch(
  "/{user_id}",
  response_model=UserWithCompanyIdAndRole,
)
async def patch_user(
  user_id: UUID,
  payload: UserUpdate,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(
    select(User)
    .where(User.id == user_id)
  )

  user = result.scalar_one_or_none()

  if not user:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  update_data = payload.model_dump(
    exclude_unset=True,
    exclude_none=True,
  )

  is_own_account = current_user.id == user_id

  # -------------------------------------------------------------------------
  # Authorization
  # -------------------------------------------------------------------------

  if user.role == "admin" and current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can update an admin's account",
    )

  if current_user.role == "manager":

    if update_data.get("role") == "admin":
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Cannot assign admin role",
      )

    if is_own_account:
      pass

    else:
      if current_user.company_id != user.company_id:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="Managers can only manage users in their company",
        )

      non_role_fields = {
        key: value
        for key, value in update_data.items()
        if key != "role"
      }

      if non_role_fields:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="Managers can only update the role of other users",
        )

  elif current_user.role == "user":

    if not is_own_account:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to update this user",
      )

    if "role" in update_data:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Users cannot change their own role",
      )

  # -------------------------------------------------------------------------
  # Email uniqueness
  # -------------------------------------------------------------------------

  if (
    "email" in update_data
    and update_data["email"] != user.email
  ):
    existing = await db.execute(
      select(User)
      .where(User.email == update_data["email"])
    )

    if existing.scalar_one_or_none():
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Email already in use",
      )

  # -------------------------------------------------------------------------
  # Standard user fields only
  # -------------------------------------------------------------------------

  for field, value in update_data.items():
    setattr(user, field, value)

  await db.commit()

  # -------------------------------------------------------------------------
  # Reload the user with custom fields
  # -------------------------------------------------------------------------

  result = await db.execute(
    select(User)
    .where(User.id == user_id)
    .options(
      selectinload(
        User.custom_field_links
      ).selectinload(
        CustomFieldUserLink.custom_field
      ).selectinload(
        CustomField.definition
      ),
    )
  )

  user = result.scalar_one()

  # -------------------------------------------------------------------------
  # Build custom fields and relationships for the response.
  #
  # These are read-only here. PATCH does not modify either one.
  # -------------------------------------------------------------------------

  custom_fields = await get_user_custom_fields(
    user,
    db,
  )

  custom_relationships = await get_user_custom_relationships(
    user,
    db,
  )

  return UserWithCompanyIdAndRole(
    email=user.email,
    first_name=user.first_name,
    last_name=user.last_name,
    company_id=user.company_id,
    role=user.role,
    custom_fields=custom_fields,
    custom_relationships=custom_relationships,
  )


async def get_user_custom_fields(
  user: User,
  db: AsyncSession,
) -> list[CustomFieldRead]:

  definitions_result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.company_id == user.company_id,
      CustomFieldDefinition.entity_type == "user",
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  definitions = definitions_result.scalars().all()

  existing_fields = {
    link.custom_field.custom_field_definition_id: link.custom_field
    for link in user.custom_field_links
    if link.custom_field is not None
  }

  return [
    CustomFieldRead(
      id=existing_fields[definition.id].id
        if definition.id in existing_fields
        else None,
      value=existing_fields[definition.id].value
        if definition.id in existing_fields
        else None,
      definition=definition,
    )
    for definition in definitions
  ]


async def get_user_custom_relationships(
  user: User,
  db: AsyncSession,
) -> list[CustomRelationshipRead]:

  if user.company_id is None:
    return []

  # -------------------------------------------------------------------------
  # Get every relationship definition applicable to users in this company.
  # -------------------------------------------------------------------------

  definitions_result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id == user.company_id,
      CustomRelationshipDefinition.source_entity_type == "user",
    )
    .order_by(
      CustomRelationshipDefinition.sort_order,
      CustomRelationshipDefinition.name,
    )
  )

  definitions = definitions_result.scalars().all()

  if not definitions:
    return []

  definition_ids = {
    definition.id
    for definition in definitions
  }

  # -------------------------------------------------------------------------
  # Get existing relationships for this user.
  # -------------------------------------------------------------------------

  relationships_result = await db.execute(
    select(CustomRelationship)
    .where(
      CustomRelationship.company_id == user.company_id,
      CustomRelationship.source_entity_type == "user",
      CustomRelationship.source_entity_id == user.id,
      CustomRelationship.custom_relationship_definition_id.in_(
        definition_ids
      ),
    )
    .order_by(
      CustomRelationship.created_at,
    )
  )

  existing_relationships = (
    relationships_result.scalars().all()
  )

  # -------------------------------------------------------------------------
  # Group existing relationships by definition.
  # -------------------------------------------------------------------------

  existing_by_definition = {}

  for relationship in existing_relationships:
    existing_by_definition.setdefault(
      relationship.custom_relationship_definition_id,
      [],
    ).append(relationship)

  # -------------------------------------------------------------------------
  # Build the response from definitions.
  #
  # This guarantees that a definition is returned even when there is no
  # existing relationship.
  # -------------------------------------------------------------------------

  results = []

  for definition in definitions:

    relationships = existing_by_definition.get(
      definition.id,
      [],
    )

    if relationships:

      for relationship in relationships:

        results.append(
          CustomRelationshipRead(
            id=relationship.id,
            source_entity_id=relationship.source_entity_id,
            target_entity_id=relationship.target_entity_id,
            definition=definition,
          )
        )

    else:

      results.append(
        CustomRelationshipRead(
          id=None,
          source_entity_id=user.id,
          target_entity_id=None,
          definition=definition,
        )
      )

  return results


@router.post(
  "/create-admin",
)
async def create_admin(
  payload: UserBase,
  background_tasks: BackgroundTasks,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):

  if current_user.email != settings.SUPER_USER_EMAIL:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only super user can create admins",
    )

  result = await db.execute(
    select(User)
    .where(User.email == payload.email)
  )

  existing_user = result.scalar_one_or_none()

  if existing_user:
    raise HTTPException(
      status_code=400,
      detail="Email already registered",
    )

  user = User(
    email=payload.email,
    first_name=payload.first_name,
    last_name=payload.last_name,
    role="admin",
  )

  db.add(user)

  await db.flush()

  token = secrets.token_urlsafe(32)

  hashed_token = hash_token(token)

  expires_at = (
    datetime.now(timezone.utc)
    + timedelta(minutes=30)
  )

  reset_entry = PasswordResetToken(
    id=str(uuid4()),
    user_id=user.id,
    token_hash=hashed_token,
    expires_at=expires_at,
  )

  db.add(reset_entry)

  await db.commit()

  background_tasks.add_task(
    send_password_reset_email,
    user.email,
    token,
  )

  return {
    "success": True,
  }


@router.delete(
  "/{user_id}/company",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_user_from_company(
  user_id: UUID,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):

  if current_user.role == "user":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not authorized to remove users from a company",
    )

  result = await db.execute(
    select(User)
    .where(User.id == user_id)
  )

  target = result.scalar_one_or_none()

  if not target:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  if target.company_id is None:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="User is not a member of any company",
    )

  if target.role == "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Cannot remove an admin from a company",
    )

  if current_user.role == "manager":

    if current_user.company_id != target.company_id:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Managers can only remove users from their own company",
      )

  target.company_id = None
  target.role = "user"

  await db.commit()