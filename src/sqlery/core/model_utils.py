"""Utilities for creating unified Pydantic/Django models.

Inspired by the pattern of defining models once and generating both
Pydantic (SQLModel) and Django ORM models from the same definition.
"""

import inspect
from typing import Any, TypeVar, get_args, get_origin, Union
from datetime import datetime, date, time

from pydantic import BaseModel, EmailStr, create_model
from pydantic_core import PydanticUndefined

try:
    from django.db import models as django_models
except ImportError:
    django_models = None

T = TypeVar('T', bound=BaseModel)
FieldDefinition = tuple[Any, Any]


def resolve_ftype(ftype):
    """Resolve field type, unwrapping Optional/Union types."""
    origin = get_origin(ftype)
    if origin is Union:
        args = get_args(ftype)
        if len(args) == 2 and type(None) in args:
            return next(arg for arg in args if arg is not type(None))
        return args[0]
    elif origin:
        if origin is dict:
            return dict
        args = get_args(ftype)
        return args[0] if args else origin
    return ftype


def pydantic_to_django_model(
    name: str,
    schema: BaseModel,
    app_label: str | None = None,
    table_name: str | None = None,
    indexes: list | None = None,
):
    """Convert a Pydantic model to a Django model.

    This allows defining models once in Pydantic and generating Django models.

    Args:
        name: Django model class name
        schema: Pydantic model schema
        app_label: Django app label
        table_name: Database table name
        indexes: List of Django Index objects

    Returns:
        Django Model class
    """
    # from django.db import models  # moved to top-level (try/except as django_models)
    models = django_models

    # Get Pydantic fields
    if hasattr(schema, "model_fields"):
        p_fields = schema.model_fields
    else:
        p_fields = getattr(schema, "__fields__", {})

    # Initialize attrs and extract config
    attrs = {"__module__": schema.__module__}
    extra: dict[str, Any] = {}

    # Extract config from model_config
    config = getattr(schema, "model_config", None)
    if config:
        if isinstance(config, dict):
            extra = config.get("json_schema_extra", {}) or {}
        else:
            extra = getattr(config, "json_schema_extra", {}) or {}

        if table_name is None:
            table_name = extra.get("db_table")

    conf_indexes = extra.get("indexes", [])
    final_indexes = indexes if indexes is not None else conf_indexes

    # Derive app_label if not provided
    if app_label is None:
        app_label = schema.__module__.split(".")[0]

    # Build Meta class
    class Meta:
        pass

    Meta.app_label = app_label
    if table_name:
        Meta.db_table = table_name
    if final_indexes:
        Meta.indexes = final_indexes

    attrs["Meta"] = Meta

    # Map each Pydantic field to Django field
    for fname, p_field in p_fields.items():
        attrs[fname] = map_pydantic_to_django_field(fname, p_field)

    # Add user-defined methods
    for key, value in schema.__class__.__dict__.items():
        if callable(value) and not key.startswith("_"):
            if value.__qualname__.startswith(schema.__name__ + "."):
                attrs[key] = value

    return type(name, (models.Model,), attrs)


def map_pydantic_to_django_field(name: str, p_field):
    """Map a Pydantic field to a Django field.

    Args:
        name: Field name
        p_field: Pydantic FieldInfo

    Returns:
        Django Field instance
    """
    # from django.db import models  # moved to top-level (try/except as django_models)
    models = django_models

    # Handle primary key
    if name == 'id':
        return models.AutoField(primary_key=True)

    # Detect Pydantic version
    is_v2 = not hasattr(p_field, 'outer_type_')

    # Extract metadata
    schema_extra = {}
    if not is_v2:  # v1
        field_info = getattr(p_field, 'field_info', None)
        schema_extra = getattr(field_info, 'extra', {})
    else:  # v2
        if p_field.json_schema_extra:
            schema_extra.update(p_field.json_schema_extra)

    # Check for custom Django flags
    if isinstance(schema_extra, dict):
        if schema_extra.get('django_auto_now_add') is True:
            return models.DateTimeField(auto_now_add=True, editable=False, help_text=p_field.description or None)
        if schema_extra.get('django_auto_now') is True:
            return models.DateTimeField(auto_now=True, editable=False, help_text=p_field.description or None)

    # Extract field properties
    if not is_v2:  # v1
        ftype = p_field.outer_type_
        is_optional = get_origin(ftype) is Union and type(None) in get_args(ftype)
        required = p_field.required
        default = p_field.default if p_field.default is not None else PydanticUndefined
        field_info = getattr(p_field, 'field_info', None)
        desc = field_info.description if field_info else None
        min_len = getattr(field_info, 'min_length', None)
        max_len = getattr(field_info, 'max_length', None)
    else:  # v2
        ftype = p_field.annotation
        required = p_field.is_required()
        origin = get_origin(ftype)
        is_explicit_optional = origin is Union and type(None) in get_args(ftype)
        is_optional = (not required and p_field.get_default() is not PydanticUndefined) or is_explicit_optional

        default = p_field.get_default() if not required else PydanticUndefined
        desc = p_field.description

        min_len = max_len = None
        if p_field.metadata:
            for meta in p_field.metadata:
                if hasattr(meta, 'min_length'):
                    min_len = meta.min_length
                if hasattr(meta, 'max_length'):
                    max_len = meta.max_length

    allow_null = is_optional

    kwargs = {
        'null': allow_null,
        'help_text': desc,
    }

    if default is not PydanticUndefined:
        kwargs['default'] = default

    if allow_null:
        kwargs['blank'] = True
    elif allow_null and 'default' not in kwargs:
        kwargs['default'] = None

    resolved_ftype = resolve_ftype(ftype)

    # Handle boolean fields
    if resolved_ftype is bool and 'default' not in kwargs:
        kwargs['default'] = False

    # Field type mapping
    DJANGO_FIELD_MAP = [
        (EmailStr, lambda k, _: models.EmailField(max_length=254, **k)),
        (str, lambda k, ml: models.CharField(max_length=ml, **k) if ml else models.TextField(**k)),
        (bool, lambda k, _: models.BooleanField(**k)),
        (int, lambda k, _: models.IntegerField(**k)),
        (float, lambda k, _: models.FloatField(**k)),
        ((datetime, date, time), lambda k, _: models.DateTimeField(**k)),
        (dict, lambda k, _: models.JSONField(**k)),
        (list, lambda k, _: models.JSONField(**k)),
    ]

    # Handle long text fields
    long_text_threshold = 255
    if resolved_ftype is str and (max_len is None or max_len > long_text_threshold):
        return models.TextField(**kwargs)

    # Map field type to Django field
    for base, builder in DJANGO_FIELD_MAP:
        if isinstance(resolved_ftype, type):
            if isinstance(base, tuple):
                if any(issubclass(resolved_ftype, b) for b in base):
                    return builder(kwargs, max_len)
            elif issubclass(resolved_ftype, base):
                return builder(kwargs, max_len)
        elif resolved_ftype is base:
            return builder(kwargs, max_len)

    # Handle list and dict types
    if resolved_ftype is list or get_origin(resolved_ftype) is list or resolved_ftype is dict:
        return models.JSONField(**kwargs)

    # Handle custom Pydantic models
    if isinstance(resolved_ftype, type) and issubclass(resolved_ftype, BaseModel):
        return models.JSONField(**kwargs)

    raise TypeError(f"Unsupported type for field {name}: {resolved_ftype} (original: {ftype})")


def extract_submodel(
    base_model: type[T],
    fields: list[str],
    additional_fields: dict[str, FieldDefinition] | None = None,
    model_name: str | None = None,
    include_defaults: bool = True,
) -> type[BaseModel]:
    """Extract a submodel with only specified fields.

    Useful for creating request/response models for API endpoints.

    Args:
        base_model: Base Pydantic model
        fields: List of field names to include
        additional_fields: Additional fields to add
        model_name: Name for the new model
        include_defaults: Whether to include default values

    Returns:
        New Pydantic model with specified fields
    """
    if not inspect.isclass(base_model) or not issubclass(base_model, BaseModel):
        raise TypeError("Base model must be a Pydantic model class")

    type_hints = base_model.__annotations__
    field_definitions = base_model.model_fields
    new_fields: dict[str, FieldDefinition] = {}

    for field_name in fields:
        if field_name not in type_hints:
            raise ValueError(f"Field '{field_name}' not found in base model")

        field_type = type_hints[field_name]
        field_info = field_definitions.get(field_name)

        if field_info and include_defaults and field_info.default is not ...:
            new_fields[field_name] = (field_type, field_info.default)
        else:
            new_fields[field_name] = (field_type, ...)

    if additional_fields:
        new_fields.update(additional_fields)

    model_name = model_name or f"{base_model.__name__}Sub"

    new_model = create_model(model_name, **new_fields, __module__=base_model.__module__)

    if base_model.__doc__:
        new_model.__doc__ = f"Submodel of {base_model.__name__}: {base_model.__doc__}"

    return new_model


def create_endpoint_model(
    base_model: type[T],
    endpoint_name: str,
    fields: list[str],
    additional_fields: dict[str, FieldDefinition] | None = None,
    description: str | None = None,
) -> type[BaseModel]:
    """Create a Pydantic model for an API endpoint.

    Args:
        base_model: Base Pydantic model
        endpoint_name: Name of the endpoint (e.g., "Create", "Update")
        fields: List of field names to include
        additional_fields: Additional fields to add
        description: Description for the model

    Returns:
        New Pydantic model for the endpoint
    """
    model_name = f"{base_model.__name__}{endpoint_name}"

    model = extract_submodel(
        base_model=base_model,
        fields=fields,
        additional_fields=additional_fields,
        model_name=model_name,
        include_defaults=True
    )

    if description:
        model.__doc__ = description

    return model


def create_response_model(
    base_model: type[T],
    fields: list[str],
    name_suffix: str = "Response",
    additional_fields: dict[str, FieldDefinition] | None = None,
) -> type[BaseModel]:
    """Create a Pydantic model for API responses.

    Args:
        base_model: Base Pydantic model
        fields: List of field names to include
        name_suffix: Suffix for the model name
        additional_fields: Additional fields to add

    Returns:
        New Pydantic model for responses
    """
    model_name = f"{base_model.__name__}{name_suffix}"

    return extract_submodel(
        base_model=base_model,
        fields=fields,
        additional_fields=additional_fields,
        model_name=model_name
    )
