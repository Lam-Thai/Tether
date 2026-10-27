"""`redact`: card numbers, US SSNs and security codes never reach storage, the model or UI."""

import pytest

from services.redaction import mask_phone, redact

CARD = "[card number removed]"
SSN = "[SSN removed]"
CODE = "[security code removed]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my card is 4111111111111111 thanks", f"my card is {CARD} thanks"),
        ("4111 1111 1111 1111", CARD),
        ("4111-1111-1111-1111.", f"{CARD}."),
        ("amex 3782 822463 10005 ok", f"amex {CARD} ok"),
        ("5500 0000 0000 0004", CARD),
        ("digits 4111111111111111, then 5500000000000004", f"digits {CARD}, then {CARD}"),
        ("visa 4222222222222 (13 digits)", f"visa {CARD} (13 digits)"),
        ("a 19 digit card 6304000000000000000 here", f"a 19 digit card {CARD} here"),
        ("on 2026-10-25 4111111111111111", f"on 2026-10-25 {CARD}"),
        ("4111.1111.1111.1111", CARD),
        ("4111  1111   1111 1111", CARD),
        ("4111 - 1111 - 1111 - 1111", CARD),
        ("4111 1111-1111.1111", CARD),
        ("+4111111111111111", f"+{CARD}"),
        ("card:+4111 1111 1111 1111", f"card:+{CARD}"),
    ],
    ids=[
        "plain",
        "spaces",
        "dashes",
        "amex-groups",
        "mastercard",
        "two-cards",
        "thirteen-digits",
        "nineteen-digits",
        "after-a-date",
        "dots",
        "runs-of-spaces",
        "spaced-dashes",
        "mixed-separators",
        "plus-prefixed",
        "plus-prefixed-groups",
    ],
)
def test_card_numbers_are_removed(text: str, expected: str):
    assert redact(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ssn 123-45-6789", f"ssn {SSN}"),
        ("it's 078-05-1120, ok?", f"it's {SSN}, ok?"),
    ],
    ids=["labelled", "inline"],
)
def test_ssn_is_removed(text: str, expected: str):
    assert redact(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CVV 123", CODE),
        ("cvv: 123", CODE),
        ("the CVC is 4567 thanks", f"the {CODE} thanks"),
        ("security code 987", CODE),
        ("Security Code: 9876.", f"{CODE}."),
        ("cvv2=123", CODE),
    ],
    ids=["cvv", "cvv-colon", "cvc-is", "security-code", "security-code-caps", "cvv2-equals"],
)
def test_security_codes_are_removed(text: str, expected: str):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "call me on 555-123-4567",
        "call +1 555 123 4567",
        "call +44 20 7946 0958",
        "call (555) 123-4567 please",
        "it costs $1,234.50 total",
        "the haircut is $45 and takes 45 minutes",
        "see you at 10:30 on 2026-10-25",
        "booking 1a2b3c4d is confirmed",
        "booking 1a2b3c4d-1111-2222-3333-444455556666",
        "zip 94107 and room 1234",
        "my security is fine, code is 12",
        "cvv is what exactly?",
        "123-45-678 is not an ssn",
        "number 4111111111111112 is not a valid card",
        "we have 20261025 103000 style stamps",
        "call +1 555 123 4567 or +1.555.123.4567",
        "version 10.2.3.4.5.6.7 released 2026.10.25",
        "ip 192.168.100.200 and 255.255.255.255",
        "",
    ],
    ids=[
        "phone-dashes",
        "phone-plus-1",
        "phone-international",
        "phone-parens",
        "price-thousands",
        "price-and-duration",
        "time-and-date",
        "booking-short-id",
        "booking-uuid",
        "zip-and-room",
        "short-code",
        "cvv-without-digits",
        "short-ssn",
        "luhn-invalid",
        "timestamp-like",
        "phones-with-plus",
        "version-string",
        "ip-addresses",
        "empty",
    ],
)
def test_ordinary_text_is_untouched(text: str):
    assert redact(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # Digit groups of these UUIDs once formed Luhn-valid 13-19 digit runs.
        "3d5e9993-2419-4881-9321-aed3fecaa5bb",
        "f7875740-1280-4202-a1ff-1d3f7da1f958",
        "40ea4328-4e15-4129-8437-931201f38bec",
        "booking 12345678-1234-1234-1234-123456789012 confirmed",
        "event a41111111111111111b",
    ],
    ids=["uuid-a", "uuid-b", "uuid-c", "all-digit-uuid", "inside-an-id"],
)
def test_identifiers_are_untouched(text: str):
    assert redact(text) == text


def test_random_uuids_are_never_redacted():
    import uuid

    for _ in range(20_000):
        value = str(uuid.uuid4())
        assert redact(value) == value


def test_redaction_is_idempotent():
    once = redact("card 4111 1111 1111 1111, ssn 123-45-6789, cvv 123")
    assert redact(once) == once


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("+15557654321", "•••4321"),
        ("(555) 765-4321", "•••4321"),
        ("+44 20 7946 0958", "•••0958"),
        ("Alex", "Alex"),
        ("12", "12"),
        ("Room 4321", "Room 4321"),
    ],
    ids=["e164", "us_format", "uk_spaced", "a_name", "too_short", "text_with_digits"],
)
def test_mask_phone_keeps_only_the_last_four_digits_of_a_number(value, masked):
    assert mask_phone(value) == masked
