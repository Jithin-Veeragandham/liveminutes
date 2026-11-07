"""Meeting chat: reading consent replies and writing the bot's consent request."""

import html
import re

CONSENT_PROMPT = "LiveMinutes is here to take notes. Reply yes to allow recording, or no to stop."

# Only clear, short answers count. Anything else ("no problem, go ahead") is ignored (and logged).
YES = {"yes", "y", "yeah", "yep", "yes please", "yes i agree", "i agree", "agree", "allow", "yes allow", "ok yes"}
NO = {"no", "n", "nope", "no thanks", "no thank you", "stop", "decline", "i decline", "i do not agree",
      "i dont agree", "dont record", "do not record", "no dont record", "no do not record"}


def normalize(text):
    """Lowercase, letters/digits/spaces only: "Veeragandham, Jithin" -> "veeragandham jithin"."""
    text = (text or "").lower().replace("'", "")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def parse_reply(text):
    """ "Yes!" -> "yes", "No thanks" -> "no", anything else -> None."""
    phrase = normalize(text)
    if phrase in YES:
        return "yes"
    if phrase in NO:
        return "no"
    return None


def find_name(name_words, names):
    """The one display name these words point to: an exact match, else the only name that
    contains all the words ("jithin" -> "Veeragandham, Jithin"). None if zero or several fit."""
    exact = [n for n in names if normalize(n).split() == name_words]
    partial = [n for n in names if set(name_words) <= set(normalize(n).split())]
    hits = exact or partial
    return hits[0] if len(hits) == 1 else None


def parse_vouch(text, names):
    """A reply for someone else (a meeting room, phone line or bot that can't type):
    "<name> yes", "yes <name>", "yes for <name>", "yes on behalf of <name>" (same with no).
    names: display names of the people present. Returns (name, "yes" | "no") or None."""
    words = normalize(text).split()
    for cut in range(1, len(words)):
        # Try both orders: the name first, then the answer; or the answer first, then the name.
        for name_words, answer_words in ((words[:cut], words[cut:]), (words[cut:], words[:cut])):
            answer = " ".join(answer_words).removesuffix(" for").removesuffix(" on behalf of")
            decision = parse_reply(answer)
            name = find_name(name_words, names) if decision else None
            if name:
                return name, decision
    return None


def consent_message(names, teams):
    """The first consent request, listing everyone present, one per line.
    Teams: Attendee types plain text key by key, so a newline would press Enter and send half the
    message. HTML (<br>, <b>) is pasted as rich text instead. Meet sends text as-is, so \\n works."""
    example = "Conference Room 4B yes"
    if teams:
        people = "".join(f"<br>• {html.escape(n)}" for n in names)
        return ("<b>LiveMinutes is here to take notes.</b><br>"
                "Reply <b>yes</b> to allow recording, or <b>no</b> to stop.<br><br>"
                f"<b>In this meeting:</b>{people}<br><br>"
                f"For a meeting room, phone line or another bot, reply with its name and yes, e.g. <i>{example}</i>")
    people = "".join(f"\n• {n}" for n in names)
    return (f"{CONSENT_PROMPT}\n\nIn this meeting:{people}\n\n"
            f'For a meeting room, phone line or another bot, reply with its name and yes, e.g. "{example}"')


def plain(text):
    """Chat text without the Teams HTML (<br> -> newline, tags removed), for the live view."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    return html.unescape(re.sub(r"<[^>]+>", "", text))
