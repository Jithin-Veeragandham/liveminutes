"""Baseline 1: action items from phrase rules. No model, no training.

    items = extract(lines, people)                 # lines: UI contract transcript lines; people: display names
    items = extract(lines, people, date(2026, 11, 18))   # with the meeting date, "by Friday" becomes 2026-11-20

Output: the UI contract's action items (docs/UI_CONTRACT.md), plus two extra fields for reading the results:
  {"id": "a1", "text": "Send the minutes round", "owner": "Helen", "due": "", "due_text": "tonight",
   "status": "pending", "source_line_ids": ["t412300"], "version": 1, "kind": "first_person"}

How a line becomes an action item (each step is one function below):
  1. clean     lower case, no "um"/"uh", "gonna" -> "going to", repeated words once ("I I'll" -> "I'll").
  2. trigger   a phrase that makes a commitment or a request, in one of five kinds (TRIGGERS):
                 first_person  "I'll", "I will", "I can", "I'm going to", "let me", "should I"  -> owner: the speaker
                 request       "can you", "could you", "you'll", "you need to", "please"       -> owner: who is asked
                 third_person  "<name> will", "<name> should", "ask <name> to"                 -> owner: that person
                 group         "let's", "we need to", "we should", "someone should"           -> owner: nobody ("")
                               (with a verb of work done after the meeting only: GROUP_VERBS)
  3. task verb the trigger must be followed (after a few small words like "just", "also") by a verb that names
               work: send, check, look into, research, write up, ... (TASK_VERBS). "I'll be honest", "can you hear
               me", "let's move on" have a trigger but no task verb, so they are not items (and NOT_TASKS lists the
               common ones explicitly). Verbs that are also done in the meeting itself ("present", "show",
               "discuss") count only with a time later than now ("next time", "tomorrow").
  4. owner     request: the person named in the line ("Priya, can you ..."); else whoever answers "Sure" /
               "I can do that" in the next few lines; else the next person to speak. "Put that on Tom", "add it to
               his list", "he can ask": the person named (or, for he/she, named just before, or the one absent). Anyone saying "I'll take that" / "I can do that" right after a
               request, an offer or a "we need to" takes the task (an offer taken by someone else).
  5. due       the first due-date phrase in the line or the answer (due.py).
  6. merge     the same task said twice (a request, then "yes, I'll write the commands"), or restated at the end,
               becomes one item: the owner answering within merge_window_lines (or a little word overlap
               there), or, anywhere in the meeting, content words overlapping by merge_jaccard.
  7. drop      "forget that", "no need to", "scratch that" about an earlier task removes it.
"""
import re
from dataclasses import dataclass, field, replace

from due import find_due, resolve

# ================= 1. clean =================

FILLERS = re.compile(r"\b(um+|uh+|uhm+|erm+|er|ah+|hmm+|mm+|mm-hmm|uh-huh)\b[,.]?", re.IGNORECASE)
SPOKEN = [(r"\bgonna\b", "going to"), (r"\bwanna\b", "want to"), (r"\bgotta\b", "got to"),
          (r"\bi'm\b", "i'm"), (r"\bok\b", "okay")]


def clean(text):
    """Lower case, fillers out, spoken forms spelled out, a word repeated straight away kept once."""
    t = (text or "").replace("’", "'").replace("‘", "'").lower()
    t = FILLERS.sub(" ", t)
    for pattern, repl in SPOKEN:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"\b([\w']+)(?:[\s,]+\1\b)+", r"\1", t)       # "i i'll" -> "i'll", "the the" -> "the"
    return " ".join(t.split())


def words_of(t):
    return re.findall(r"[a-z0-9][a-z0-9'\-]*", t)


# ================= 2. triggers =================

# (regex, kind). The regex is searched in the cleaned line; the task verb must come right after it.
TRIGGERS = [
    (r"\b(?:i'll|i will|i shall|i can|i could|i'm going to|i am going to|i need to|i have to|i've got to|"
     r"i should|i might|i'd be happy to|i'm happy to|happy to|let me|i'll try to|i will try to|i'm gonna|"
     r"should i|shall i|do you want me to|want me to|i'm on|i'll go and|i'll also|i'll just)\b", "first_person"),
    (r"\b(?:can you|could you|would you|will you|would you mind|can you please|could you please|i need you to|"
     r"i want you to|i'd like you to|you'll|you will|you're going to|you are going to|you need to|you should|"
     r"make sure you|please|if you could)\b", "request"),
    # Not "you can / you could / you have to": in meetings that is mostly the generic "you" of a design idea
    # ("you can make them red"), not a request.
    (r"\b(?:let's|let us|we'll|we will|we're going to|we are going to|we need to|we have to|we've got to|"
     r"we should|we must|we can|someone should|somebody should|someone needs to|somebody needs to|"
     r"someone has to|someone could|somebody could|someone'll|we might want to|"
     r"(?:everyone|everybody|you guys|all of you)(?:'s| is| are)? (?:going to|will|should|needs? to|has to|have to))\b",
     "group"),
]
TRIGGERS = [(re.compile(p), kind) for p, kind in TRIGGERS]
THIRD_PERSON = r"(?:will|'ll|is going to|should|needs to|has to|can|could|is to|must)"
# "Marcus gets the usage numbers", "Marcus is doing the user flow": a task stated as someone's. The words after the
# verb must name something (2+ content words), so "Marcus is doing well" is not a task.
HOLDS = (r"(?:is|'s)\s+(?:doing|getting|taking|handling|sorting out|writing|making|preparing|building|checking|"
         r"researching|looking into|working on)|gets|takes|handles|does|owns")
# Someone else takes over a task that was given to a named person: "Oh, I can take that, actually."
TAKEOVER = re.compile(r"\b(?:i(?:'ll| will| can| could)(?: just)? take (?:that|it|this)(?: on)?|let me (?:take|do) "
                      r"(?:that|it)|i(?:'ll| can) do (?:that|it) instead|i'll take over)\b")

# Trigger phrases that are almost never a task. Removed from the line before looking for triggers.
NOT_TASKS = re.compile(
    r"\b(?:i'll be honest|to be honest|i'll tell you what|i'll tell you|i can see|i can hear|i could see|"
    r"can (?:you|everyone|everybody) (?:hear|see)|could you (?:hear|see|repeat|say that)|can you (?:repeat|say that)|"
    r"you can see|as you can see|you'll see|we'll see|let me see|let me think|let's see|let me just say|"
    r"let's (?:start|begin|get started|move on|go on|continue|go back|go through|have a look|look at|recap|"
    r"wrap up|finish|stop|call it|take a break|get going|go)|i could be wrong|i must say|i have to say|"
    r"i've got to say|i should say|i'll admit|i will say|i can't|you can't|we can't|i can imagine|"
    r"you could say|i'll be quick|let me introduce|let me show you|i'll show you|i'll just show|"
    r"(?:make|keep) (?:this|it) (?:quick|short|brief)|"
    r"you(?:'ll| will| are going to|'re going to) (?:get|receive|be|see|find|have|hear|notice|need|want|like)|"
    r"(?:do you want|you want|would you like|want me) to (?:go first|go next|start|begin|kick off|present first)|"
    r"(?:i'll|i can|i will|let me) (?:go first|go next|start|begin|kick off))\b")
# A future assignment to "you": in-meeting verbs count after it ("you're going to look at product evaluation").
FUTURE_YOU = re.compile(r"\b(?:you'll|you will|you're going to|you are going to|you need to)\s*$")

# Words allowed between the trigger and the task verb.
SKIP = {"just", "also", "then", "probably", "maybe", "definitely", "quickly", "actually", "really", "all", "both",
        "try", "to", "and", "go", "please", "first", "still", "perhaps", "now", "certainly", "um", "uh", "so",
        "um,", "start", "be", "able", "of", "course", "personally", "myself", "yourself", "obviously", "each",
        "briefly", "properly", "definately", "kindly", "possibly", "get", "on", "have", "a", "go", "at"}
MAX_SKIP = 3

# ================= 3. task verbs =================

# Verbs that name work someone does after (or outside) the meeting. Base form, as after "I'll" / "can you".
TASK_VERBS = {
    "send", "email", "e-mail", "mail", "forward", "share", "circulate", "post", "upload", "distribute", "message",
    "text", "ping", "check", "verify", "confirm", "test", "research", "investigate", "explore", "find", "figure",
    "work", "make", "build", "create", "design", "draw", "sketch", "prototype", "mock", "model", "prepare", "write",
    "draft", "put", "compile", "collect", "gather", "contact", "call", "ring", "phone", "talk", "speak", "ask",
    "meet", "schedule", "book", "organise", "organize", "arrange", "set", "follow", "chase", "bring", "buy",
    "order", "print", "update", "finish", "complete", "finalise", "finalize", "fix", "add", "review", "read",
    "summarise", "summarize", "report", "cost", "price", "estimate", "calculate", "compare", "measure",
    "evaluate", "analyse", "analyze", "come", "handle", "take", "sort", "deal", "remind", "notify", "inform",
    "brief", "tell", "let", "do", "produce", "specify", "define", "develop", "implement", "run", "fill", "sign",
    "submit", "double-check", "invite", "visit", "survey", "interview", "plan", "look", "get", "give", "pass",
    "note", "record", "list", "circulate", "agree", "propose", "suggest", "source", "contribute", "negotiate",
    "construct", "redo", "revise", "rework", "change", "keep", "decide", "present", "show", "explain", "discuss",
    "think", "go", "demonstrate", "draw", "jot", "type", "keep", "search", "google", "map", "outline", "consult",
}
# After "we" / "let's" / "someone should", only verbs of work clearly done after the meeting count: "we have to make
# the buttons bigger" is talk about the product, "we need to check the costs" is a task.
GROUP_VERBS = {
    "send", "email", "e-mail", "mail", "forward", "circulate", "share", "post", "upload", "check", "verify", "confirm",
    "research", "investigate", "find", "look", "contact", "call", "ring", "phone", "ask", "schedule", "book", "arrange",
    "organise", "organize", "follow", "chase", "prepare", "write", "draft", "report", "review", "update", "finalise",
    "finalize", "submit", "fill", "buy", "order", "print", "test", "compare", "estimate", "price", "cost", "measure",
    "survey", "interview", "visit", "brief", "inform", "notify", "remind", "meet", "talk", "speak", "get", "decide",
    "evaluate", "summarise", "summarize", "compile", "collect", "gather", "source", "negotiate", "consult", "run",
}

# These are also done right now in the meeting ("I'll show you", "let's discuss"): a task only with a later time.
FUTURE_ONLY = {"present", "show", "explain", "discuss", "think", "go", "demonstrate", "decide", "look", "talk",
               "agree", "keep", "change", "tell", "pass", "let", "give", "take", "do", "get", "come", "put"}
# ... except in these combinations, which are tasks on their own.
PHRASAL_TASKS = re.compile(
    r"^(?:look (?:into|up|for)|take care of|take (?:a|the) (?:look|lead)|take on|take notes|take that|"
    r"come up with|put together|get (?:back|in touch|hold|a quote|quotes|some|the|a|an|prices|details|it)|"
    r"go (?:and|ahead and|get|find|check|ask)|do (?:a|an|the|some|my|our|his|her|their|this|proper|more|"
    r"research|that research)|let (?:him|her|them|\w+) know|tell (?:him|her|them)|talk to|"
    r"put (?:it|that|them|together|in)|give (?:him|her|them)|pass (?:it|that|this|on)|keep (?:an eye|track))\b")
FUTURE = re.compile(r"\b(later|afterwards|after the meeting|after this|next time|for next time|before the next|"
                    r"by next|for the next|at the next|next meeting|offline|tonight|tomorrow|next week|"
                    r"before we meet|when we meet|for our next|by the time)\b")
NOT_OBJECTS = re.compile(r"^(?:do (?:that|it|this|so)\b|do\s*$|take (?:it|that) (?:as|into)|get (?:it|that|this)\b(?! done| sorted| to))")

# Short answers that take on a task said just before.
STRONG_ACCEPT = re.compile(
    r"\b(?:i can do (?:that|it|this|those)|i'll do (?:that|it|this|those)|i will do (?:that|it)|will do|"
    r"i'll take (?:that|it|this|care of (?:it|that))|i can take (?:that|it|this)|leave it (?:with|to) me|"
    r"i'm on it|i'll handle (?:that|it)|i'll sort (?:that|it) out|i can manage that|i'll get on (?:it|that)|"
    r"consider it done|i'll look into (?:it|that)|i can look into (?:it|that)|let me do (?:that|it)|"
    r"i'll have a go|i'll give it a go|(?:sure|yes|yeah|okay|yep),? i (?:can|will|'ll)\b|happy to)\b")
WEAK_ACCEPT = re.compile(r"^(?:yeah|yes|yep|yup|sure|okay|alright|all right|right|of course|absolutely|"
                         r"definitely|no problem|fine|sounds good|great|cool)(?:[ ,.!]+(?:yeah|yes|sure|okay|"
                         r"no problem|of course|will do|i can|i will|thanks))*[.!]?$")
# A task said only as "that" / "it": the answer to the task just before, not a task of its own.
ANAPHORIC = re.compile(r"^(?:take|do|handle|sort|manage|look into|look at|get on|have a go at|deal with)\s+(?:that|it|this|those)\b")
# The task is given to someone named or meant in the line: "put that on Tom", "add it to his list", "he can ask".
GIVE_TO = re.compile(r"^(?:put|add|give|leave|pass|hand)\b")
ASK_TO = re.compile(r"^(?:ask|tell|get|have)\s+(\w+(?:\s+\w+)?)\s+to\b")   # "(let's) ask Priya to review ..."
PRONOUN_OWNER = re.compile(r"\b(?:his|her) (?:list|job|bit|task|plate|area|side)\b|"
                           r"^(?:put|give|leave|pass|add|hand) (?:it|that|this|those)? ?(?:on|to|with|over to) (?:him|her)\b|"
                           r"\b(?:he|she)(?:'ll| will| can| could| should| needs to| has to| is going to)\b")
DROP = re.compile(r"\b(?:forget (?:about )?(?:that|it|the)|scratch that|never mind|don't bother|no need to|"
                  r"we don't need to|you don't need to|cancel (?:that|it|the)|drop (?:that|it|the)|"
                  r"skip (?:that|it|the)|not necessary|let's not do|leave (?:that|it) for now)\b")

STOP = set("""a an the and or but so to of in on at for with from by about into onto over up out off this that these
those it its i i'll i'm i've i'd you you'll your we we'll our us he him his she her they them their there here
is are was were be been being have has had do does did will would can could should shall may might must just
also then now very really quite some any all each every more most other such own same than too only not no yes
yeah okay right well like um uh mm hmm actually maybe probably sure thing things stuff bit lot let's let what
which who when where why how if as go going get got make sort kind""".split())


def stem(w):
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def content(t):
    return {stem(w) for w in words_of(t) if w not in STOP and len(w) > 2}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


# ================= people =================

@dataclass
class Who:
    name: str           # display name, what the owner field gets
    mentions: tuple     # lowercase words that name them in speech
    present: bool = True


def as_people(people):
    """Display names ("Priya Raman") or objects with .name and .mentions (6_evaluation data.Person)."""
    out = []
    for p in people or []:
        if isinstance(p, str):
            first = p.split()[0].lower().strip(",")
            out.append(Who(p, tuple(dict.fromkeys([p.lower(), first]))))
        else:
            out.append(Who(p.name, tuple(getattr(p, "mentions", ()) or (p.name.lower(),)), getattr(p, "present", True)))
    return out


def mention_pattern(m):
    return r"\b" + re.escape(m).replace(r"\ ", r"\s+") + r"\b"


def mentioned(t, people, before=None):
    """[(position, person)] for every person named in the cleaned text (up to `before`)."""
    found = []
    for p in people:
        for m in p.mentions:
            for hit in re.finditer(mention_pattern(m), t[:before] if before else t):
                found.append((hit.start(), p))
    return sorted(found, key=lambda x: x[0])


# ================= candidates =================

@dataclass
class Item:
    index: int                 # line index where it was said first
    kind: str
    speaker: str               # who said it
    owner: str                 # display name, "" = unassigned
    text: str
    due_text: str
    due: str                   # due key (due.py)
    lines: list = field(default_factory=list)
    words: set = field(default_factory=set)
    owner_fixed: bool = False  # named or said by the owner (not just "someone")


def task_after(t, end, assigned=False, verbs=TASK_VERBS):
    """If a task verb follows position `end` (allowing a few small words), the task text from that verb on.
    assigned: someone was named for it ("Rafael, you'll look at ..."), so in-meeting verbs count too."""
    rest = words_of(t[end:])
    for k, w in enumerate(rest[: MAX_SKIP + 1]):
        if w in verbs:
            phrase = " ".join(rest[k:])
            if NOT_OBJECTS.search(phrase):
                return None
            if w in FUTURE_ONLY and not assigned and not PHRASAL_TASKS.search(phrase) and not FUTURE.search(t) \
                    and not find_due(t)[1]:
                return None
            return phrase
        if w not in SKIP:
            return None
    return None


def first_trigger(t, people, speaker):
    """(kind, owner or None, task text) for the first trigger in the line that is followed by a task verb."""
    t_scan = NOT_TASKS.sub(lambda m: "#" * len(m.group(0)), t)   # keep positions, hide the non-task phrases
    hits = []
    for pattern, kind in TRIGGERS:
        for m in pattern.finditer(t_scan):
            hits.append((m.start(), m.end(), kind, None))
    for p in people:   # third person: "<name> will ...", "ask <name> to ..."
        for name in p.mentions:
            for m in re.finditer(mention_pattern(name) + r"\s+" + THIRD_PERSON + r"\b", t_scan):
                hits.append((m.start(), m.end(), "third_person", p.name))
            for m in re.finditer(r"\b(?:ask|tell|get|have)\s+" + mention_pattern(name) + r"\s+to\b", t_scan):
                hits.append((m.start(), m.end(), "third_person", p.name))
            for m in re.finditer(mention_pattern(name) + r"\s+(" + HOLDS + r")\b", t_scan):
                hits.append((m.start(), m.start(1), "holds", p.name))
            m = re.match(mention_pattern(name) + r"\s*,\s*", t_scan)   # "Nadia, finish the display ..."
            if m and p.name != speaker:
                hits.append((m.start(), m.end(), "request", p.name))
    for start, end, kind, owner in sorted(hits, key=lambda h: h[0]):
        if kind == "holds":   # the verb itself is the task verb: "gets the usage numbers"
            phrase = " ".join(words_of(t_scan[end:]))
            if len(content(" ".join(phrase.split()[1:]))) >= 2:
                return "third_person", owner, phrase
            continue
        if kind == "request" and owner is None:   # the person named in the line, if any
            named = [p for _, p in mentioned(t, people) if p.name != speaker]
            owner = named[-1].name if named else None
        assigned = (owner is not None and kind in ("request", "third_person")) or bool(FUTURE_YOU.search(t_scan[:end]))
        phrase = task_after(t_scan, end, assigned=assigned, verbs=GROUP_VERBS if kind == "group" else TASK_VERBS)
        if phrase:
            return kind, owner, phrase
    return None


TRAILING = re.compile(r"(?:[\s,]+(?:okay|ok|right|yeah|alright|then|please|as well|too|you know|or something|"
                      r"and stuff|and so on|i guess|i think))+[\s,.?!]*$", re.IGNORECASE)


def tidy(phrase, line_text, min_words, until="line"):
    """The item's text: from the task verb to the end of the line (until="line") or of its sentence ("sentence"),
    as it was said (original case), without fillers and tags like "okay?". Shorter than min_words: the whole line."""
    said = " ".join(FILLERS.sub(" ", line_text or "").split())
    words = phrase.split()[:3]
    said_words = r"[\s,]+".join(rf"{re.escape(w)}(?:[\s,]+{re.escape(w)})*" for w in words)   # "the the" said twice
    hit = re.search(r"\b" + said_words + r"\b", said, re.IGNORECASE) if words else None
    if hit:
        rest = said[hit.start():]
        end = re.search(r"[.?!](?:\s|$)", rest) if until == "sentence" else None
        if end and len(content(rest[:end.start()])) < 4:   # "Can you find out for sure? Whether the vendors ...":
            end = None                                     # the task is in the next sentence, keep the turn
        text = TRAILING.sub("", (rest[:end.start()] if end else rest).rstrip(" .?!")).strip(" ,;:-")
    else:
        text = TRAILING.sub("", phrase).strip()
    if len(text.split()) < min_words:
        text = said
    text = re.sub(r"\b([\w']+)(?:[\s,]+\1\b)+", r"\1", text, flags=re.IGNORECASE)   # "the the deck" -> "the deck"
    return text[:1].upper() + text[1:]


def owner_from_answers(lines, i, speaker, lookahead, unnamed_you):
    """(name, line id) of someone else who takes the task in the next lines ("Sure, I can do that"), or None.
    unnamed_you: a request to "you" with no name. Then a plain "yeah" counts as taking it, and if nobody answers
    like that, the next other person to speak (within 2 lines) is taken as the one asked (dev: owner accuracy
    0.47 -> 0.60 on AMI, unchanged on synthetic)."""
    for j in range(i + 1, min(len(lines), i + 1 + lookahead)):
        other = lines[j]
        if other["speaker"] == speaker:
            continue
        t = clean(other["text"])
        if STRONG_ACCEPT.search(t) or (unnamed_you and WEAK_ACCEPT.match(t)):
            return other["speaker"], other["id"]
        if len(words_of(t)) > 12:   # a long turn moved the conversation on
            break
    if unnamed_you:
        for j in range(i + 1, min(len(lines), i + 3)):
            if lines[j]["speaker"] != speaker:
                return lines[j]["speaker"], lines[j]["id"]
    return None


def fallback_owner(t, people, speaker):
    """Owner for a line chosen without a rule trigger (the zero-shot baseline): 'I' -> the speaker, a name -> that
    person, 'we' / 'let's' -> nobody, 'you' -> a request (named person or who answers), else nobody."""
    named = [p for _, p in mentioned(t, people) if p.name != speaker]
    if re.search(r"\b(?:i|i'll|i'm|i've|i'd|me|my|mine|myself)\b", t):
        return "first_person", speaker
    if named:
        return "third_person", named[-1].name
    if re.search(r"\b(?:you|you'll|your)\b", t):
        return "request", None
    return "group", ""


def meant_person(lines, i, people, speaker, back=6):
    """Who 'he' / 'she' / 'his list' means: the person named most recently in this or the last few lines (not the
    speaker); else the one participant who is absent, if exactly one is."""
    for j in range(i, max(-1, i - back - 1), -1):
        named = [p for _, p in mentioned(clean(lines[j]["text"]), people) if p.name != speaker]
        if named:
            return named[-1].name
    absent = [p for p in people if not p.present]
    return absent[0].name if len(absent) == 1 else None


def item_for_line(lines, i, people, cfg, items, require_trigger=True, memo=None):
    """The action item said in line i, or None. require_trigger=False (zero-shot): the line was already chosen as a
    task, so if no rule fires, the whole line is the task and fallback_owner() names the owner.
    items: the items so far (an "I'll take that" right after one is not a new task). memo: a dict kept by the caller
    for one meeting, so each line's rules run once however many thresholds are tried (tune.py)."""
    if memo is None:
        base, anaphoric = line_item(lines, i, people, cfg, require_trigger)
    else:
        if (i, require_trigger) not in memo:
            memo[(i, require_trigger)] = line_item(lines, i, people, cfg, require_trigger)
        base, anaphoric = memo[(i, require_trigger)]
    if base is None or (anaphoric and items and i - items[-1].index <= cfg["lookahead_lines"]):
        return None   # "I'll take that" / "I can do that": the task before it changes owner, no new task
    return replace(base, lines=list(base.lines), words=set(base.words))


def line_item(lines, i, people, cfg, require_trigger):
    """(the item line i says, or None; whether its task is only "that"/"it"), from line i and the lines after it."""
    line = lines[i]
    anaphoric = False
    t, speaker = clean(line["text"]), line["speaker"]
    found = first_trigger(t, people, speaker)
    if not found:
        if require_trigger:
            return None, False
        kind, owner = fallback_owner(t, people, speaker)
        phrase = ""
    else:
        kind, owner, phrase = found
        anaphoric = bool(ANAPHORIC.match(phrase))
    fixed = True
    given = [p for _, p in mentioned(phrase, people) if p.name != speaker] if GIVE_TO.match(phrase) else []
    asked = ASK_TO.match(phrase)
    if asked:
        given = [p for _, p in mentioned(asked.group(1), people) if p.name != speaker] or given
    if given:   # "put that on Tom", "add it to Priya's list", "ask Priya to ..."
        kind, owner = "third_person", given[0].name
    elif PRONOUN_OWNER.search(phrase) or (PRONOUN_OWNER.search(t) and kind != "first_person"):
        meant = meant_person(lines, i, people, speaker)
        if meant:
            kind, owner = "third_person", meant
    if kind == "first_person":
        owner = speaker
    elif kind == "group":
        owner, fixed = "", False
    elif owner is None:   # a request to "you" with no name in the line
        owner, fixed = "", False
    due_text, due = find_due(line["text"])
    item = Item(i, kind, speaker, owner, tidy(phrase, line["text"], cfg["min_text_words"], cfg.get("text_until", "line")),
                due_text, due,
                [line["id"]], content(phrase or t), fixed)
    # Someone else takes it in the next lines: an answer to a request, an offer taken by another person,
    # or "I'll do it" after "we need to ...".
    weak = kind == "request" and not fixed
    answer = owner_from_answers(lines, i, speaker, cfg["lookahead_lines"], weak)
    offer = kind == "first_person" and re.search(r"\b(?:should i|shall i|want me to|i could|i might|i can)\b", t)
    if answer and (not fixed or offer):
        item.owner, item.owner_fixed = answer[0], True
        item.lines.append(answer[1])
    elif kind == "request" and fixed:   # "Priya, can you ...?" / "Sure, by tomorrow.": the answer's due date
        reply = owner_from_answers(lines, i, speaker, cfg["lookahead_lines"], True)
        if reply and reply[0] == owner:
            answer = reply
    if answer and not item.due:
        ans = next(l for l in lines if l["id"] == answer[1])
        item.due_text, item.due = find_due(ans["text"])
    for j in range(i + 1, min(len(lines), i + 1 + cfg["lookahead_lines"])):   # someone else takes it over
        other = lines[j]
        if other["speaker"] not in (speaker, item.owner) and TAKEOVER.search(clean(other["text"])):
            item.owner, item.owner_fixed = other["speaker"], True
            item.lines.append(other["id"])
            break
    return item, anaphoric


def candidates(lines, people, cfg):
    items = []
    for i, line in enumerate(lines):
        t = clean(line["text"])
        if DROP.search(t) and items:
            drop(items, t, i)
        item = item_for_line(lines, i, people, cfg, items)
        if item:
            items.append(item)
    return items


def drop(items, t, i):
    """'Forget that' / 'no need to ...': remove the earlier task it is about (same words, or the last one said)."""
    words = content(t)
    best = max(items, key=lambda it: jaccard(words, it.words))
    if jaccard(words, best.words) >= 0.2:
        items.remove(best)
    elif re.search(r"\b(?:that|it)\b", t) and i - items[-1].index <= 4:
        items.pop()


# ================= 6. merge =================

def merge(items, cfg):
    """One item per task: the owner answering close by, or content words overlapping by merge_jaccard.
    Each item is compared with the items just before it and with those sharing a word (a word index keeps this
    fast when the zero-shot classifier picks thousands of lines); the latest one that fits takes it."""
    out, by_word = [], {}
    for it in items:
        near = set()
        k = len(out) - 1
        while k >= 0 and it.index - out[k].index <= cfg["merge_window_lines"]:   # out is in line order
            near.add(k)
            k -= 1
        candidates = near | {k for w in it.words for k in by_word.get(w, ())}
        same = None
        for k in sorted(candidates, reverse=True):
            prev = out[k]
            owners_agree = prev.owner == it.owner or not prev.owner or not it.owner
            overlap = jaccard(prev.words, it.words)
            # Said twice in a row: "Tomasz, you'll do the interface design?" / "Yeah, I'll write out the commands."
            # (the owner answering right away, or a little word overlap). Far apart: only near-identical words.
            reply = it.kind == "first_person" and it.owner == prev.owner and prev.speaker != it.speaker
            close = it.index - prev.index <= cfg["merge_window_lines"] and (reply or overlap >= 0.15)
            if owners_agree and (close or overlap >= cfg["merge_jaccard"]):
                same = k
                break
        if same is None:
            out.append(it)
            for w in it.words:
                by_word.setdefault(w, set()).add(len(out) - 1)
            continue
        prev = out[same]
        if it.owner and (not prev.owner or (it.owner_fixed and not prev.owner_fixed) or it.index > prev.index):
            prev.owner, prev.owner_fixed = it.owner, prev.owner_fixed or it.owner_fixed
        if len(it.words) > len(prev.words):   # keep the fuller wording
            prev.text = it.text
        prev.words |= it.words
        for w in it.words:
            by_word.setdefault(w, set()).add(same)
        prev.lines += [l for l in it.lines if l not in prev.lines]
        if not prev.due:
            prev.due_text, prev.due = it.due_text, it.due
    return out


# ================= the baseline =================

def to_contract(item, n, meeting_date):
    return {"id": f"a{n}", "text": item.text, "owner": item.owner, "due": resolve(item.due, meeting_date),
            "due_text": item.due_text, "status": "pending", "source_line_ids": item.lines, "version": 1,
            "kind": item.kind}


def extract(lines, people, meeting_date=None, cfg=None):
    """Action items (UI contract format) for transcript lines [{"id", "speaker", "start", "text"}, ...]."""
    import settings
    cfg = cfg or settings.RULES
    items = merge(candidates(lines, as_people(people), cfg), cfg)
    return [to_contract(it, n, meeting_date) for n, it in enumerate(items, 1)]


class RuleBaseline:
    name = "rules"

    def __init__(self, cfg=None):
        import settings
        self.cfg = dict(cfg or settings.RULES)
        self.version = "rules: 3_action_items/rules.py (no model)"

    def predict(self, meeting):
        return extract(meeting.lines, meeting.people, None, self.cfg)
