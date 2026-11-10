"""Pinned OvisOCR3 task contracts. No runtime downloads or local inference."""

import json
from importlib.resources import files
from typing import Any

from .output_adapters import clean_markdown, structured_projection, validate_records

TASK_FORMATS = {
    "document_markdown": "markdown",
    "document_structured": "json",
    "document_layout": "json",
    "figure_captioning": "json",
    "plain_ocr": "text",
    "word_spotting": "json",
    "reference_grounding": "json",
    "region_ocr": "text",
    "formula_recognition": "latex",
    "chart_parsing": "json",
    "text_translation": "text",
    "image_classification": "text",
    "visual_question_answering": "text",
    "position_aware_vqa": "json",
    "kie_open": "json",
    "kie_fields": "json",
    "kie_schema": "json",
    "crosspage_table": "json",
}


def prompt_text(task: str) -> str:
    if task not in TASK_FORMATS:
        raise ValueError("Unknown task")
    return files("ocr_mcp").joinpath("prompts", f"{task}.txt").read_text(encoding="utf-8")


def task_prompt(
    task: str,
    *,
    question: str = "",
    references: list[str] | None = None,
    target_region: list[int] | None = None,
    target_language: str = "",
    candidate_labels: list[str] | None = None,
    target_description: str = "the main subject",
    keys: list[str] | None = None,
    template: dict | None = None,
) -> str:
    prompt = prompt_text(task)
    if task != "position_aware_vqa":
        prompt = prompt.strip()
    for values in (references, candidate_labels, keys):
        if values is not None and (
            not values or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ValueError("Task labels and fields must be nonempty strings")
    if task in {"visual_question_answering", "position_aware_vqa"} and not question.strip():
        raise ValueError("This task requires question")
    if task == "reference_grounding" and not references:
        raise ValueError("This task requires references")
    if task == "image_classification" and not candidate_labels:
        raise ValueError("This task requires candidate_labels")
    if task == "text_translation" and not target_language.strip():
        raise ValueError("This task requires target_language")
    if task == "kie_fields" and not keys:
        raise ValueError("Specify at least one field")
    if task == "kie_schema" and not template:
        raise ValueError("Specify a JSON output template, not JSON Schema")
    if task in {"region_ocr", "text_translation"}:
        target_region = target_region if target_region is not None else [0, 0, 999, 999]
        if len(target_region) != 4 or any(type(n) is not int or not 0 <= n < 1000 for n in target_region):
            raise ValueError("target_region must be normalized [left, top, right, bottom] integers in [0,999]")
        if target_region[0] >= target_region[2] or target_region[1] >= target_region[3]:
            raise ValueError("target_region must have positive width and height")
    substitutions = {
        "{question}": question,
        "{references}": json.dumps(references, ensure_ascii=False, separators=(",", ":")),
        "{target_region}": json.dumps(target_region, separators=(",", ":")),
        "{target_language}": target_language,
        "{candidate_labels}": json.dumps(candidate_labels, ensure_ascii=False),
        "{target description}": target_description,
        "{keys_json}": json.dumps(keys, ensure_ascii=False),
        "{schema_json}": json.dumps(template, ensure_ascii=False),
    }
    # One pass prevents placeholders in user values from being substituted again.
    import re

    rendered = re.sub(
        r"\{(?:question|references|target_region|target_language|candidate_labels|target description|keys_json|schema_json)\}",
        lambda match: substitutions[match[0]],
        prompt,
    )
    return re.sub(r"\n{3,}", "\n\n", rendered).strip() if task == "visual_question_answering" else rendered


def parse_task_result(result: dict[str, Any], task: str) -> dict[str, Any]:
    if not result.get("success"):
        return result
    raw = result["text"]
    result = {**result, "task": task, "raw_output": raw}
    if task == "document_markdown":
        cleaned = clean_markdown(raw)
        return {**result, "raw_markdown": raw, "text": cleaned, "cleanup_applied": cleaned != raw}
    if task == "document_structured":
        markdown, blocks, diagnostics = structured_projection(raw)
        result.update(data=blocks, projected_markdown=markdown, parse_diagnostics=diagnostics)
        if not diagnostics["valid"]:
            return {
                **result,
                "success": False,
                "partial_success": bool(markdown),
                "error_code": "INVALID_TASK_JSON",
                "error": "Structured output is incomplete or has invalid fields; complete prefix content is retained",
                "json_validated": diagnostics["json_complete"],
            }
    if TASK_FORMATS[task] != "json":
        return result
    try:

        def reject_constant(value):
            raise ValueError("Non-finite JSON number")

        data = result["data"] if task == "document_structured" else json.loads(raw, parse_constant=reject_constant)
        expected = (
            dict if task in {"chart_parsing", "position_aware_vqa", "kie_open", "kie_fields", "kie_schema"} else list
        )
        if not isinstance(data, expected):
            raise ValueError("Unexpected top-level JSON type")
        if isinstance(data, list) and any(not isinstance(record, dict) for record in data):
            raise ValueError("Expected JSON objects in list")
        if task in {
            "document_structured",
            "document_layout",
            "figure_captioning",
            "word_spotting",
            "reference_grounding",
        }:
            validate_records(data, task)
        return {**result, "data": data, "json_validated": True}
    except (ValueError, KeyError, TypeError, RecursionError):
        return {
            **result,
            "success": False,
            "error_code": "INVALID_TASK_JSON",
            "error": "Task output does not match the expected JSON structure",
            "json_validated": False,
        }
