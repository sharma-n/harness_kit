"""YAML loader replicating llm_kit's config pattern.

Mirrors the shape of ``llm_kit/config/app.py`` (``${VAR}`` / ``${VAR:-default}``
interpolation + recursive dataclass construction) rather than importing its
private helpers, so agent_kit stays decoupled from llm_kit internals. The nested
``llm_kit`` block is delegated to ``AppConfig.from_dict`` — the one place we hand
config back to llm_kit.
"""

from __future__ import annotations

import os
import re
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

import yaml
from llm_kit import AppConfig

T = TypeVar("T")

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def load_yaml(cls: type[T], path: str | Path) -> T:
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Top-level YAML at {path} must be a mapping")
    return load_dict(cls, data)


def load_dict(cls: type[T], data: dict[str, Any]) -> T:
    return _build_dataclass(cls, _interpolate_env(data))


def _interpolate_env(value: Any) -> Any:
    """Recursively replace ${VAR} / ${VAR:-default} inside string values."""
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(_replace_env_match, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def _replace_env_match(match: re.Match[str]) -> str:
    var, default = match.group(1), match.group(2)
    resolved = os.environ.get(var)
    if resolved is not None:
        return resolved
    if default is not None:
        return default
    raise KeyError(f"Environment variable {var!r} referenced in config but not set")


def _build_dataclass(cls: type[T], data: dict[str, Any]) -> T:
    if not is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown config key {key!r} for {cls.__name__}")
        kwargs[key] = _coerce(hints[key], value)
    return cls(**kwargs)  # type: ignore[call-arg]


def _coerce(annotation: Any, value: Any) -> Any:
    if value is None:
        return None

    # The nested llm_kit block is owned by llm_kit; hand it back wholesale.
    if annotation is AppConfig and isinstance(value, dict):
        return AppConfig.from_dict(value)

    origin = get_origin(annotation)

    if is_dataclass(annotation) and isinstance(value, dict):
        return _build_dataclass(annotation, value)

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)

    if origin is list:
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        return [_coerce(item_type, v) for v in value]

    if origin is dict:
        args = get_args(annotation)
        v_type = args[1] if len(args) == 2 else Any
        return {k: _coerce(v_type, v) for k, v in value.items()}

    # Union (e.g. ``str | None``): try the first non-None arm that accepts it.
    if origin is not None and get_args(annotation):
        for arm in get_args(annotation):
            if arm is type(None):
                continue
            try:
                return _coerce(arm, value)
            except (TypeError, ValueError):
                continue
        return value

    return value
