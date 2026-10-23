from core import keygen
from core.config import parse_token_keys


def test_keygen_prints_values_settings_accepts(capsys):
    keygen.main()
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())

    keys, active = parse_token_keys(lines["CALENDAR_TOKEN_KEYS"])
    assert active == "k1" and len(keys["k1"]) == 32
    assert len(lines["OAUTH_STATE_SECRET"]) >= 32
