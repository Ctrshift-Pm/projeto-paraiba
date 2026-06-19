from __future__ import annotations

import re
from datetime import date


def only_digits(value: object) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def only_alnum(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).upper()


def _cnpj_char_value(value: str) -> int:
    return ord(value.upper()) - 48


def is_valid_cpf(value: object) -> bool:
    digits = only_digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    first = sum(int(digits[index]) * (10 - index) for index in range(9))
    first_digit = 0 if first % 11 < 2 else 11 - (first % 11)
    second = sum(int(digits[index]) * (11 - index) for index in range(10))
    second_digit = 0 if second % 11 < 2 else 11 - (second % 11)
    return digits[-2:] == f"{first_digit}{second_digit}"


def is_valid_cnpj(value: object) -> bool:
    cleaned = only_alnum(value)
    if len(cleaned) != 14 or len(set(cleaned)) == 1:
        return False

    weights_first = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_second = [6, *weights_first]
    first = sum(_cnpj_char_value(char) * weight for char, weight in zip(cleaned[:12], weights_first))
    first_digit = 0 if first % 11 < 2 else 11 - (first % 11)
    second = sum(_cnpj_char_value(char) * weight for char, weight in zip(cleaned[:12], weights_second[:12]))
    second += first_digit * weights_second[12]
    second_digit = 0 if second % 11 < 2 else 11 - (second % 11)
    return cleaned[-2:] == f"{first_digit}{second_digit}"


def mask_cpf(value: object) -> str:
    digits = only_digits(value)
    if len(digits) != 11:
        return digits
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def mask_cnpj(value: object) -> str:
    cleaned = only_alnum(value)
    if len(cleaned) != 14:
        return cleaned
    return f"{cleaned[:2]}.{cleaned[2:5]}.{cleaned[5:8]}/{cleaned[8:12]}-{cleaned[12:]}"


def mask_cep(value: object) -> str:
    digits = only_digits(value)
    if len(digits) != 8:
        return digits
    return f"{digits[:5]}-{digits[5:]}"


def mask_ie(value: object) -> str:
    digits = only_digits(value)
    if not digits:
        return ""
    return ".".join(digits[index : index + 3] for index in range(0, len(digits), 3))


def mask_phone(value: object) -> str:
    digits = only_digits(value)
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return digits


def due_status(due_date: date) -> str:
    if due_date < date.today():
        return "VENCIDA"
    if due_date > date.today():
        return "A_VENCER"
    return "ABERTA"


def display_document_name(*, supplier_name: str, number: str, issue_date: date | str) -> str:
    supplier = " ".join(str(supplier_name or "DOCUMENTO").upper().split())
    doc_number = str(number or "SEM-NUMERO").strip()
    date_text = issue_date.isoformat() if isinstance(issue_date, date) else str(issue_date or "").strip()
    return f"{supplier} - NF {doc_number} - {date_text}".strip()
