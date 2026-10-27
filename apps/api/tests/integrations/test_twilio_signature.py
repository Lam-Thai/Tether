"""Twilio's X-Twilio-Signature algorithm, against the vector in Twilio's docs."""

from integrations.twilio.signature import compute_signature, is_valid_signature

TOKEN = "12345"
URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
# Twilio's "Validating requests" docs example and its published signature.
DOCS_PARAMS = {
    "CallSid": "CA1234567890ABCDE",
    "Caller": "+12349013030",
    "Digits": "1234",
    "From": "+12349013030",
    "To": "+18005551212",
}
DOCS_EXPECTED = "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
# The same request with other phone numbers (the shape twilio-python's tests use).
PARAMS = {**DOCS_PARAMS, "Caller": "+14158675310", "From": "+14158675310"}
EXPECTED = "GvWf1cFY/Q7PnoempGyD5oXAezc="


def test_matches_the_documented_vector():
    assert compute_signature(TOKEN, URL, DOCS_PARAMS) == DOCS_EXPECTED
    assert is_valid_signature(TOKEN, URL, DOCS_PARAMS, DOCS_EXPECTED)
    assert compute_signature(TOKEN, URL, PARAMS) == EXPECTED


def test_param_order_does_not_matter():
    assert compute_signature(TOKEN, URL, dict(reversed(PARAMS.items()))) == EXPECTED


def test_any_change_invalidates():
    assert not is_valid_signature("other", URL, PARAMS, EXPECTED)
    assert not is_valid_signature(TOKEN, URL + "x", PARAMS, EXPECTED)
    assert not is_valid_signature(TOKEN, URL, {**PARAMS, "Digits": "9999"}, EXPECTED)
    assert not is_valid_signature(TOKEN, URL, PARAMS, "GvWf1cFY/Q7PnoempGyD5oXAezc")


def test_missing_or_empty_header_is_invalid():
    assert not is_valid_signature(TOKEN, URL, PARAMS, None)
    assert not is_valid_signature(TOKEN, URL, PARAMS, "")


def test_repeated_names_contribute_every_value_in_sorted_order():
    pairs = [("To", "+1"), ("Tag", "b"), ("Tag", "a")]
    expected = compute_signature(TOKEN, URL, [("Tag", "a"), ("Tag", "b"), ("To", "+1")])
    assert compute_signature(TOKEN, URL, pairs) == expected
    assert compute_signature(TOKEN, URL, list(reversed(pairs))) == expected
    sig = compute_signature(TOKEN, URL, pairs)
    assert is_valid_signature(TOKEN, URL, pairs, sig)
    assert not is_valid_signature(TOKEN, URL, [("To", "+1"), ("Tag", "a")], sig)
