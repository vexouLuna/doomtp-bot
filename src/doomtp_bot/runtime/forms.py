"""The language's forms, listed beside the commands (ADR-0037 G7).

`ifelse`, `switch` and the operators are grammar: the parser builds them, so they have no permission,
cooldown or toggle, and their branches run lazily. This table only describes them, in the shape of a
command, so `/api/v1/commands`, `!help` and the editor can show them where a reader looks for commands.
"""

from __future__ import annotations

from dataclasses import dataclass

from doomtp_bot.runtime.spec import CommandSpec, Example

MODULE = "language"


@dataclass(frozen=True, slots=True)
class Form:
    spec: CommandSpec  # fixed_policy and not toggleable: a form has no policy
    syntax: str  # what `usage` is for a command

    @property
    def keyword(self) -> bool:
        """Typed as a word, like a command name (`ifelse`), rather than as an operator (`|`)."""
        return self.spec.name.isalpha()


def _form(name: str, syntax: str, summary: str, description: str, *examples: Example) -> Form:
    spec = CommandSpec(
        name=name,
        module=MODULE,
        summary=summary,
        description=description,
        examples=examples,
        toggleable=False,
        fixed_policy=True,
    )
    return Form(spec, syntax)


FORMS: tuple[Form, ...] = (
    _form(
        "ifelse",
        "ifelse <condition> ( then ) [<condition> ( then )…] [( else )]",
        "Run the branch of the first condition that holds, or the else branch",
        "Each condition is one argument, usually a placeholder such as {x > 1}. Only the chosen branch runs, "
        "so a command in another branch is never refused or run. Spec §8.2.",
        Example("{sign}ifelse {2 > 1} ( echo yes ) ( echo no )", "yes"),
        Example("{sign}random 1-9 | ifelse {_ > 6} ( echo high ) {_ > 3} ( echo mid ) ( echo low )", "mid"),
    ),
    _form(
        "switch",
        "switch <value> <case> ( then ) [<case> ( then )…] [( default )]",
        "Run the branch of the first case equal to the value, or the default branch",
        "Cases compare like ==: numbers as numbers, so 1 matches 1.0, and text case-sensitively. Spec §8.3.",
        Example("{sign}random 1-3 | switch {_} 1 ( echo one ) 2 ( echo two ) ( echo three )", "two"),
    ),
    _form(
        "|",
        "a | b",
        "Pipe: run b with a's result as its input",
        "b runs only if a succeeded, sees a's result as {_}, and gets it as its input, so a command that "
        "reads input (upper, for one) acts on it with no placeholder. Spec §6.3.",
        Example("{sign}random 1-6 | echo you rolled {_}", "you rolled 4"),
    ),
    _form(
        "&&",
        "a && b",
        "Then: run b only if a succeeded",
        "b sees a's result as {_} but gets no input. Only the last result is sent to chat. Spec §6.3.",
        Example("{sign}random 1-6 -> chatter.roll && echo saved {_}", "saved 4"),
    ),
    _form(
        "||",
        "a || b",
        "Or else: run b only if a failed",
        "b sees a's failed result as {_}. && and || bind equally, left to right. Spec §6.3.",
        Example("{sign}echo {chatter.lucky} || echo no lucky number yet", "no lucky number yet"),
    ),
    _form(
        "( )",
        "( a … )",
        "Group: treat what's inside as one command",
        "Groups change the order operators apply in, and are the branches of ifelse and switch. Spec §3.2.",
        Example("{sign}( random 1-6 | echo rolled {_} ) -> chatter.last", "rolled 4"),
    ),
    _form(
        "->",
        "a -> name",
        "Store: save a's result in a variable",
        "Stores the result's fields, or its message when it has none, and passes the result on. A store "
        "applies to one command; group a pipe to store its result. Spec §6.5.",
        Example("{sign}random 1-100 -> chatter.lucky", "57"),
    ),
    _form(
        "-->",
        "a --> name",
        "Append: add a's result to a list variable",
        "A missing variable becomes a one-item list; anything but a list fails. Spec §6.5.",
        Example("{sign}echo hello --> channel.greetings", "hello"),
    ),
)

BY_NAME: dict[str, Form] = {f.spec.name: f for f in FORMS}
