"""Twilio webhook signature (`X-Twilio-Signature`).

data = full URL + each POST param name followed by its value, sorted by name;
signature = base64(HMAC-SHA1(auth_token, data)). Compared in constant time.

A parameter that appears more than once contributes ALL its values; the values of one
name are sorted (what twilio-python's `RequestValidator` does for multi-valued params),
so the order they arrived in does not matter. `params` is a mapping or a sequence of
(name, value) pairs; pass the pairs when the body may repeat a name.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping, Sequence

Params = Mapping[str, str] | Sequence[tuple[str, str]]


def compute_signature(auth_token: str, url: str, params: Params) -> str:
    pairs = list(params.items()) if isinstance(params, Mapping) else list(params)
    data = url + "".join(f"{name}{value}" for name, value in sorted(pairs))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def is_valid_signature(auth_token: str, url: str, params: Params, signature: str | None) -> bool:
    if not signature:
        return False
    expected = compute_signature(auth_token, url, params)
    return hmac.compare_digest(expected.encode(), signature.encode())
