# ADR-0037: Listed forms, `switch`, multi-branch `ifelse`, returned fields and `format`

**Status:** Accepted, 2026-10-24; built in v0.21.0
**Date:** 2026-10-23
**Deciders:** Project owner

## Context

Five requests about the command language arrived together:

- **G7.** "`ifelse {true} (…) (…)` works as a command, so it feels weird to be listed as grammar. Can it
  and other grammar be done instead as commands?" Today `ifelse` is a special form: the parser builds an
  `IfElse` node (`lang/parser.py` `if_else`), the executor evaluates it (`runtime/executor.py` `_eval`)
  and preflight walks both branches (`runtime/preflight.py`). `/api/v1/commands`, `!help`, the editor's
  autocomplete and doomtp-web's reference read only the command registry, so none of them list it.
- **G1** (decided: `switch`, since `match` is a reserved root) and **G2** (decided: more branches in
  `ifelse`, no brackets).
- **G3.** The docs should list `ifelse`, `switch` and the other forms.
- **G9.** "Shouldn't dtp commands also return a dict alongside the string? How would a custom command set
  that, and how would the docs show it?"
- **G11.** A formatter for numbers and times.

What exists already matters for G9: every Result is `(code, message, data)` (spec §6.1), `data` may be a
map, callers read it with `{_1[celsius]}` or `{!weather}`, and built-in specs declare it in
`CommandSpec.data_schema`, which the reference shows. What is missing is a way for a **custom** command
to build a map, and a way for the docs to know a custom command's fields.

## G7: can the forms be commands?

A command gets its arguments **at run time, already expanded**. A form needs three things a command
can't have:

1. **Its groups parsed as code before anything runs.** Preflight (spec §5.2) checks every command in
   every branch up front: names, permissions on the chosen path, references, `MAX_INVOCATIONS`, custom
   command cycles. If `( echo a )` were an argument it would be text until the handler ran, so preflight
   would have to parse arguments of "lazy" commands anyway, which is the special form again under
   another name.
2. **No index of its own.** Invocations are numbered for `_N` (§6.4). A command `ifelse` would take an
   index and shift every `_N` after it in every stored custom command, trigger and timer. A migration
   could renumber them, but text people typed in chat a minute ago would silently mean something else.
3. **No policy.** A command has a role, cooldowns, a toggle and a log level. A form must have none of
   them, or disabling `ifelse` in a channel would break every command that uses it.

`check` and `calc` show the same limit from the other side: they are commands, but the parser already
special-cases them by name (`ExprArgs` in §C.4) because their argument is an expression, not words.

**Options**

1. **Real commands with lazy group arguments.** Pay costs 1–3: parser still name-driven, an index
   migration, sentinel-like policy rules, and preflight learning which arguments are code.
2. **Keep them as forms in the grammar, and list them like commands.** A small `FORMS` table beside the
   registry holds each form's name, syntax line, summary, description and examples, in the same
   `CommandSpec` shape with `kind = "form"`. `/api/v1/commands` returns them with `"kind": "form"`,
   `!help ifelse` explains it, the editor completes it, doomtp-web's reference lists it, and
   `/api/v1/commands/{name}` works for it. Parser, executor and preflight don't change.
3. **Document them only** (a docs section, G3's fallback).

**Decision: option 2.** To a user it is a command in every place they look; inside, it keeps the
guarantees above. The forms listed: `ifelse`, `switch`, and the operators as one entry each
(`|`, `&&`, `||`, `( … )`, `->`, `-->`) so the reference has somewhere to explain them.

## G2: more branches in `ifelse`

```
ifelse {c1} ( t1 ) {c2} ( t2 ) … ( else )
```

```peg
IfElse   <- CmdPrefix? 'ifelse' Boundary IfCond IfThen (WS IfCond IfThen)* (WS Branch)?
```

- After a branch, a word followed by a branch is another condition; a lone branch is the else and must
  be last (V2.27 stays an error).
- Conditions are evaluated in order and stop at the first that holds. A missing condition fails with
  230 and runs nothing after it, as today.
- The AST nests: `ifelse c1 t1 c2 t2 e` is `IfElse(c1, t1, Group(IfElse(c2, t2, e)))`. Executor,
  preflight and `_N` numbering need no change, and `!explain` shows the chain.

## G1: `switch`

```
switch {subject} {v1} ( b1 ) {v2} ( b2 ) … ( default )
```

- `subject` is evaluated once; each case value is evaluated in turn and the first that is **equal as a
  value**, by the same rule as `==` in an expression (§7.4), wins. So `1` matches `1.0`, and
  `switch {arg.1} 1 (…)` matches whether `arg.1` came in as the text `1` or the number. Text compares
  case-sensitively, as `==` does.
- Case values are evaluated only until a match, so `{!…}` in a later case doesn't run.
- A lone last group is the default. No match and no default: success with no output, like `ifelse`.
- A missing subject fails with 230 and runs nothing. `switch {x}` with no case is a parse error.
- AST: `Switch(subject, cases: [(value, Group)], default)`. Preflight checks every branch as it checks
  `ifelse` branches (a command is refused only on the path chosen).
- `switch` becomes a reserved name, like `ifelse`.

## G3

Falls out of G7 option 2: the forms come from the same source as the commands. The spec keeps §8.2 and
gains §8.3 `switch`.

## G9: returned fields

**Built-ins** already return data and declare it in `data_schema`; nothing changes, except that the
reference shows `data_schema` as a "Returns" table on every command page (some pages don't yet).

**Custom commands** get one new core primitive, `result`:

```
result <key>=<value> … [-- <message…>]
```

- Each `key=value` word becomes a field; the key is an ident, and a value that is a single placeholder
  keeps its type (like a param of type `any`). The words after `--` are the message. Without them, the
  message is the one piped in, so `echo hi | result n=1` still says `hi`; with neither, it is null.
- The message is also the field `message`, always present (null when there is none), so a caller reading
  the map gets the text too. `message=…` as a key is refused (E_USAGE): the message goes after `--`.
- Like the other sentinels: no policy, logged at `off`, counts toward `MAX_INVOCATIONS`; data is
  limited by `MAX_DATA_BYTES`.

A weather command's body:

```
http https://api.example/now?q={arg.1+} | result celsius={_[temp_c]} rain={_[rain_mm]} -- {_[temp_c]}°C, {_[rain_mm]} mm
```

Its callers:

```
!weather Lisbon                                   → 21°C, 0 mm
!weather Lisbon | ifelse {_[rain] > 0} ( echo bring a coat ) ( echo {_[celsius]}°C )
!echo it is {!weather Lisbon}                     → the data, so read a field: {{!weather Lisbon}[celsius]}
```

(The `,` in the request is `|` here: the right side reads the left's result as `_`.)

**Docs.** The bot infers a custom command's `data_schema` from its body and the reference shows the same
"Returns" table it shows for built-ins, with no field to fill in.

A body can end in more than one place: each branch of an `ifelse` or `switch`, and the right side of an
`||`. The owner asked whether to force a single `result` at the end so every branch pipes into it. That
would make the common case awkward (`ifelse {c} ( result a=1 ) ( result a=2 )` would have to become
`ifelse {c} ( echo 1 ) ( echo 2 ) | result a={_}`, which can't give the branches different fields), and
the schema can be read without it:

- The **endings** of a body are its last stages: through `|`, `&&` and groups to the right side, into
  every branch of `ifelse` and `switch` (including the missing else, which returns nothing), and both
  sides of `||`.
- The Returns table is the **union** of the keys of the endings that are a `result`, plus `message`. A
  key that not every ending returns is marked **sometimes**, so the table says honestly what a caller can
  rely on.
- A body with no `result` ending shows no table.

## G11: `format`, and the same as accessors

Both: a core command for a whole value, listed, completed and documented like any other, and accessors
for formatting inside a placeholder.

| Form | Example | Says |
|------|---------|------|
| `format time <value> [pattern]` | `format time {$now.unix} DD MMM HH:mm` | `23 Oct 21:30` |
| `format ago <value>` | `format ago {channel.last_death}` | `3h 2m ago` |
| `format duration <seconds>` | `format duration 90061` | `1d 1h 1m 1s` (`{x:human}` keeps two units) |
| `format number <value> [decimals]` | `format number 1234567.891 2` | `1,234,567.89` |
| `format percent <value> [decimals]` | `format percent 0.4567 1` | `45.7%` |
| `format ordinal <value>` | `format ordinal 22` | `22nd` |

- `time` and `ago` take epoch seconds or ISO 8601 text, in the channel's timezone. Pattern tokens are
  Moment-style: `YYYY YY MM MMM MMMM DD D ddd dddd HH H hh mm ss A`, everything else literal; default
  `YYYY-MM-DD HH:mm`. Not strftime: `%` reads as a modulo to people who write expressions.
- The accessors are the same, except for durations: `{x:time}`, `{x:time(DD MMM HH:mm)}`, `{x:ago}`,
  `{x:number(2)}`, `{x:percent(1)}`, `{x:ordinal}`. A pattern runs to the closing `)`. `:duration` was
  already the cast that reads `1h30m` as seconds, so it stays that, and a duration's formatter is the
  existing `:human` (two units, `1h 30m`).
- `message` is the text and `data` the same text, so `-> channel.x` stores it.
- A value that isn't a number or a time fails with 232 (`E_TYPE`).

## Consequences

- No `_N` numbering changes and no migration of stored commands.
- Two names become reserved (`switch`, `result`, plus `format` as a command name). A channel with a
  custom command of one of these names would lose it: the action items check production first.
- The editor's tokenizer learns `switch` as a form keyword.

## Action items

1. [x] `FORMS` table with `kind = "form"`, served by `/api/v1/commands` and `!help`; doomtp-web lists them.
   *(`runtime/forms.py`, v0.21.0; doomtp-web#109.)*
2. [x] Multi-branch `ifelse` (parser, spec C.3, railroad, corpus). *(v0.21.0.)*
3. [x] `switch` (parser, AST, executor, preflight, explain, spec §8.3, corpus, editor). *(v0.21.0.)*
4. [x] `result` primitive and the inferred `data_schema`; "Returns" tables in doomtp-web. *(The endings
   come from `lang/ast.endings`, v0.21.0; doomtp-web#109.)*
5. [x] `format` command and the accessors. *(`runtime/formats.py`, v0.21.0; `:human` for durations, as
   above.)*
6. [x] Before 3–5: count custom commands named `switch`, `result` or `format` in production. *(The public
   API showed none on 2026-10-24, and the owner confirmed on 2026-10-26 that no alias or unpublished
   command has those names either.)*

## Decisions

The owner answered the open questions on 2026-10-24:

1. **G7:** forms stay in the grammar, listed beside commands with `kind = "form"`.
2. **G1:** `switch` compares values, so `1` matches `1.0` (the draft recommended rendered text).
3. **G9:** the `result key=value … -- message` primitive, with the message always in the `message`
   field.
4. **G9:** the Returns table is inferred from the body: the union of every ending's keys, a key some
   endings lack marked "sometimes" (see G9 above).
5. **G11:** both a `format` command and accessors.
6. **G11:** Moment-style pattern tokens.

Before reserving `switch`, `result` and `format`, the public API was checked on 2026-10-24: no channel
publishes a command by those names and no pack has one. Personal aliases and unpublished commands aren't
public; the owner confirmed on 2026-10-26 that none has those names (action item 6).
