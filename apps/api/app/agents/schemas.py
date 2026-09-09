"""Конвертация реестра инструментов в форматы tool calling разных провайдеров."""

from __future__ import annotations

from app.agents.tools import TOOLS


def openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for name, spec in TOOLS.items()
    ]


def anthropic_tools() -> list[dict]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "input_schema": spec["parameters"],
        }
        for name, spec in TOOLS.items()
    ]


def tool_catalog_text() -> str:
    """Текстовое описание инструментов для JSON-протокола (фолбэк без native tools)."""
    lines = []
    for name, spec in TOOLS.items():
        required = ", ".join(spec["parameters"].get("required", [])) or "нет обязательных"
        lines.append(f"- {name}: {spec['description']} Обязательные аргументы: {required}.")
    return "\n".join(lines)
