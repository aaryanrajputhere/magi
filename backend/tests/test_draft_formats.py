import pytest

from app.api.campaigns import FORMAT_GUIDANCE, draft_output_model


@pytest.mark.parametrize("format_name", ["email", "blog", "landing page", "x", "linkedin"])
def test_shared_format_instructions(format_name):
    assert format_name in FORMAT_GUIDANCE.lower()


@pytest.mark.parametrize("evidence", [[], [{"chunk_id": "chunk"}]])
def test_draft_schema_accepts_strict_output_and_nullable_metadata(evidence):
    from app.llm.client import strict_output_schema

    model = draft_output_model(evidence)
    schema = strict_output_schema(model)["format"]["schema"]
    assert set(schema["required"]) == set(schema["properties"])
    draft = model(title="Title", subject=None, body="Body", source_ids=[],
                  preheader=None, sender_name=None, byline=None)
    assert draft.preheader is draft.sender_name is draft.byline is None


def test_draft_metadata():
    model = draft_output_model([{"chunk_id": "chunk"}])
    draft = model(title="Email", subject="Subject", body="Copy", source_ids=["chunk"],
                  preheader="Preview", sender_name="Sender", byline=None)
    assert draft.model_dump()["preheader"] == "Preview"
