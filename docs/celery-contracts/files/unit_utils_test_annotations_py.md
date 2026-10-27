### `t/unit/utils/test_annotations.py`

Celery-specific: type-annotation helpers used to decide how task arguments are parsed/serialized (pydantic support). No queue behaviour. Portable guarantee: argument-type introspection must be predictable and never raise on odd input.

**None-type detection**
- Ask whether a value is the None type: None's type yes; an int instance, a string, the `int` class all no. Result is always a strict bool.
- Inside an Optional[int] annotation, the int part is not None-type and the None part is.

**Optional argument extraction**
- Parameter typed Optional[int], Union[int, None] or Union[None, int] (either order): the inner type (int) is returned.
- Same for `int | None` and `None | int` notation (Python 3.10+).
- Plain int, or unions of non-None types (str|int, str|int|bool): nothing returned (None); no guessing among multiple types.

**Subclass check on annotations**
- Plain class annotations (int, a pydantic model, list, dict) are correctly reported as subclasses of themselves/their base.
- Annotation that is not a real class (typing.List, typing.Dict, List[str], Dict[str, str]) checked against a base class: returns False, does not raise.
- Parameterised builtin generics (list[str], dict[str, str]) checked against list/dict/pydantic model: returns False, does not raise (3.9+).
