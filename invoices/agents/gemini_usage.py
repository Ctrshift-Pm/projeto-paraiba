from __future__ import annotations

import json


def response_usage_metadata(response) -> object:
    for name in ("usage_metadata", "usageMetadata"):
        usage = getattr(response, name, None)
        if usage is not None:
            return usage
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        if isinstance(dumped, dict):
            return dumped.get("usage_metadata") or dumped.get("usageMetadata")
    return None


def usage_payload(usage_metadata: object) -> dict:
    if usage_metadata is None:
        return {}
    if hasattr(usage_metadata, "model_dump"):
        dumped = usage_metadata.model_dump()
        if isinstance(dumped, dict):
            usage_metadata = dumped

    input_tokens = metadata_value(usage_metadata, "prompt_token_count", "promptTokenCount")
    output_tokens = metadata_value(usage_metadata, "candidates_token_count", "candidatesTokenCount")
    total_tokens = metadata_value(usage_metadata, "total_token_count", "totalTokenCount")
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated": False,
    }


def estimated_usage_payload(prompt: str, data: dict) -> dict:
    output = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    input_tokens = estimate_tokens(prompt)
    output_tokens = estimate_tokens(output)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": True,
    }


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text or "") / 4))


def metadata_value(value: object, *names: str) -> int:
    raw = 0
    for name in names:
        if isinstance(value, dict):
            raw = value.get(name, 0)
        else:
            raw = getattr(value, name, 0)
        if raw:
            break
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0
