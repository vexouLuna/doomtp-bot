"""A custom command's Returns table, read from its body's endings (ADR-0037 G9)."""

from doomtp_bot.customcmds.resolution import returns


def test_a_body_ending_in_result_returns_its_fields() -> None:
    body = "weather {arg.1} | result c={_[celsius]} town=x{arg.1} -- {_[celsius]}°C"
    assert returns(body) == {"": "{c: any, town: str, message: str?}"}


def test_a_field_not_every_ending_returns_is_marked() -> None:
    body = "ifelse {arg.1 > 1} ( result a=1 b={arg.1} ) ( result a={arg.1} )"
    assert returns(body) == {"": "{a: any, b?: any, message: str?}"}
    # A missing else returns nothing, and an `||` fallback may return text: every field is "sometimes".
    assert returns("ifelse {arg.1 > 1} ( result a=1 )") == {"": "{a?: str, message?: str?}"}
    assert returns("result a=1 || echo no") == {"": "{a?: str, message?: str?}"}


def test_a_body_with_no_result_ending_has_no_table() -> None:
    assert returns("result a=1 | echo {_[a]}") == {}
    assert returns("echo (") == {}  # unparsable
