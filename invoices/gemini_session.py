from __future__ import annotations

import re

from django.conf import settings


SESSION_KEY = "gemini_api_key"


class GeminiAccessError(RuntimeError):
    pass


def resolve_gemini_api_key(request=None) -> str:
    if request is not None:
        session_key = str(request.session.get(SESSION_KEY, "") or "").strip()
        if session_key:
            return session_key
    return str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()


def has_session_gemini_key(request) -> bool:
    return bool(str(request.session.get(SESSION_KEY, "") or "").strip())


def store_gemini_api_key(request, api_key: str) -> None:
    request.session[SESSION_KEY] = str(api_key or "").strip()


def clear_gemini_api_key(request) -> None:
    request.session.pop(SESSION_KEY, None)


def is_gemini_auth_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    patterns = [
        r"\b401\b",
        r"\b403\b",
        "unauthorized",
        "permission denied",
        "invalid api key",
        "api key",
        "forbidden",
        "permission",
        "authentication",
        "auth",
    ]
    return any(re.search(pattern, message) for pattern in patterns)


def _friendly_gemini_validation_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}".lower()
    if "api_key_invalid" in message or "api key not valid" in message or is_gemini_auth_error(exc):
        return "Chave do Gemini invalida. Passe uma chave valida."
    return "Nao foi possivel validar a chave do Gemini. Passe uma chave valida."


def validate_gemini_api_key(api_key: str, model: str) -> tuple[bool, str]:
    api_key = str(api_key or "").strip()
    if not api_key:
        return False, "Informe uma chave do Gemini."

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        client.models.generate_content(
            model=model,
            contents="Responda somente OK.",
            config={"max_output_tokens": 5, "temperature": 0},
        )
        return True, ""
    except Exception as exc:
        return False, _friendly_gemini_validation_error(exc)
