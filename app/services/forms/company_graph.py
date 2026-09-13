from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company import Company
from app.db.models.project import Project
from app.db.models.user import User
from app.db.models.custom_object import CustomObject
from app.db.models.custom_object_definition import (
  CustomObjectDefinition,
)
from app.db.models.custom_field import (
  CustomField,
  CustomFieldCompanyLink,
  CustomFieldProjectLink,
  CustomFieldUserLink,
  CustomFieldCustomObjectLink,
)
from app.db.models.custom_field_definition import (
  CustomFieldDefinition,
)
from app.db.models.custom_relationship import (
  CustomRelationship,
)
from app.db.models.custom_relationship_definition import (
  CustomRelationshipDefinition,
)


ENTITY_TYPES = (
  "company",
  "project",
  "user",
  "custom_object",
)


def _value(value: Any) -> str:
  if value is None:
    return ""

  if hasattr(value, "value"):
    return str(value.value)

  return str(value)


def _short_id(
  entity_type: str,
  entity_id: UUID,
) -> str:
  prefixes = {
    "company": "C",
    "project": "P",
    "user": "U",
    "custom_object": "O",
  }

  normalized_type = _value(entity_type)

  prefix = prefixes.get(
    normalized_type,
    "E",
  )

  return f"{prefix}-{str(entity_id)[:8]}"


def _entity_key(
  entity_type: str,
  entity_id: UUID,
) -> tuple[str, UUID]:
  return (
    _value(entity_type),
    entity_id,
  )


def _entity_label(
  entity_type: str,
  entity: Any,
  object_definitions: dict[
    UUID,
    CustomObjectDefinition,
  ],
) -> str:
  entity_type = _value(entity_type)

  if entity_type == "company":
    return entity.name or "Company"

  if entity_type == "project":
    return entity.name or "Project"

  if entity_type == "user":
    name = " ".join(
      part
      for part in [
        entity.last_name,
        entity.first_name,
      ]
      if part
    ).strip()

    return (
      name
      or entity.email
      or "User"
    )

  if entity_type == "custom_object":
    definition = object_definitions.get(
      entity.custom_object_definition_id,
    )

    if definition is not None:
      return definition.name

    return "Custom object"

  return entity_type


async def build_company_graph(
  db: AsyncSession,
  company_id: UUID,
  project_id: UUID | None = None,
) -> str:

  # ==========================================================
  # LOAD CORE ENTITIES
  # ==========================================================

  company = await db.scalar(
    select(Company)
    .where(
      Company.id == company_id,
    )
  )

  if company is None:
    raise ValueError(
      f"Company {company_id} not found"
    )

  projects = list(
    (
      await db.scalars(
        select(Project)
        .where(
          Project.company_id == company_id,
        )
        .order_by(Project.name)
      )
    ).all()
  )

  users = list(
    (
      await db.scalars(
        select(User)
        .where(
          User.company_id == company_id,
        )
        .order_by(
          User.last_name,
          User.first_name,
          User.email,
        )
      )
    ).all()
  )

  object_definitions = list(
    (
      await db.scalars(
        select(CustomObjectDefinition)
        .where(
          CustomObjectDefinition.company_id
          == company_id,
        )
        .order_by(
          CustomObjectDefinition.name,
        )
      )
    ).all()
  )

  custom_objects = list(
    (
      await db.scalars(
        select(CustomObject)
        .where(
          CustomObject.company_id
          == company_id,
        )
        .order_by(
          CustomObject.created_at,
        )
      )
    ).all()
  )

  field_definitions = list(
    (
      await db.scalars(
        select(CustomFieldDefinition)
        .where(
          CustomFieldDefinition.company_id
          == company_id,
        )
        .order_by(
          CustomFieldDefinition.sort_order,
          CustomFieldDefinition.name,
        )
      )
    ).all()
  )

  fields = list(
    (
      await db.scalars(
        select(CustomField)
        .where(
          CustomField.company_id
          == company_id,
        )
      )
    ).all()
  )

  relationship_definitions = list(
    (
      await db.scalars(
        select(CustomRelationshipDefinition)
        .where(
          CustomRelationshipDefinition.company_id
          == company_id,
        )
        .order_by(
          CustomRelationshipDefinition.sort_order,
          CustomRelationshipDefinition.name,
        )
      )
    ).all()
  )

  relationships = list(
    (
      await db.scalars(
        select(CustomRelationship)
        .where(
          CustomRelationship.company_id
          == company_id,
        )
      )
    ).all()
  )

  # ==========================================================
  # VALIDATE PRIMARY PROJECT
  # ==========================================================

  project = None

  if project_id is not None:
    project = next(
      (
        item
        for item in projects
        if item.id == project_id
      ),
      None,
    )

    if project is None:
      raise ValueError(
        f"Project {project_id} does not belong to "
        f"company {company_id}"
      )

  # ==========================================================
  # INDEX DEFINITIONS
  # ==========================================================

  object_def_by_id = {
    item.id: item
    for item in object_definitions
  }

  field_def_by_id = {
    item.id: item
    for item in field_definitions
  }

  relationship_def_by_id = {
    item.id: item
    for item in relationship_definitions
  }

  # ==========================================================
  # INDEX ENTITIES
  # ==========================================================

  entity_by_key: dict[
    tuple[str, UUID],
    Any,
  ] = {}

  for entity in [company]:
    entity_by_key[
      _entity_key(
        "company",
        entity.id,
      )
    ] = entity

  for entity in projects:
    entity_by_key[
      _entity_key(
        "project",
        entity.id,
      )
    ] = entity

  for entity in users:
    entity_by_key[
      _entity_key(
        "user",
        entity.id,
      )
    ] = entity

  for entity in custom_objects:
    entity_by_key[
      _entity_key(
        "custom_object",
        entity.id,
      )
    ] = entity

  short_ids = {
    key: _short_id(
      key[0],
      key[1],
    )
    for key in entity_by_key
  }

  # ==========================================================
  # LOAD CUSTOM FIELD LINKS
  # ==========================================================

  company_field_links = list(
    (
      await db.scalars(
        select(CustomFieldCompanyLink)
        .join(
          CustomField,
          CustomField.id
          == CustomFieldCompanyLink.custom_field_id,
        )
        .where(
          CustomField.company_id
          == company_id,
        )
      )
    ).all()
  )

  project_field_links = list(
    (
      await db.scalars(
        select(CustomFieldProjectLink)
        .join(
          CustomField,
          CustomField.id
          == CustomFieldProjectLink.custom_field_id,
        )
        .join(
          Project,
          Project.id
          == CustomFieldProjectLink.project_id,
        )
        .where(
          Project.company_id
          == company_id,
        )
      )
    ).all()
  )

  user_field_links = list(
    (
      await db.scalars(
        select(CustomFieldUserLink)
        .join(
          CustomField,
          CustomField.id
          == CustomFieldUserLink.custom_field_id,
        )
        .join(
          User,
          User.id
          == CustomFieldUserLink.user_id,
        )
        .where(
          User.company_id
          == company_id,
        )
      )
    ).all()
  )

  object_field_links = list(
    (
      await db.scalars(
        select(CustomFieldCustomObjectLink)
        .join(
          CustomField,
          CustomField.id
          == CustomFieldCustomObjectLink.custom_field_id,
        )
        .join(
          CustomObject,
          CustomObject.id
          == CustomFieldCustomObjectLink.custom_object_id,
        )
        .where(
          CustomObject.company_id
          == company_id,
        )
      )
    ).all()
  )

  # ==========================================================
  # INDEX CUSTOM FIELD VALUES
  # ==========================================================

  field_by_id = {
    field.id: field
    for field in fields
  }

  field_values: dict[
    tuple[str, UUID],
    list[dict[str, Any]],
  ] = defaultdict(list)

  def add_field(
    entity_type: str,
    entity_id: UUID,
    field: CustomField,
  ) -> None:
    definition = field_def_by_id.get(
      field.custom_field_definition_id,
    )

    if definition is None:
      return

    field_values[
      _entity_key(
        entity_type,
        entity_id,
      )
    ].append(
      {
        "name": definition.name,
        "description": definition.description,
        "value": field.value,
        "data_type": definition.data_type,
        "sort_order": definition.sort_order,
      }
    )

  for link in company_field_links:
    field = field_by_id.get(
      link.custom_field_id,
    )

    if field is not None:
      add_field(
        "company",
        link.company_id,
        field,
      )

  for link in project_field_links:
    field = field_by_id.get(
      link.custom_field_id,
    )

    if field is not None:
      add_field(
        "project",
        link.project_id,
        field,
      )

  for link in user_field_links:
    field = field_by_id.get(
      link.custom_field_id,
    )

    if field is not None:
      add_field(
        "user",
        link.user_id,
        field,
      )

  for link in object_field_links:
    field = field_by_id.get(
      link.custom_field_id,
    )

    if field is not None:
      add_field(
        "custom_object",
        link.custom_object_id,
        field,
      )

  for values in field_values.values():
    values.sort(
      key=lambda item: (
        item["sort_order"] or 0,
        item["name"],
      )
    )

  # ==========================================================
  # INDEX RELATIONSHIPS
  # ==========================================================

  #
  # IMPORTANT:
  #
  # Each relationship is stored once, but indexed under BOTH
  # the source and target entity.
  #
  # This means the graph can answer:
  #
  #   "What does this project point to?"
  #
  # AND:
  #
  #   "What points to this project?"
  #
  # without losing the actual source -> target direction.
  #

  relationships_by_entity: dict[
    tuple[str, UUID],
    list[CustomRelationship],
  ] = defaultdict(list)

  valid_relationships: list[
    tuple[
      CustomRelationship,
      CustomRelationshipDefinition,
      tuple[str, UUID],
      tuple[str, UUID],
    ]
  ] = []

  for relationship in relationships:
    definition = relationship_def_by_id.get(
      relationship.custom_relationship_definition_id,
    )

    if definition is None:
      continue

    source_key = _entity_key(
      relationship.source_entity_type,
      relationship.source_entity_id,
    )

    target_key = _entity_key(
      relationship.target_entity_type,
      relationship.target_entity_id,
    )

    if source_key not in entity_by_key:
      continue

    if target_key not in entity_by_key:
      continue

    relationships_by_entity[
      source_key
    ].append(relationship)

    relationships_by_entity[
      target_key
    ].append(relationship)

    valid_relationships.append(
      (
        relationship,
        definition,
        source_key,
        target_key,
      )
    )

  # ==========================================================
  # RENDER HELPERS
  # ==========================================================

  def render_custom_fields(
    entity_key: tuple[str, UUID],
    indent: str = "  ",
  ) -> list[str]:
    values = field_values.get(
      entity_key,
      [],
    )

    if not values:
      return [
        f"{indent}Custom fields: (none)"
      ]

    lines = [
      f"{indent}Custom fields:"
    ]

    for field in values:
      value = field["value"]

      if value is None or value == "":
        value_display = "(empty)"
      else:
        value_display = _value(value)

      lines.append(
        f"{indent}  - {field['name']}: "
        f"{value_display}"
      )

      if field["description"]:
        lines.append(
          f"{indent}    Meaning: "
          f"{field['description']}"
        )

      if field["data_type"]:
        lines.append(
          f"{indent}    Data type: "
          f"{_value(field['data_type'])}"
        )

    return lines

  def render_relationships(
    entity_key: tuple[str, UUID],
    indent: str = "  ",
  ) -> list[str]:
    entity_relationships = relationships_by_entity.get(
      entity_key,
      [],
    )

    if not entity_relationships:
      return [
        f"{indent}Relationships: (none)"
      ]

    lines = [
      f"{indent}Relationships:"
    ]

    for relationship in entity_relationships:
      definition = relationship_def_by_id.get(
        relationship.custom_relationship_definition_id,
      )

      source_key = _entity_key(
        relationship.source_entity_type,
        relationship.source_entity_id,
      )

      target_key = _entity_key(
        relationship.target_entity_type,
        relationship.target_entity_id,
      )

      if (
        source_key not in short_ids
        or target_key not in short_ids
      ):
        continue

      relationship_name = (
        definition.name
        if definition is not None
        else "(unknown relationship)"
      )

      current_key = entity_key

      if current_key == source_key:
        direction = "OUTGOING"
        arrow = "-->"
        other_key = target_key
      elif current_key == target_key:
        direction = "INCOMING"
        arrow = "<--"
        other_key = source_key
      else:
        continue

      other_label = _entity_label(
        other_key[0],
        entity_by_key[other_key],
        object_def_by_id,
      )

      lines.append(
        f"{indent}  - [{direction}] "
        f"{short_ids[entity_key]} "
        f"{arrow} "
        f"{short_ids[other_key]} "
        f"[{relationship_name}] "
        f"{other_label}"
      )

      lines.append(
        f"{indent}    Relationship ID: "
        f"{relationship.id}"
      )

      if definition is not None:
        if definition.description:
          lines.append(
            f"{indent}    Meaning: "
            f"{definition.description}"
          )

        lines.append(
          f"{indent}    Cardinality: "
          f"{_value(definition.cardinality)}"
        )

    return lines

  def render_entity(
    entity_type: str,
    entity: Any,
    indent: str = "",
  ) -> list[str]:
    key = _entity_key(
      entity_type,
      entity.id,
    )

    short_id = short_ids[key]

    label = _entity_label(
      entity_type,
      entity,
      object_def_by_id,
    )

    lines = []

    lines.append(
      f"{indent}[{short_id}]"
    )

    lines.append(
      f"{indent}Name: {label}"
    )

    lines.append(
      f"{indent}ID: {entity.id}"
    )

    normalized_type = _value(entity_type)

    if normalized_type == "company":
      if getattr(
        entity,
        "corporate_number",
        None,
      ):
        lines.append(
          f"{indent}Corporate number: "
          f"{entity.corporate_number}"
        )

    elif normalized_type == "project":
      if entity.description:
        lines.append(
          f"{indent}Description: "
          f"{entity.description}"
        )

      if entity.status:
        lines.append(
          f"{indent}Status: "
          f"{_value(entity.status)}"
        )

      lines.append(
        f"{indent}Company ID: "
        f"{entity.company_id}"
      )

    elif normalized_type == "user":
      if entity.first_name:
        lines.append(
          f"{indent}First name: "
          f"{entity.first_name}"
        )

      if entity.last_name:
        lines.append(
          f"{indent}Last name: "
          f"{entity.last_name}"
        )

      if entity.email:
        lines.append(
          f"{indent}Email: "
          f"{entity.email}"
        )

      if entity.role:
        lines.append(
          f"{indent}Role: "
          f"{_value(entity.role)}"
        )

      lines.append(
        f"{indent}Company ID: "
        f"{entity.company_id}"
      )

    elif normalized_type == "custom_object":
      definition = object_def_by_id.get(
        entity.custom_object_definition_id,
      )

      if definition is not None:
        lines.append(
          f"{indent}Type: "
          f"{definition.name}"
        )

        if definition.description:
          lines.append(
            f"{indent}Type description: "
            f"{definition.description}"
          )

      lines.append(
        f"{indent}Company ID: "
        f"{entity.company_id}"
      )

    lines.extend(
      render_custom_fields(
        key,
        indent,
      )
    )

    lines.extend(
      render_relationships(
        key,
        indent,
      )
    )

    return lines

  # ==========================================================
  # BEGIN GRAPH
  # ==========================================================

  lines: list[str] = []

  lines.append(
    "============================================================"
  )
  lines.append(
    "KENCHIKU COMPANY DATA GRAPH"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")
  lines.append(
    "This graph contains authoritative Kenchiku application data."
  )
  lines.append(
    "Use it directly when completing the form."
  )
  lines.append(
    "Do not attempt to retrieve Kenchiku data through tools."
  )
  lines.append("")
  lines.append(
    "ENTITY ID FORMAT:"
  )
  lines.append(
    "  C-XXXXXXXX = Company"
  )
  lines.append(
    "  P-XXXXXXXX = Project"
  )
  lines.append(
    "  U-XXXXXXXX = User"
  )
  lines.append(
    "  O-XXXXXXXX = Custom object"
  )
  lines.append("")
  lines.append(
    "IMPORTANT GRAPH RULES:"
  )
  lines.append(
    "- Explicit relationships are authoritative."
  )
  lines.append(
    "- Relationship direction is significant."
  )
  lines.append(
    "- A relationship is shown under both entities, but its"
  )
  lines.append(
    "  actual source and target are always preserved."
  )
  lines.append(
    "- Do not infer relationships merely from shared company,"
  )
  lines.append(
    "  names, emails, or similar field values."
  )
  lines.append(
    "- Custom field values belong only to the entity where shown."
  )
  lines.append(
    "- Do not invent missing information."
  )
  lines.append("")

  # ==========================================================
  # PRIMARY PROJECT
  # ==========================================================

  if project is not None:
    project_key = _entity_key(
      "project",
      project.id,
    )

    lines.append(
      "============================================================"
    )
    lines.append(
      "PRIMARY PROJECT CONTEXT"
    )
    lines.append(
      "============================================================"
    )
    lines.append("")

    lines.append(
      f"Primary project graph ID: "
      f"{short_ids[project_key]}"
    )

    lines.append(
      f"Primary project name: "
      f"{project.name}"
    )

    if project.description:
      lines.append(
        f"Primary project description: "
        f"{project.description}"
      )

    lines.append("")
    lines.append(
      "This project is the primary context for the current form job."
    )
    lines.append(
      "Prioritize entities explicitly related to this project."
    )
    lines.append("")

  # ==========================================================
  # CUSTOM OBJECT DEFINITIONS
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "CUSTOM OBJECT DEFINITIONS"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  if not object_definitions:
    lines.append(
      "(No custom object definitions.)"
    )
  else:
    for definition in object_definitions:
      lines.append(
        f"OBJECT TYPE: {definition.name}"
      )
      lines.append(
        f"Definition ID: {definition.id}"
      )

      if definition.description:
        lines.append(
          f"Description: "
          f"{definition.description}"
        )

      definition_fields = [
        field_definition
        for field_definition in field_definitions
        if (
          field_definition.custom_object_definition_id
          == definition.id
        )
      ]

      definition_fields.sort(
        key=lambda item: (
          item.sort_order or 0,
          item.name,
        )
      )

      lines.append(
        "Fields:"
      )

      if not definition_fields:
        lines.append(
          "  (none)"
        )
      else:
        for field_definition in definition_fields:
          lines.append(
            f"  - {field_definition.name}"
          )

          lines.append(
            f"    Definition ID: "
            f"{field_definition.id}"
          )

          if field_definition.description:
            lines.append(
              f"    Meaning: "
              f"{field_definition.description}"
            )

          lines.append(
            f"    Data type: "
            f"{_value(field_definition.data_type)}"
          )

      lines.append("")

  # ==========================================================
  # CUSTOM FIELD DEFINITIONS
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "CUSTOM FIELD DEFINITIONS"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  if not field_definitions:
    lines.append(
      "(No custom field definitions.)"
    )
  else:
    for definition in field_definitions:
      lines.append(
        f"FIELD: {definition.name}"
      )

      lines.append(
        f"Definition ID: {definition.id}"
      )

      lines.append(
        f"Entity type: "
        f"{_value(definition.entity_type)}"
      )

      lines.append(
        f"Data type: "
        f"{_value(definition.data_type)}"
      )

      if definition.description:
        lines.append(
          f"Meaning: "
          f"{definition.description}"
        )

      if definition.custom_object_definition_id:
        object_definition = object_def_by_id.get(
          definition.custom_object_definition_id,
        )

        if object_definition is not None:
          lines.append(
            f"Custom object type: "
            f"{object_definition.name}"
          )

      lines.append("")

  # ==========================================================
  # CUSTOM RELATIONSHIP DEFINITIONS
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "CUSTOM RELATIONSHIP DEFINITIONS"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  if not relationship_definitions:
    lines.append(
      "(No custom relationship definitions.)"
    )
  else:
    for definition in relationship_definitions:
      lines.append(
        f"RELATIONSHIP: {definition.name}"
      )

      lines.append(
        f"Definition ID: {definition.id}"
      )

      lines.append(
        f"Source entity type: "
        f"{_value(definition.source_entity_type)}"
      )

      if definition.source_custom_object_definition_id:
        source_definition = object_def_by_id.get(
          definition.source_custom_object_definition_id,
        )

        if source_definition is not None:
          lines.append(
            f"Source custom object type: "
            f"{source_definition.name}"
          )

      lines.append(
        f"Target entity type: "
        f"{_value(definition.target_entity_type)}"
      )

      if definition.target_custom_object_definition_id:
        target_definition = object_def_by_id.get(
          definition.target_custom_object_definition_id,
        )

        if target_definition is not None:
          lines.append(
            f"Target custom object type: "
            f"{target_definition.name}"
          )

      lines.append(
        f"Cardinality: "
        f"{_value(definition.cardinality)}"
      )

      if definition.description:
        lines.append(
          f"Meaning: "
          f"{definition.description}"
        )

      lines.append("")

  # ==========================================================
  # COMPANY
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "COMPANY"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  lines.extend(
    render_entity(
      "company",
      company,
    )
  )

  lines.append("")

  # ==========================================================
  # PROJECTS
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "PROJECTS"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  ordered_projects = list(projects)

  if project is not None:
    ordered_projects.sort(
      key=lambda item: (
        item.id != project.id,
        item.name or "",
      )
    )

  if not ordered_projects:
    lines.append(
      "(No projects.)"
    )
  else:
    for item in ordered_projects:
      marker = (
        " [PRIMARY PROJECT]"
        if project_id == item.id
        else ""
      )

      lines.append(
        f"PROJECT{marker}"
      )

      lines.extend(
        render_entity(
          "project",
          item,
          indent="  ",
        )
      )

      lines.append("")

  # ==========================================================
  # USERS
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "USERS"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  if not users:
    lines.append(
      "(No users.)"
    )
  else:
    for item in users:
      lines.append(
        "USER"
      )

      lines.extend(
        render_entity(
          "user",
          item,
          indent="  ",
        )
      )

      lines.append("")

  # ==========================================================
  # CUSTOM OBJECTS
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "CUSTOM OBJECT INSTANCES"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  if not custom_objects:
    lines.append(
      "(No custom objects.)"
    )
  else:
    for item in custom_objects:
      lines.append(
        "CUSTOM OBJECT"
      )

      lines.extend(
        render_entity(
          "custom_object",
          item,
          indent="  ",
        )
      )

      lines.append("")

  # ==========================================================
  # COMPLETE RELATIONSHIP INDEX
  # ==========================================================

  lines.append(
    "============================================================"
  )
  lines.append(
    "COMPLETE RELATIONSHIP EDGE INDEX"
  )
  lines.append(
    "============================================================"
  )
  lines.append("")

  if not valid_relationships:
    lines.append(
      "(No relationship instances.)"
    )
  else:
    for (
      relationship,
      definition,
      source_key,
      target_key,
    ) in valid_relationships:

      relationship_name = (
        definition.name
        if definition is not None
        else "(unknown relationship)"
      )

      lines.append(
        f"{short_ids[source_key]} "
        f"({ _entity_label(
          source_key[0],
          entity_by_key[source_key],
          object_def_by_id,
        ) }) "
        f"--[{relationship_name}]--> "
        f"{short_ids[target_key]} "
        f"({ _entity_label(
          target_key[0],
          entity_by_key[target_key],
          object_def_by_id,
        ) })"
      )

      lines.append(
        f"  Relationship ID: "
        f"{relationship.id}"
      )

      if definition is not None:
        lines.append(
          f"  Definition ID: "
          f"{definition.id}"
        )

        lines.append(
          f"  Cardinality: "
          f"{_value(definition.cardinality)}"
        )

        if definition.description:
          lines.append(
            f"  Meaning: "
            f"{definition.description}"
          )

      lines.append("")

  lines.append(
    "============================================================"
  )
  lines.append(
    "END KENCHIKU COMPANY DATA GRAPH"
  )
  lines.append(
    "============================================================"
  )

  return "\n".join(lines)

async def build_company_graph_json(
  db: AsyncSession,
  company_id: UUID,
  project_id: UUID | None = None,
) -> dict[str, Any]:
  """
  Same underlying data as build_company_graph, returned as a
  JSON-serializable dict instead of a formatted multi-line string.
  """

  # ==========================================================
  # LOAD CORE ENTITIES (identical to build_company_graph)
  # ==========================================================

  company = await db.scalar(
    select(Company).where(Company.id == company_id)
  )

  if company is None:
    raise ValueError(f"Company {company_id} not found")

  projects = list(
    (
      await db.scalars(
        select(Project)
        .where(Project.company_id == company_id)
        .order_by(Project.name)
      )
    ).all()
  )

  users = list(
    (
      await db.scalars(
        select(User)
        .where(User.company_id == company_id)
        .order_by(User.last_name, User.first_name, User.email)
      )
    ).all()
  )

  object_definitions = list(
    (
      await db.scalars(
        select(CustomObjectDefinition)
        .where(CustomObjectDefinition.company_id == company_id)
        .order_by(CustomObjectDefinition.name)
      )
    ).all()
  )

  custom_objects = list(
    (
      await db.scalars(
        select(CustomObject)
        .where(CustomObject.company_id == company_id)
        .order_by(CustomObject.created_at)
      )
    ).all()
  )

  field_definitions = list(
    (
      await db.scalars(
        select(CustomFieldDefinition)
        .where(CustomFieldDefinition.company_id == company_id)
        .order_by(CustomFieldDefinition.sort_order, CustomFieldDefinition.name)
      )
    ).all()
  )

  fields = list(
    (
      await db.scalars(
        select(CustomField).where(CustomField.company_id == company_id)
      )
    ).all()
  )

  relationship_definitions = list(
    (
      await db.scalars(
        select(CustomRelationshipDefinition)
        .where(CustomRelationshipDefinition.company_id == company_id)
        .order_by(CustomRelationshipDefinition.sort_order, CustomRelationshipDefinition.name)
      )
    ).all()
  )

  relationships = list(
    (
      await db.scalars(
        select(CustomRelationship).where(CustomRelationship.company_id == company_id)
      )
    ).all()
  )

  project = None

  if project_id is not None:
    project = next((p for p in projects if p.id == project_id), None)

    if project is None:
      raise ValueError(
        f"Project {project_id} does not belong to company {company_id}"
      )

  object_def_by_id = {d.id: d for d in object_definitions}
  field_def_by_id = {d.id: d for d in field_definitions}
  relationship_def_by_id = {d.id: d for d in relationship_definitions}

  entity_by_key: dict[tuple[str, UUID], Any] = {}

  for e in [company]:
    entity_by_key[_entity_key("company", e.id)] = e

  for e in projects:
    entity_by_key[_entity_key("project", e.id)] = e

  for e in users:
    entity_by_key[_entity_key("user", e.id)] = e

  for e in custom_objects:
    entity_by_key[_entity_key("custom_object", e.id)] = e

  short_ids = {key: _short_id(key[0], key[1]) for key in entity_by_key}

  # ==========================================================
  # CUSTOM FIELD LINKS (identical to build_company_graph)
  # ==========================================================

  company_field_links = list(
    (
      await db.scalars(
        select(CustomFieldCompanyLink)
        .join(CustomField, CustomField.id == CustomFieldCompanyLink.custom_field_id)
        .where(CustomField.company_id == company_id)
      )
    ).all()
  )

  project_field_links = list(
    (
      await db.scalars(
        select(CustomFieldProjectLink)
        .join(CustomField, CustomField.id == CustomFieldProjectLink.custom_field_id)
        .join(Project, Project.id == CustomFieldProjectLink.project_id)
        .where(Project.company_id == company_id)
      )
    ).all()
  )

  user_field_links = list(
    (
      await db.scalars(
        select(CustomFieldUserLink)
        .join(CustomField, CustomField.id == CustomFieldUserLink.custom_field_id)
        .join(User, User.id == CustomFieldUserLink.user_id)
        .where(User.company_id == company_id)
      )
    ).all()
  )

  object_field_links = list(
    (
      await db.scalars(
        select(CustomFieldCustomObjectLink)
        .join(CustomField, CustomField.id == CustomFieldCustomObjectLink.custom_field_id)
        .join(CustomObject, CustomObject.id == CustomFieldCustomObjectLink.custom_object_id)
        .where(CustomObject.company_id == company_id)
      )
    ).all()
  )

  field_by_id = {f.id: f for f in fields}

  field_values: dict[tuple[str, UUID], list[dict[str, Any]]] = defaultdict(list)

  def add_field(entity_type: str, entity_id: UUID, field: CustomField) -> None:
    definition = field_def_by_id.get(field.custom_field_definition_id)

    if definition is None:
      return

    field_values[_entity_key(entity_type, entity_id)].append(
      {
        "definition_id": str(definition.id),
        "name": definition.name,
        "description": definition.description,
        "value": field.value,
        "data_type": _value(definition.data_type),
        "sort_order": definition.sort_order,
      }
    )

  for link in company_field_links:
    f = field_by_id.get(link.custom_field_id)
    if f is not None:
      add_field("company", link.company_id, f)

  for link in project_field_links:
    f = field_by_id.get(link.custom_field_id)
    if f is not None:
      add_field("project", link.project_id, f)

  for link in user_field_links:
    f = field_by_id.get(link.custom_field_id)
    if f is not None:
      add_field("user", link.user_id, f)

  for link in object_field_links:
    f = field_by_id.get(link.custom_field_id)
    if f is not None:
      add_field("custom_object", link.custom_object_id, f)

  for values in field_values.values():
    values.sort(key=lambda item: (item["sort_order"] or 0, item["name"]))

  # ==========================================================
  # RELATIONSHIPS (identical indexing to build_company_graph)
  # ==========================================================

  relationships_by_entity: dict[tuple[str, UUID], list[CustomRelationship]] = defaultdict(list)
  valid_relationships: list[tuple[CustomRelationship, CustomRelationshipDefinition, tuple, tuple]] = []

  for relationship in relationships:
    definition = relationship_def_by_id.get(relationship.custom_relationship_definition_id)

    if definition is None:
      continue

    source_key = _entity_key(relationship.source_entity_type, relationship.source_entity_id)
    target_key = _entity_key(relationship.target_entity_type, relationship.target_entity_id)

    if source_key not in entity_by_key or target_key not in entity_by_key:
      continue

    relationships_by_entity[source_key].append(relationship)
    relationships_by_entity[target_key].append(relationship)
    valid_relationships.append((relationship, definition, source_key, target_key))

  # ==========================================================
  # JSON RENDER HELPERS
  # ==========================================================

  def render_custom_fields_json(entity_key: tuple[str, UUID]) -> list[dict[str, Any]]:
    result = []

    for field in field_values.get(entity_key, []):
      value = field["value"]
      is_empty = value is None or value == ""

      result.append(
        {
          "definition_id": field["definition_id"],
          "name": field["name"],
          "value": None if is_empty else _value(value),
          "is_empty": is_empty,
          "description": field["description"],
          "data_type": field["data_type"],
        }
      )

    return result

  def render_relationships_json(entity_key: tuple[str, UUID]) -> list[dict[str, Any]]:
    result = []

    for relationship in relationships_by_entity.get(entity_key, []):
      definition = relationship_def_by_id.get(relationship.custom_relationship_definition_id)

      source_key = _entity_key(relationship.source_entity_type, relationship.source_entity_id)
      target_key = _entity_key(relationship.target_entity_type, relationship.target_entity_id)

      if source_key not in short_ids or target_key not in short_ids:
        continue

      if entity_key == source_key:
        direction = "outgoing"
        other_key = target_key
      elif entity_key == target_key:
        direction = "incoming"
        other_key = source_key
      else:
        continue

      other_entity = entity_by_key[other_key]

      result.append(
        {
          "relationship_id": str(relationship.id),
          "definition_id": str(definition.id) if definition else None,
          "name": definition.name if definition else None,
          "direction": direction,
          "cardinality": _value(definition.cardinality) if definition else None,
          "description": definition.description if definition else None,
          "other_entity": {
            "id": str(other_key[1]),
            "short_id": short_ids[other_key],
            "entity_type": other_key[0],
            "label": _entity_label(other_key[0], other_entity, object_def_by_id),
          },
        }
      )

    return result

  def render_entity_json(entity_type: str, entity: Any) -> dict[str, Any]:
    key = _entity_key(entity_type, entity.id)
    normalized_type = _value(entity_type)

    data: dict[str, Any] = {
      "id": str(entity.id),
      "short_id": short_ids[key],
      "entity_type": normalized_type,
      "name": _entity_label(entity_type, entity, object_def_by_id),
    }

    if normalized_type == "company":
      data["corporate_number"] = getattr(entity, "corporate_number", None)

    elif normalized_type == "project":
      data["description"] = entity.description
      data["status"] = _value(entity.status) if entity.status else None
      data["company_id"] = str(entity.company_id)
      data["is_primary_project"] = project is not None and entity.id == project.id

    elif normalized_type == "user":
      data["first_name"] = entity.first_name
      data["last_name"] = entity.last_name
      data["email"] = entity.email
      data["role"] = _value(entity.role) if entity.role else None
      data["company_id"] = str(entity.company_id)

    elif normalized_type == "custom_object":
      definition = object_def_by_id.get(entity.custom_object_definition_id)
      data["object_type"] = definition.name if definition else None
      data["object_type_description"] = definition.description if definition else None
      data["company_id"] = str(entity.company_id)

    data["custom_fields"] = render_custom_fields_json(key)
    data["relationships"] = render_relationships_json(key)

    return data

  # ==========================================================
  # ASSEMBLE FINAL JSON STRUCTURE
  # ==========================================================

  return {
    "primary_project": (
      {
        "id": str(project.id),
        "short_id": short_ids[_entity_key("project", project.id)],
        "name": project.name,
        "description": project.description,
      }
      if project is not None
      else None
    ),
    "object_definitions": [
      {
        "id": str(d.id),
        "name": d.name,
        "description": d.description,
        "fields": [
          {
            "id": str(fd.id),
            "name": fd.name,
            "description": fd.description,
            "data_type": _value(fd.data_type),
          }
          for fd in sorted(
            [f for f in field_definitions if f.custom_object_definition_id == d.id],
            key=lambda f: (f.sort_order or 0, f.name),
          )
        ],
      }
      for d in object_definitions
    ],
    "field_definitions": [
      {
        "id": str(d.id),
        "name": d.name,
        "entity_type": _value(d.entity_type),
        "data_type": _value(d.data_type),
        "description": d.description,
        "custom_object_type": (
          object_def_by_id[d.custom_object_definition_id].name
          if d.custom_object_definition_id and d.custom_object_definition_id in object_def_by_id
          else None
        ),
      }
      for d in field_definitions
    ],
    "relationship_definitions": [
      {
        "id": str(d.id),
        "name": d.name,
        "source_entity_type": _value(d.source_entity_type),
        "source_custom_object_type": (
          object_def_by_id[d.source_custom_object_definition_id].name
          if d.source_custom_object_definition_id and d.source_custom_object_definition_id in object_def_by_id
          else None
        ),
        "target_entity_type": _value(d.target_entity_type),
        "target_custom_object_type": (
          object_def_by_id[d.target_custom_object_definition_id].name
          if d.target_custom_object_definition_id and d.target_custom_object_definition_id in object_def_by_id
          else None
        ),
        "cardinality": _value(d.cardinality),
        "description": d.description,
      }
      for d in relationship_definitions
    ],
    "company": render_entity_json("company", company),
    "projects": [render_entity_json("project", p) for p in projects],
    "users": [render_entity_json("user", u) for u in users],
    "custom_objects": [render_entity_json("custom_object", o) for o in custom_objects],
    "relationships": [
      {
        "relationship_id": str(relationship.id),
        "definition_id": str(definition.id),
        "name": definition.name,
        "cardinality": _value(definition.cardinality),
        "description": definition.description,
        "source": {
          "id": str(source_key[1]),
          "short_id": short_ids[source_key],
          "entity_type": source_key[0],
          "label": _entity_label(source_key[0], entity_by_key[source_key], object_def_by_id),
        },
        "target": {
          "id": str(target_key[1]),
          "short_id": short_ids[target_key],
          "entity_type": target_key[0],
          "label": _entity_label(target_key[0], entity_by_key[target_key], object_def_by_id),
        },
      }
      for relationship, definition, source_key, target_key in valid_relationships
    ],
  }