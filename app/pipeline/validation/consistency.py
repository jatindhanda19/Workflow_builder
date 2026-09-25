"""Consistency and contradiction checks.

A value can be valid in format and still be suspicious: a name that restates the
goal, "clients" followed by one address, fixed text that mentions an amount. Each
rule returns an Ambiguous describing the readings as Options, so the user's reply
can be resolved in code.
"""

import re
from dataclasses import dataclass, field

from app.pipeline.extraction.normalize import plain
from app.registry import NO, YES, FieldDef
from app.state.models import FieldValue, Option

FILE_WORDS = re.compile(r"\b(file|spreadsheet|workbook|document|doc)\b", re.IGNORECASE)
TAB_WORDS = re.compile(r"\b(tab|worksheet)\b", re.IGNORECASE)
SHEETISH = re.compile(r"\b(sheet|tab)\b", re.IGNORECASE)
COLUMN_WORDS = re.compile(r"\bcolumn\b", re.IGNORECASE)
GOAL_VERBS = frozenset({"send", "notify", "email", "post", "sync", "backup", "alert", "forward", "copy", "remind", "automate", "auto"})
# Verb forms and "change in ..." phrasing only: nouns such as "updates" are common in real names.
EVENT_WORDS = frozenset({
    "changed", "updated", "edited", "modified", "added", "deleted", "removed", "created", "arrives", "arrived",
    "submitted", "when", "whenever", "if",
})
EVENT_PHRASE = re.compile(r"\b(?:change|update|edit)s? (?:in|of|to)\b")
LOCATION_NAMES = frozenset({"file_name", "tab_name", "label", "folder", "form_name"})
STOPWORDS = frozenset({"a", "an", "the", "to", "my", "our", "all", "of", "for", "in", "on", "and", "every", "each"})
PLACEHOLDER_WORDS = (
    "invoice number", "invoice no", "due date", "client name", "customer name", "amount", "total", "name", "date",
)
BRACKETED = re.compile(r"[\[{<]\s*([\w ]+?)\s*[\]}>]")


@dataclass(frozen=True)
class CheckContext:
    message: str
    target_field: str | None
    values: dict[str, FieldValue]
    original_request: str
    cardinality: str | None
    recipient_word: str


@dataclass(frozen=True)
class Ambiguous:
    key: str
    phrase: str
    reason: str
    question: str
    options: list[Option] = field(default_factory=list)


def detect(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    for rule in (_sheet_location, _column_name, _restates_goal, _sounds_like_event, _placeholders_in_fixed_text,
                 _plural_vs_single):
        found = rule(fdef, value, ctx)
        if found:
            return found
    return None


def resolve(options: list[Option], message: str) -> dict[str, FieldValue | None] | None:
    """Pick the option the reply points to, or None when it is not clear."""
    lowered = re.sub(r"[^\w\s#@:.-]", " ", message.lower())
    words = lowered.split()
    if words and words[0] in NO and len(options) == 1:
        return None
    matched = [
        o for o in options if any(re.search(rf"(?<![\w]){re.escape(k)}(?![\w])", lowered) for k in o.keywords)
    ]
    both = next((o for o in matched if "both" in o.keywords), None)
    if both:
        return dict(both.assign)
    return dict(matched[0].assign) if len(matched) == 1 else None


def _sheet_location(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    if fdef.name not in ("file_name", "tab_name") or not isinstance(value, str):
        return None
    if (FILE_WORDS if fdef.name == "file_name" else TAB_WORDS).search(ctx.message):
        return None
    if fdef.key == ctx.target_field and not SHEETISH.search(value):
        return None
    file_key, tab_key = f"{fdef.node}.file_name", f"{fdef.node}.tab_name"
    options = []
    if file_key not in ctx.values:
        options.append(Option(label="the spreadsheet file", assign={file_key: value}, keywords=["file", "spreadsheet", "document"]))
    if tab_key not in ctx.values:
        options.append(Option(label="a tab inside the file", assign={tab_key: value}, keywords=["tab", "worksheet"]))
    if not options:
        return None
    if len(options) == 2:
        options.append(Option(label="both", assign={file_key: value, tab_key: value}, keywords=["both"]))
    labels = ", ".join(o.label for o in options[:-1]) + (" or " if len(options) > 1 else "") + options[-1].label
    return Ambiguous(
        key=file_key if file_key not in ctx.values else tab_key, phrase=value,
        reason=f'"{value}" could be the spreadsheet file or a tab', question=f'Is "{value}" the name of {labels}?',
        options=options,
    )


def _column_name(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    if fdef.key != "sheet_trigger.watched_column" or not isinstance(value, str):
        return None
    if fdef.key == ctx.target_field or COLUMN_WORDS.search(ctx.message):
        return None
    column = value.strip().title()
    return Ambiguous(
        key=fdef.key, phrase=value, reason=f'"{value}" was not named as a column',
        question=f'Which column holds the {value.lower()}? Is it a column named "{column}"?',
        options=[Option(label=f'the column "{column}"', assign={fdef.key: column}, keywords=list(YES))],
    )


def _restates_goal(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    if fdef.kind != "name" or not isinstance(value, str):
        return None
    tokens = [t for t in plain(value).split() if t not in STOPWORDS]
    request = set(plain(ctx.original_request).split())
    verb = any(t in GOAL_VERBS for t in tokens) and len(tokens) >= 2
    echo = len(tokens) >= 3 and sum(t in request for t in tokens) / len(tokens) >= 0.8
    if not (verb or echo):
        return None
    label = fdef.label.lower()
    return Ambiguous(
        key=fdef.key, phrase=value, reason=f'"{value}" sounds like the goal of the workflow, not a {label}',
        question=f'Just to check: is "{value}" the exact name of the {label}? It sounds like what the workflow should do.',
        options=[
            Option(label="yes, that is the exact name", assign={fdef.key: value}, keywords=[*YES, "exact", "exactly", "literal"]),
            Option(label="no", assign={fdef.key: None}, keywords=[*NO, "not"]),
        ],
    )


def _sounds_like_event(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    """ "change in value" given as a tab name is more likely an answer to the trigger-event question."""
    if fdef.name not in LOCATION_NAMES or fdef.kind != "name" or not isinstance(value, str):
        return None
    text = plain(value)
    tokens = [t for t in text.split() if t not in STOPWORDS]
    if len(tokens) < 2 or not (EVENT_PHRASE.search(text) or any(t in EVENT_WORDS for t in tokens)):
        return None
    label = fdef.label.lower()
    return Ambiguous(
        key=fdef.key, phrase=value, reason=f'"{value}" sounds like an event, not the name of a {label}',
        question=(f'Just to check: is "{value}" the exact name of the {label}? '
                  "It sounds like what should start the workflow."),
        options=[
            Option(label="yes, that is the exact name", assign={fdef.key: value}, keywords=[*YES, "exact", "exactly", "literal"]),
            Option(label="no", assign={fdef.key: None}, keywords=[*NO, "not"]),
        ],
    )


def _placeholders_in_fixed_text(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    if fdef.key != "email.body_text" or not isinstance(value, str):
        return None
    words = _placeholder_words(value)
    if not words:
        return None
    shown = ", ".join(f'"{w}"' for w in words)
    return Ambiguous(
        key=fdef.key, phrase=value, reason=f"the fixed text mentions {shown}, which usually changes per email",
        question=(
            f"Your text mentions {shown}. Should {'that' if len(words) == 1 else 'those'} be filled in from the data "
            "for each email, or sent exactly as written?"
        ),
        options=[
            Option(label="fill them in from the data", keywords=[*YES, "fill", "data", "dynamic", "variable", "template", "each"],
                   assign={"email.content_mode": "template", "template.body": to_template(value, words), fdef.key: None}),
            Option(label="send exactly as written", assign={fdef.key: value},
                   keywords=[*NO, "exactly", "as written", "literal", "fixed", "keep"]),
        ],
    )


def _plural_vs_single(fdef: FieldDef, value: FieldValue, ctx: CheckContext) -> Ambiguous | None:
    if fdef.key != "email.recipients" or ctx.cardinality not in ("multiple", "dynamic"):
        return None
    if not isinstance(value, list) or len(value) != 1:
        return None
    address, who = value[0], ctx.recipient_word
    source_open = "recipients.source" not in ctx.values
    return Ambiguous(
        key="recipients.source" if source_open else fdef.key, phrase=address,
        reason=f"you mentioned {who}s (more than one) but gave a single address",
        question=f"You mentioned {who}s, but gave one address ({address}). Should I send only to {address}, or to each {who} from a list?",
        options=[
            Option(label=f"only {address}", keywords=["only", "just", "single", "one", address],
                   assign={"recipients.source": "fixed_list", fdef.key: [address], "intent.cardinality": "single"}),
            Option(label=f"each {who} from a list", assign={fdef.key: None, "recipients.source": None},
                   keywords=["each", "list", "every", "all", "sheet"]),
        ],
    )


def _placeholder_words(text: str) -> list[str]:
    found = [m.strip().lower() for m in BRACKETED.findall(text)]
    lowered = text.lower()
    for word in PLACEHOLDER_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered) and not any(word in f for f in found):
            found.append(word)
    return found


def to_template(text: str, words: list[str]) -> str:
    """Turn "pay the amount by Friday" into "pay the {amount} by Friday"."""
    result = BRACKETED.sub(lambda m: "{" + _snake(m.group(1)) + "}", text)
    for word in sorted(words, key=len, reverse=True):
        result = re.sub(rf"(?<![{{\w]){re.escape(word)}(?![\w}}])", "{" + _snake(word) + "}", result, flags=re.IGNORECASE)
    return result


def _snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
