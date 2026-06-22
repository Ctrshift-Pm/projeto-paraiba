from __future__ import annotations

import os
import re

from django.conf import settings


SESSION_KEY = "docextract_logged_in"
LEGACY_GEMINI_SESSION_KEY = "gemini_api_key"
ADMIN_USERNAME = os.getenv("DOCEXTRACT_ADMIN_USER", "admin").strip()
ADMIN_PASSWORD = os.getenv("DOCEXTRACT_ADMIN_PASSWORD", "admin")


class GeminiAccessError(RuntimeError):
    pass


def resolve_gemini_api_key(request=None) -> str:
    if request is not None:
        session_key = str(request.session.get(LEGACY_GEMINI_SESSION_KEY, "") or "").strip()
        if session_key:
            return session_key
        return ""
    return str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()


def has_session_gemini_key(request) -> bool:
    return bool(str(request.session.get(LEGACY_GEMINI_SESSION_KEY, "") or "").strip())


def store_gemini_api_key(request, api_key: str) -> None:
    request.session[LEGACY_GEMINI_SESSION_KEY] = str(api_key or "").strip()


def clear_gemini_api_key(request) -> None:
    request.session.pop(LEGACY_GEMINI_SESSION_KEY, None)


def has_session_login(request) -> bool:
    return bool(request.session.get(SESSION_KEY))


def store_session_login(request) -> None:
    request.session[SESSION_KEY] = True


def clear_session_login(request) -> None:
    request.session.pop(SESSION_KEY, None)


def validate_session_login(username: str, password: str) -> tuple[bool, str]:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        return False, "Informe usuário e senha."
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return False, "Usuário ou senha inválidos."
    return True, ""


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
        message = f"{type(exc).__name__}: {exc}".lower()
        if "api_key_invalid" in message or "api key not valid" in message or is_gemini_auth_error(exc):
            return False, "Chave do Gemini inválida. Passe uma chave válida."
        return False, "Não foi possível validar a chave do Gemini. Passe uma chave válida."


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
