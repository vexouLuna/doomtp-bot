"""The pack bodies rewritten with syntax 2.1's forms (ADR-0037) do what the 2.0 bodies did.

`OLD` keeps each body as it was before the rewrite. A rewrite that only flattens nested `ifelse` into
the multi-branch form parses to the same tree, so the first test compares trees. The `switch` rewrites
don't, so the second test runs the same inputs against the old bodies in one channel and the new ones in
another, and compares every reply, code and stored variable.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from doomtp_bot.customcmds.service import CustomCommandService
from doomtp_bot.lang.ast import invocations, to_canonical
from doomtp_bot.lang.parser import Context
from doomtp_bot.policy.roles import GLOBAL
from scripts.starter_pack import PYRAMID, QUOTES
from tests.customcmds.test_customcmds import BADGES, CHANNEL_ID, CHANNEL_LOGIN, USERS
from tests.customcmds.test_packs import OTHER_CHANNEL, Harness, h  # noqa: F401
from tests.test_starter_pack import OWNER, _install

OLD = {
    "quote": 'ifelse {(arg.1 ?? "-") == "add"} ( quote_add {arg.2+raw} ) ( ifelse {(arg.1 ?? "-") == "del" or (arg.1 ?? "-") == "delete"} ( quote_del {arg.2} ) ( ifelse {(arg.1 ?? "-") == "-"} ( quote_random ) ( quote_show {arg.1} ) ) )',  # noqa: E501
    "quote_show": 'ifelse {(publisher.channel.quotes[arg.number] ?? 0) != 0} ( ifelse {"game" in publisher.channel.quotes[arg.number]} ( echo #{arg.number}: {publisher.channel.quotes[arg.number][text]} [{publisher.channel.quotes[arg.number][game]}, {publisher.channel.quotes[arg.number][date]}] ) ( echo #{arg.number}: {publisher.channel.quotes[arg.number][text]} [{publisher.channel.quotes[arg.number][date]}] ) ) ( fail 3 there is no quote #{arg.number} )',  # noqa: E501
    "pyramid": 'ifelse {(arg.1 ?? "-") == "-" or (arg.1 ?? "-") == "stats"} ( pyramid_stats ) ( ifelse {(arg.1 ?? "-") == "top"} ( pyramid_top {arg.2 ?? builders} ) ( ifelse {(arg.1 ?? "-") == "fact"} ( pyramid_fact {arg.2 ?? 0} ) ( ifelse {(arg.1 ?? "-") == "settings"} ( pyramid_settings ) ( ifelse {(arg.1 ?? "-") == "addfact"} ( pyramid_addfact {arg.2+raw ?? -} ) ( ifelse {(arg.1 ?? "-") == "delfact"} ( pyramid_delfact {arg.2 ?? 0} ) ( ifelse {(arg.1 ?? "-") == "exempt"} ( pyramid_exempt {arg.2 ?? -} ) ( pyramid_set {arg.1} {arg.2+ ?? -} ) ) ) ) ) ) )',  # noqa: E501
    "pyramid_on_event": 'ifelse {(event.type ?? "-") == "pyramid"} ( ifelse {event.phase == "step"} ( pyramid_step ) ( ifelse {event.phase == "complete"} ( pyramid_complete ) ( pyramid_broken ) ) )',  # noqa: E501
    "pyramid_broken": 'ifelse {event.peak >= (publisher.channel.pyramid[min_peak] ?? 3) - 1 or (publisher.channel.pyramid[attempt] ?? "-") == event.pyramid_id} ( ifelse {event.by_bot} ( var incr publisher.channel.pyramid_stats[broken_by_bot] && true ) ( ifelse {event.self_broken} ( var incr publisher.channel.pyramid_stats[fumbled] && true ) ( var incr publisher.channel.pyramid_stats[broken_by_chatters] && var incr publisher.channel.chatter.pyramid_breaks && true ) ) )',  # noqa: E501
    "pyramid_fact": "ifelse {(arg.number ?? 0) > 0} ( ifelse {arg.number <= (publisher.channel.pyramid_facts:len ?? 0)} ( echo #{arg.number}: {publisher.channel.pyramid_facts[arg.number - 1]} ) ( fail 3 there is no pyramid fact #{arg.number} ) ) ( ifelse {(publisher.channel.pyramid[shared_facts] ?? true) and (publisher.pyramid_facts:len ?? 0) > 0} ( ifelse {(publisher.channel.pyramid_facts:len ?? 0) > 0} ( random {publisher.channel.pyramid_facts + publisher.pyramid_facts} ) ( random {publisher.pyramid_facts} ) ) ( ifelse {(publisher.channel.pyramid_facts:len ?? 0) > 0} ( random {publisher.channel.pyramid_facts} ) ( fail 3 no pyramid facts yet. Add one with {$channel.prefix}pyramid addfact <fact> ) ) )",  # noqa: E501
    "pyramid_set": ' ifelse {arg.1 == "chance" or arg.1 == "chanceup" or arg.1 == "chancedown"} ( ifelse {0 <= (arg.2:int ?? -1) <= 100} ( ( ifelse {arg.1 != "chancedown"} ( var set publisher.channel.pyramid[chance_up] {arg.2:int} ) ( true ) ) && ( ifelse {arg.1 != "chanceup"} ( var set publisher.channel.pyramid[chance_down] {arg.2:int} ) ( true ) ) && echo pyramid break chance is now {publisher.channel.pyramid[chance_up] ?? 25}% going up, {publisher.channel.pyramid[chance_down] ?? 75}% going down ) ( fail 2 usage: pyramid {arg.1} <0-100> ) ) ( ifelse {arg.1 == "minpeak"} ( ifelse {2 <= (arg.2:int ?? 0) <= 50} ( var set publisher.channel.pyramid[min_peak] {arg.2:int} && echo pyramids now count from {arg.2:int} wide ) ( fail 2 usage: pyramid minpeak <2-50> ) ) ( ifelse {arg.1 == "sharedfacts"} ( ifelse {arg.2 == "on" or arg.2 == "off"} ( var set publisher.channel.pyramid[shared_facts] {arg.2 == "on"} && echo shared pyramid facts are {arg.2} ) ( fail 2 usage: pyramid sharedfacts <on|off> ) ) ( ifelse {arg.1 == "congrats" and arg.2 != "-"} ( ifelse {arg.2 == "reset"} ( ( var del publisher.channel.pyramid[congrats] || true ) && echo pyramid congratulations are back to the default ) ( var set publisher.channel.pyramid[congrats] {arg.2+raw} && ifelse {arg.2 == "off"} ( echo pyramid congratulations are off ) ( echo pyramid congratulations set ) ) ) ( fail 2 usage: pyramid <stats|top|fact|settings|chance|chanceup|chancedown|minpeak|exempt|congrats|sharedfacts|addfact|delfact> ) ) ) )',  # noqa: E501
    "pyramid_exempt": '( ifelse {arg.role == "off"} ( var set publisher.channel.pyramid[exempt_rank] 100000 ) ( ifelse {arg.role == "broadcaster"} ( var set publisher.channel.pyramid[exempt_rank] 100 ) ( ifelse {arg.role == "mod"} ( var set publisher.channel.pyramid[exempt_rank] 80 ) ( ifelse {arg.role == "vip"} ( var set publisher.channel.pyramid[exempt_rank] 60 ) ( ifelse {arg.role == "sub"} ( var set publisher.channel.pyramid[exempt_rank] 20 ) ( fail 2 usage: pyramid exempt <off|sub|vip|mod|broadcaster> ) ) ) ) ) ) && var set publisher.channel.pyramid[exempt] {arg.role} && ifelse {arg.role == "off"} ( echo nobody is exempt from pyramid breaks ) ( echo {arg.role} and up are exempt from pyramid breaks )',  # noqa: E501
    "pyramid_addfact": ' ifelse {arg.1+raw == "-"} ( fail 2 usage: pyramid addfact <fact> ) ( ifelse {arg.1+raw:len > 300} ( fail 2 a pyramid fact is at most 300 characters ) ( echo {arg.1+raw} --> publisher.channel.pyramid_facts && echo added pyramid fact #{publisher.channel.pyramid_facts:len} ) )',  # noqa: E501
}
NEW = {d.name: d.body for d in (*QUOTES, *PYRAMID) if d.name in OLD}
# Only multi-branch `ifelse`: the parser nests it back into what the old body spelled out (ADR-0037 G2).
SAME_TREE = ("pyramid_on_event", "pyramid_broken", "pyramid_fact", "pyramid_set", "pyramid_addfact")


def _tree(body: str) -> tuple[str, list[tuple[int, str]]]:
    node = CustomCommandService.parse_body(body, "!")
    return to_canonical(node), [(inv.index, inv.name) for inv in invocations(node)]


@pytest.mark.parametrize("name", SAME_TREE)
def test_flattened_ifelse_parses_to_the_old_tree(name: str) -> None:
    assert NEW[name] != OLD[name]
    assert _tree(NEW[name]) == _tree(OLD[name])


def test_every_rewrite_is_covered() -> None:
    assert set(NEW) == set(OLD)


Step = tuple[str, str, str] | tuple[str, dict[str, Any]]

# Chat lines (who, text) and pyramid events (fields over `_EVENT`). Every random pick has one possible
# outcome: the shared facts are set to ["S"], the channel's own fact is "S" too, and `!quote` alone runs
# only with at most one quote.
STEPS: list[Step] = [
    ("say", "alice", "!quote"),
    ("say", "alice", "!quote -"),
    ("say", "alice", "!quote 1"),
    ("say", "alice", "!quote abc"),
    ("live", "mod", "!quote add first one"),
    ("say", "alice", "!quote"),
    ("say", "alice", "!quote 1"),
    ("say", "mod", "!quote add second"),
    ("say", "alice", "!quote 2"),
    ("say", "alice", "!quote 1.0"),
    ("say", "alice", "!quote ADD x"),
    ("say", "alice", "!quote add nope"),
    ("say", "mod", "!quote add"),
    ("say", "mod", "!quote add " + "x" * 401),
    ("say", "mod", "!quote del"),
    ("say", "mod", "!quote del abc"),
    ("say", "alice", "!quote delete 2"),
    ("say", "mod", "!quote delete 2"),
    ("say", "mod", "!quote del 2"),
    ("say", "mod", "!quote del 1"),
    ("say", "alice", "!quote 2"),
    ("say", "alice", "!pyramid"),
    ("say", "alice", "!pyramid stats"),
    ("say", "alice", "!pyramid STATS"),
    ("say", "alice", "!pyramid -"),
    ("say", "alice", "!pyramid top"),
    ("say", "alice", "!pyramid top breakers"),
    ("say", "alice", "!pyramid settings"),
    ("say", "alice", "!pyramid fact"),
    ("say", "alice", "!pyramid fact 1"),
    ("say", "alice", "!pyramid fact abc"),
    ("say", "alice", "!pyramid addfact x"),
    ("say", "alice", "!pyramid delfact 1"),
    ("say", "alice", "!pyramid exempt mod"),
    ("say", "alice", "!pyramid bogus"),
    ("say", "alice", "!pyramid chance 50"),
    ("say", "mod", "!pyramid bogus"),
    ("say", "mod", "!pyramid exempt"),
    ("say", "mod", "!pyramid exempt admins"),
    ("say", "mod", "!pyramid exempt MOD"),
    ("say", "mod", "!pyramid exempt broadcaster"),
    ("say", "mod", "!pyramid exempt vip"),
    ("say", "mod", "!pyramid exempt sub"),
    ("say", "mod", "!pyramid exempt mod"),
    ("say", "mod", "!pyramid exempt off"),
    ("say", "mod", "!pyramid addfact"),
    ("say", "mod", "!pyramid addfact " + "y" * 301),
    ("say", "mod", "!pyramid addfact S"),
    ("say", "alice", "!pyramid fact"),
    ("say", "alice", "!pyramid fact 1"),
    ("say", "alice", "!pyramid fact 2"),
    ("say", "alice", "!pyramid fact -3"),
    ("say", "mod", "!pyramid sharedfacts maybe"),
    ("say", "mod", "!pyramid sharedfacts off"),
    ("say", "alice", "!pyramid fact"),
    ("say", "mod", "!pyramid delfact 1"),
    ("say", "mod", "!pyramid delfact 1"),
    ("say", "alice", "!pyramid fact"),
    ("say", "mod", "!pyramid sharedfacts on"),
    ("say", "alice", "!pyramid fact"),
    ("say", "mod", "!pyramid chance 101"),
    ("say", "mod", "!pyramid chanceup 0"),
    ("say", "mod", "!pyramid chancedown 30"),
    ("say", "mod", "!pyramid chance 100"),
    ("say", "mod", "!pyramid minpeak 1"),
    ("say", "mod", "!pyramid minpeak 2"),
    ("say", "mod", "!pyramid congrats"),
    ("say", "mod", "!pyramid congrats {event.user.display} wins"),
    ("say", "mod", "!pyramid congrats off"),
    ("say", "mod", "!pyramid congrats reset"),
    ("say", "alice", "!pyramid settings"),
    ("event", {}),
    ("event", {}),
    ("event", {"phase": "complete", "peak": 3}),
    ("event", {"pyramid_id": "p2", "phase": "complete", "peak": 1}),
    ("event", {"pyramid_id": "p3", "phase": "broken", "peak": 3, "by_bot": True}),
    ("event", {"pyramid_id": "p4", "phase": "broken", "peak": 3, "self_broken": True}),
    ("event", {"pyramid_id": "p5", "phase": "broken", "peak": 3}),
    ("event", {"pyramid_id": "p6", "phase": "broken", "peak": 0}),
    ("event", {"pyramid_id": "p7", "phase": "weird", "peak": 3}),
    ("event", {"pyramid_id": "p8", "phase": "STEP", "peak": 3}),
    ("event", {"type": "other"}),
    ("event", {"phase": None}),
    ("say", "alice", "!pyramid_on_event"),
    ("say", "alice", "!pyramid"),
    ("say", "alice", "!pyramid top"),
    ("say", "alice", "!pyramid top breakers"),
]

_EVENT: dict[str, Any] = {
    "type": "pyramid",
    "pyramid_id": "p1",
    "phase": "step",
    "direction": "up",
    "token": "LUL",
    "width": 2,
    "peak": 2,
    "user": {"id": "400", "name": "alice", "display": "Alice"},
    "by_bot": False,
    "self_broken": False,
}


async def _play(harness: Harness, channel: str) -> tuple[list[tuple[int, str | None]], dict[str, Any]]:
    login = CHANNEL_LOGIN if channel == CHANNEL_ID else "other"
    seen: list[tuple[int, str | None]] = []
    for step in STEPS:
        if step[0] == "event":
            fields = step[1]
            assert isinstance(fields, dict)
            event = {k: v for k, v in {**_EVENT, **fields}.items() if v is not None}
            who, text, live = "alice", "pyramid_on_event", False
        else:
            event, (kind, who, text) = None, step  # type: ignore[misc]
            live = kind == "live"
        info = dataclasses.replace(harness.policy.channel_info(channel, login), prefix="!", live=live, game="Doom")
        user = USERS[who]
        chatter = harness.policy.build_chatter(
            channel, user["id"], user["name"], user["display"], frozenset(BADGES.get(who, set()))
        )
        extra: dict[str, Any] = (
            {"context": Context.TRIGGER, "trigger_type": "pyramid", "run_as_rank": 80, "event": event}
            if event is not None
            else {}
        )
        report = await harness.runtime.run(text, harness.runtime.make_context(channel=info, invoker=chatter, **extra))
        assert report is not None
        seen.append((int(report.result.code), report.send))
    async with await harness.dbs.bot.execute(
        "SELECT name, key3, value FROM variables WHERE ns = 'publisher.channel' AND key1 = %s AND key2 = %s",
        (OWNER["id"], channel),
    ) as cur:
        stored = {f"{r['name']}/{r['key3']}": json.loads(r["value"]) for r in await cur.fetchall()}
    return seen, stored


async def _use(harness: Harness, bodies: dict[str, str]) -> None:
    for name, body in bodies.items():
        command = await harness.service.by_owner(OWNER["id"], name)
        assert command is not None, name
        if command.body != body:
            await harness.service.edit(command, body, channel_id=GLOBAL, prefix="!")


async def test_the_rewrites_answer_every_input_as_the_old_bodies_did(h: Harness) -> None:  # noqa: F811
    await _install(h)
    await h.dbs.bot.execute(
        "UPDATE variables SET value = %s WHERE ns = 'publisher' AND key1 = %s AND name = 'pyramid_facts'",
        (json.dumps(["S"]), OWNER["id"]),
    )

    await _use(h, OLD)
    old = await _play(h, CHANNEL_ID)
    await _use(h, NEW)
    new = await _play(h, OTHER_CHANNEL)

    for step, before, after in zip(STEPS, old[0], new[0], strict=True):
        assert before == after, step
    assert old[1] == new[1]
    # The run went where it was meant to: each kind of answer came up.
    sends = [send for _, send in old[0]]
    for expected in (
        "added #1",
        "deleted #2",
        "there is no quote #2",
        "S",
        "mod and up are exempt from pyramid breaks",
    ):
        assert expected in sends, expected
    assert old[1]["pyramid_stats/"]["broken_by_bot"] == 1 and old[1]["pyramid_stats/"]["fumbled"] == 1
