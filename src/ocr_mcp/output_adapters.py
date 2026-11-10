"""Model-card cleanup and output validation; raw generations remain intact."""

import json
import re
from typing import Any

from ._vendor.ovisocr3.document_crosspage import CATEGORIES, _clean_truncated_repeats, structured_to_markdown


def clean_markdown(raw: str, *, filter_imgtags: bool = False) -> str:
    text = raw.strip()
    if filter_imgtags:
        text = "\n\n".join(
            block for block in text.split("\n\n") if not block.strip().startswith('<img src="images/bbox_')
        )
    text = _clean_truncated_repeats(text)
    return re.sub(r"^```(?:markdown|html)?\s*\n|\n```\s*$", "", text, flags=re.I)


def structured_projection(raw: str) -> tuple[str, list, dict]:
    markdown, blocks, diagnostics = structured_to_markdown(_clean_truncated_repeats(raw.strip()))
    try:
        json.dumps(blocks, allow_nan=False)
    except ValueError:
        diagnostics.update(valid=False, json_complete=False, nonfinite_json=True)
        # Non-standard numeric constants cannot be returned as JSON data. Preserve
        # the source string and projected textual content, without repairing values.
        blocks = []
    return markdown, blocks, diagnostics


def valid_bbox(box: Any, *, allow_empty: bool = False) -> bool:
    if not isinstance(box, list) or len(box) != 4 or any(type(n) is not int or not 0 <= n < 1000 for n in box):
        return False
    return (box[0] <= box[2] and box[1] <= box[3]) if allow_empty else (box[0] < box[2] and box[1] < box[3])


def validate_records(data: list, task: str) -> None:
    for record in data:
        if not valid_bbox(
            record.get("bbox"), allow_empty=task in {"document_structured", "document_layout", "figure_captioning"}
        ):
            raise ValueError("Invalid normalized bbox")
        required = {
            "word_spotting": ["text"],
            "reference_grounding": ["reference"],
            "document_layout": ["category"],
        }.get(task, ["category", "content"])
        if any(not isinstance(record.get(key), str) for key in required):
            raise ValueError("Missing record fields")
        if "category" in required and record["category"] not in CATEGORIES:
            raise ValueError("Unknown category")
        if task == "word_spotting" and record["text"] != record["text"].strip():
            raise ValueError("Word spotting text must not have surrounding whitespace")


def _strings_only(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value) and all(_strings_only(item) for item in value.values())
    if isinstance(value, list):
        return bool(value) and all(_strings_only(item) for item in value)
    return isinstance(value, str)


def template_matches(value: Any, template: Any) -> bool:
    # The prompt uses empty strings for missing values, including missing substructures.
    if value == "":
        return True
    if isinstance(template, dict):
        return (
            isinstance(value, dict)
            and value.keys() == template.keys()
            and all(template_matches(value[key], expected) for key, expected in template.items())
        )
    if isinstance(template, list):
        return isinstance(value, list) and (not template or all(template_matches(item, template[0]) for item in value))
    return isinstance(value, str)


def validate_task_contract(result: dict, task: str, options: dict) -> dict:
    if not result.get("success"):
        return result
    data = result.get("data")
    try:
        if task == "chart_parsing":
            if set(data) != {"title", "source", "x_title", "y_title", "values"} or any(
                not isinstance(data[key], str) for key in ("title", "source", "x_title")
            ):
                raise ValueError("Invalid chart fields")
            y = data["y_title"]
            if not isinstance(y, str) and not (
                isinstance(y, list) and len(y) == 2 and all(isinstance(item, str) for item in y)
            ):
                raise ValueError("Invalid chart y_title")
            if not isinstance(data["values"], dict) or not _strings_only(data["values"]):
                raise ValueError("Chart data must contain string scalars")
        elif task == "position_aware_vqa":
            box = re.fullmatch(r"\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", str(data.get("bbox", "")))
            if (
                set(data) != {"answer", "bbox"}
                or not isinstance(data["answer"], str)
                or not box
                or not valid_bbox([int(n) for n in box.groups()])
            ):
                raise ValueError("Invalid position-aware answer")
        elif task == "reference_grounding":
            references = options["references"]
            if {record["reference"] for record in data} != set(references) or any(
                record["reference"] != record["reference"].strip() for record in data
            ):
                raise ValueError("References must exactly match the requested labels")
            order = {label: rank for rank, label in enumerate(dict.fromkeys(references))}
            emitted = [order[record["reference"]] for record in data]
            if emitted != sorted(emitted):
                raise ValueError("Grounding records must follow requested reference order")
        elif task == "figure_captioning":
            for record in data:
                if record["category"] in {"footer_image", "header_image", "image", "chart"}:
                    caption = re.search(r"<caption>(.*?)</caption>", record["content"], re.S)
                    if not caption or not caption[1].strip() or len(caption[1].split()) > 100:
                        raise ValueError("Visual regions need a caption of at most 100 words")
        elif task == "image_classification":
            if result["text"].strip() not in options["candidate_labels"]:
                raise ValueError("Classification label was not requested")
        elif task in {"kie_open", "kie_fields"}:
            if task == "kie_fields" and set(data) != set(options["keys"]):
                raise ValueError("Output must use exactly the requested fields")
            if any(
                not isinstance(value, str)
                and not (isinstance(value, list) and all(isinstance(item, str) for item in value))
                for value in data.values()
            ):
                raise ValueError("KIE values must preserve visible strings or repeated string arrays")
        elif task == "kie_schema" and not template_matches(data, options["template"]):
            raise ValueError("Output does not match the example template")
    except (ValueError, TypeError, KeyError, RecursionError):
        return {
            **result,
            "success": False,
            "error_code": "INVALID_TASK_CONTRACT",
            "error": "Task output violates its prompt contract",
            "contract_validated": False,
        }
    return {**result, "contract_validated": True}
