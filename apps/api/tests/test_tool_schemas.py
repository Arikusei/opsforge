"""Схемы инструментов и валидация аргументов."""

from app.agents.schemas import anthropic_tools, openai_tools, tool_catalog_text
from app.agents.tools import TOOLS, _validate_args


def test_every_tool_has_object_schema():
    for name, spec in TOOLS.items():
        params = spec["parameters"]
        assert params["type"] == "object", name
        assert params["properties"], name
        for field in params.get("required", []):
            assert field in params["properties"], f"{name}: required {field} нет в properties"
        for field, prop in params["properties"].items():
            assert prop.get("description"), f"{name}.{field} без описания"


def test_openai_schema_shape():
    tools = openai_tools()
    assert len(tools) == len(TOOLS)
    entry = next(t for t in tools if t["function"]["name"] == "create_lead")
    assert entry["type"] == "function"
    assert entry["function"]["parameters"]["required"] == ["company", "need"]


def test_anthropic_schema_shape():
    tools = anthropic_tools()
    assert len(tools) == len(TOOLS)
    entry = next(t for t in tools if t["name"] == "search_catalog")
    assert entry["input_schema"]["properties"]["query"]["type"] == "string"
    assert entry["description"]


def test_tool_catalog_text_lists_all_tools():
    text = tool_catalog_text()
    for name in TOOLS:
        assert name in text


def test_validate_args_accepts_valid_payload():
    schema = TOOLS["create_lead"]["parameters"]
    assert _validate_args(schema, {"company": "ООО Тест", "need": "СОЖ", "tags": "metalworking"}) == []


def test_validate_args_rejects_missing_and_blank_required():
    schema = TOOLS["create_lead"]["parameters"]
    assert _validate_args(schema, {"need": "СОЖ"}) == ["missing required field: company"]
    assert _validate_args(schema, {"company": "   ", "need": "СОЖ"}) == ["missing required field: company"]


def test_validate_args_rejects_unknown_field_and_bad_enum():
    schema = TOOLS["create_lead"]["parameters"]
    problems = _validate_args(schema, {"company": "ООО", "need": "СОЖ", "score": "0.9"})
    assert problems == ["unknown field: score"]

    problems = _validate_args(schema, {"company": "ООО", "need": "СОЖ", "source": "telegram"})
    assert len(problems) == 1 and "must be one of" in problems[0]


def test_validate_args_rejects_wrong_type():
    schema = TOOLS["search_catalog"]["parameters"]
    assert _validate_args(schema, {"query": 42}) == ["field query must be a string"]
