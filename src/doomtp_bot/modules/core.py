"""`core` module: sentinel commands (spec §8). Not toggleable, no cooldowns, logged at level `off`."""

from __future__ import annotations

from typing import Any

from doomtp_bot.runtime import formats, ops
from doomtp_bot.runtime.context import Args, CommandContext
from doomtp_bot.runtime.registry import Command, command
from doomtp_bot.runtime.result import Code, CommandError, Result
from doomtp_bot.runtime.spec import CommandSpec, Example, InputMode, LogLevel, Param
from doomtp_bot.runtime.values import Keyed, render

MODULE = "core"


def _spec(**kwargs: object) -> CommandSpec:
    """Sentinels (spec §8): never disabled, role everyone, no cooldowns, logged at level off."""
    return CommandSpec(
        module=MODULE, log_level=LogLevel.OFF, input=InputMode.OPTIONAL,
        toggleable=False, fixed_policy=True, **kwargs,  # type: ignore[arg-type]
    )  # fmt: skip


@command(
    _spec(
        name="true",
        data_schema={"": "the data of the result before it, unchanged"},
        summary="Always succeeds",
        description="Succeeds without a message and passes the previous data through. `{sign}x || true` makes x optional.",
        examples=(Example("{sign}shoutout @someone || true", "(nothing if the shoutout fails)"),),
    )
)
async def true_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    return Result.success(None, ctx.prev.data if ctx.prev is not None else None)


# `false` and `default` are written in the language: the `core` system pack (customcmds/system.py).


@command(
    _spec(
        name="fail",
        data_schema={"": "none"},
        summary="Fail with a code and message",
        params=(Param("1+", "message", description="Optional exit code (1–99) followed by a message"),),
        examples=(Example("{sign}check {arg.1:int ?? 0} <= 20 || fail 2 max 20 dice", "max 20 dice"),),
    )
)
async def fail_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    values = list(args.values)
    code: int = Code.FAIL
    if values and values[0].lstrip("+").isdigit() and 1 <= int(values[0]) <= 99:
        code = int(values.pop(0))
    message = " ".join(values) or None
    return Result.failure(code, message)


@command(
    _spec(
        name="echo",
        data_schema={"": "str: the text"},
        summary="Say something",
        params=(Param("1+", "text", description="Text to say"),),
        examples=(Example("{sign}random 1-100 | echo you rolled {_1}", "you rolled 42"),),
    )
)
async def echo_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    text = args.get("text", "")
    return Result.success(text, text)


def _said(value: Any) -> Result:
    return Result.success(render(value), value)


@command(
    _spec(
        name="check",
        data_schema={"": "any: the value of the expression"},
        summary="Test a condition",
        description=(
            "Succeeds if the expression is true, fails with code 1 if it is false. The data is its value. "
            "Falsy: false, 0, empty text, [] and {}. Text is read like an argument, so the text false is false."
        ),
        params=(Param("1+", "value", required=True, description="An expression, e.g. {channel.deaths} > 10"),),
        examples=(Example("{sign}check {channel.deaths ?? 0} > 10 && echo rough day", "rough day"),),
    )
)
async def check_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    value = args["value"]
    return Result(Code.OK if ops.holds(value) else Code.FAIL, None, value)


@command(
    _spec(
        name="calc",
        data_schema={"": "any: the value of the expression"},
        summary="Work out an expression",
        description="Says the value of an expression. A line like `{sign}1 + 3` runs calc.",
        params=(Param("1+", "value", required=True, description="An expression, e.g. (2 + 3) * 4"),),
        examples=(Example("{sign}calc (2 + 3) * 4", "20"),),
    )
)
async def calc_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    return _said(args["value"])


def _apply(symbol: str, texts: tuple[str, ...]) -> Any:
    values = [ops.literal(text) for text in texts]
    if len(values) == 1:
        return ops.apply_unary(symbol, values[0])
    left, right = values
    if symbol in ("and", "or"):
        return right if ops.truthy(left) == (symbol == "and") else left
    if symbol in ops.COMPARE_COMMANDS.values():
        return ops.compare(symbol, left, right)
    return ops.apply_binary(symbol, left, right)


def _operator(name: str, symbol: str, sample: tuple[str, ...] = ("7", "2")) -> Command:
    """`add 1 2` is `{1 + 2}` (ADR-0018 D8b). Each argument is read as a number, true/false, or text.

    Comparisons say true or false and succeed; `check` is what branches on a value.
    """
    params = (
        (Param("1", "left", required=True), Param("2", "right", required=True))
        if len(sample) == 2
        else (Param("1", "value", required=True),)
    )
    example = Example("{sign}" + " ".join((name, *sample)), render(_apply(symbol, sample)))

    async def handler(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
        return _said(_apply(symbol, args.values))

    handler.__name__ = f"{name}_cmd"
    spec = _spec(
        name=name,
        summary=f"The operator {symbol} as a command",
        params=params,
        examples=(example,),
        data_schema={"": "any: the value"},
    )

    return command(spec)(handler)


OPERATORS: tuple[Command, ...] = (
    *(_operator(name, symbol) for name, symbol in ops.BINARY_COMMANDS.items()),
    *(_operator(name, symbol) for name, symbol in ops.COMPARE_COMMANDS.items() if name != "in"),
    _operator("in", "in", ("b", "abc")),
    _operator("neg", "-", ("7",)),
    _operator("not", "not", ("false",)),
    _operator("and", "and", ("true", "yes")),
    _operator("or", "or", ("0", "none")),
)


@command(
    _spec(
        name="result",
        data_schema={"": "{<key>: any, message: str?}: the fields given, and the message"},
        summary="End a custom command with named fields and a message",
        description=(
            "Each key=value becomes a field; a value that is one placeholder keeps its type. The words after "
            "-- are the message; without them it is the message piped in. The message is also the field "
            "message, so give it after --, not as a key. A caller reads the fields as {_[key]}."
        ),
        params=(Param("1+", "fields", description="key=value … [-- message]"),),
        examples=(
            Example("{sign}result n=1 -- one", "one"),
            Example("{sign}weather Lisbon | result c={_[celsius]} -- {_[celsius]}°C", "21.5°C"),
        ),
    )
)
async def result_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    values = list(args.values)
    message: str | None = stdin.message if stdin is not None else None
    if "--" in values:
        at = values.index("--")
        message = " ".join(values[at + 1 :]) or None
        values = values[:at]
    fields: dict[str, Any] = {}
    for word in values:
        key, eq, text = word.partition("=")
        if not eq or not key.isidentifier():
            raise CommandError(f"usage: {ctx.channel.prefix}result key=value … [-- message] (not {word})")
        if key == "message":
            raise CommandError("the message goes after --, not in message=")
        fields[key] = word.value if isinstance(word, Keyed) else text
    return Result.success(message, {**fields, "message": message})


@command(
    _spec(
        name="format",
        data_schema={"": "str: the formatted text"},
        summary="Format a number or a time",
        description=(
            "time takes epoch seconds or ISO 8601 text, shown in the channel's timezone with a pattern of "
            "YYYY YY MMMM MMM MM DD D dddd ddd HH H hh mm ss A ([text] is literal; default YYYY-MM-DD HH:mm). "
            "ago says how long ago; duration spells out seconds in every unit; number and percent take "
            "decimals. The same formatters work inside a placeholder: {x:time(DD MMM)}, {x:ago}, {x:number(2)}, "
            "{x:percent(1)}, {x:ordinal}, and {x:human} for two units of a duration."
        ),
        params=(
            Param("1", "kind", required=True, type="choice", choices=formats.KINDS),
            Param("2", "value", required=True, type="any"),
            Param("3+", "option", description="time: a pattern; number and percent: decimals"),
        ),
        examples=(
            Example("{sign}format time {$now.unix} DD MMM HH:mm", "23 Oct 21:30"),
            Example("{sign}format duration 90061", "1d 1h 1m 1s"),
            Example("{sign}format number 1234567.891 2", "1,234,567.89"),
            Example("{sign}format ordinal 22", "22nd"),
        ),
    )
)
async def format_cmd(ctx: CommandContext, args: Args, stdin: Result | None) -> Result:
    text = formats.apply(args["kind"], args["value"], args.get("option"), ctx.channel.timezone, ctx.exec.now())
    return Result.success(text, text)


COMMANDS: tuple[Command, ...] = (
    true_cmd,
    fail_cmd,
    result_cmd,
    format_cmd,
    echo_cmd,
    check_cmd,
    calc_cmd,
    *OPERATORS,
)
