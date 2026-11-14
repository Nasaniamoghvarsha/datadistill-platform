"""Names from what people say: self-introductions ("my name is ...", "this is ... from ...", "you know me as ...").

Works with cameras off and in any layout. Only the beginning of a speaker's turn is used (where people
introduce themselves), and only capitalised name-like words that are not common words.
"""
import re
from collections import Counter, defaultdict

from rapidfuzz import fuzz

from . import namefold

NAME = r"((?:[A-Z][a-z'’]+)(?:[ -][A-Z][a-z'’]+){0,2})"
# trigger phrases are case-insensitive; the NAME itself must be capitalised (ASR capitalises names)
PATTERNS = [
    re.compile(rf"\b(?i:my name(?: is|'s))\s+{NAME}"),
    re.compile(rf"\b(?i:know me as|call me)\s+{NAME}"),
    re.compile(rf"\b(?i:this is|i'm|i am)\s+{NAME}(?=\s*(?:,|\.|$|(?i:from|with|at|here|speaking|and|the|for|i'm|i am|so|um|uh)\b))"),
    re.compile(rf"^(?i:hi|hello|good (?:morning|afternoon|evening))?[,.\s]*{NAME}\s+(?i:here)\b"),
]
HERE_PATTERN = 3     # "Sam here": only trusted at the start of a turn or after a greeting
STRONG_PATTERNS = {0, 1}   # "my name is X", "call me X": said about oneself. "this is X" / "I'm X" also introduce
STRONG_WEIGHT = 3          # someone else ("So this is Erik" while showing him on screen): they weigh 1
# Speech recognition often writes quick self-introductions in lower case ("uh this is sarah i'll jump in").
# A lower-case name is only accepted in a strong introduction pattern.
# ("it's X" is not used: "it's taboo", "it's fine" are far more common than "it's Tom")
LOWER_PATTERNS = [
    re.compile(r"\bthis is\s+([a-z]{3,})(?=[,.\s]+(?:i'll|i will|i can|i'm|i am|i just|i want|i'd|i think|"
               r"jumping|speaking|here|from|with)\b)", re.I),
    re.compile(r"^(?:uh|um|hi|hey|yeah|yes|so)?[,.\s]*this is\s+([a-z]{3,})[.!]?$", re.I),  # whole turn: "this is tom"
    re.compile(r"\b(?:hey|hi|hello)[,\s]+(?:(?:uh|um|everyone|everybody|all)[,\s]+)*([a-z]{3,})\s+here\b", re.I),  # "hey uh fernando here"
]
WHOLE_TURN = 1  # index of the whole-segment pattern in LOWER_PATTERNS
# the strongest phrases also accept a lower-case first + last name ("y'all know me as tanya carter")
STRONG_LOWER = re.compile(r"\b(?:my name is|my name's|know me as)\s+([a-z]{2,}(?:\s+[a-z'’]{2,})?)", re.I)
COMMON = set("""I Im OK Okay Yes Yeah No Hi Hello Thanks Thank So And But The This That It Its We You They Sure Just Good Great
Fine Well Here There Now Then Today Tomorrow Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February
March April May June July August September October November December Board Chair Committee Council Team Everyone
All Happy Glad Sorry Not Going Still Also Really Actually Ready Back Done New Next Last First One Two Three Mr Mrs Ms Dr
Commissioner Senator Representative Director Manager Doctor Officer President Vice Item Agenda Public Comment Motion""".split())


COMMON_LOWER = {w.lower() for w in COMMON}


CUT = {"im", "ill", "ive", "id", "i"}  # "Alima Bucintini I'm from Maine": the name ends before "I'm"


def _clean(name):
    toks = []
    for t in name.split():
        if re.sub(r"['’]", "", t).lower() in CUT:
            break
        toks.append(t)
    name = " ".join(toks)
    if not name or re.search(r"['’]", name):  # contractions ("It's", "We'll") are never names here
        return None
    toks = name.split()
    while toks and toks[-1] in COMMON:
        toks.pop()
    while toks and toks[0] in COMMON:
        toks.pop(0)
    if not toks or any(t in COMMON or t.lower() in FUNCTION_WORDS for t in toks):
        return None
    if not all(t[:1].isupper() for t in toks):
        return None
    return " ".join(toks)


def find_intros(turns, first_words=18, events=None):
    """{speaker_id: Counter(name)} from self-introductions at the start of turns.
    A turn is read together with the same speaker's next segment: speech recognition sometimes splits
    an introduction ("...please call me" | "Katie.")."""
    out = defaultdict(Counter)
    for i, t in enumerate(turns):
        if not t.get("speaker"):
            continue
        words = t["text"].split()[:first_words]
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        if nxt and nxt.get("speaker") == t["speaker"] and nxt.get("start", 0) - t.get("end", 0) < 2.0 and len(words) < first_words + 1:
            words += nxt["text"].split()[:6]
        head = " ".join(words)
        prev = turns[i - 1] if i > 0 else None
        turn_start = not prev or prev.get("speaker") != t["speaker"] or t.get("start", 0) - prev.get("end", 0) > 2.0
        greeting = re.match(r"(?i)\s*(?:hi|hello|hey|good (?:morning|afternoon|evening))\b", head)
        for k, pat in enumerate(PATTERNS):
            if k == HERE_PATTERN and not (turn_start or greeting):
                continue  # "...Gap Closure | Project here": only at the start of someone's turn or after a greeting
            for m in pat.finditer(head):
                n = _clean(m.group(1))
                if n:
                    out[t["speaker"]][n] += STRONG_WEIGHT if k in STRONG_PATTERNS else 1
                    if events is not None:
                        # said about oneself: "my name is X", "call me X", "I'm X", "X here", or a whole turn "This is X."
                        # ("this is X" inside a longer turn may introduce someone else: weak)
                        trigger = head[m.start():m.start(1)].lower()
                        first = (k in STRONG_PATTERNS or k == HERE_PATTERN or bool(re.search(r"\b(?:i'?m|i am)\b", trigger))
                                 or len(t["text"].split()) <= 5)
                        events.append({"speaker": t["speaker"], "name": n, "first_person": first,
                                       "start": t.get("start", 0), "end": t.get("end", 0)})
        for m in STRONG_LOWER.finditer(head):
            if m.group(1)[:1].isupper():
                continue  # capitalised names are handled by PATTERNS
            toks = []
            for w in m.group(1).split():
                lw = re.sub(r"['’]", "", w.lower())
                if lw in CUT or lw in FUNCTION_WORDS or lw in COMMON_LOWER or lw in NOT_NAMES:
                    break
                toks.append(w.capitalize())
            if toks:
                out[t["speaker"]][" ".join(toks)] += STRONG_WEIGHT
                if events is not None:
                    events.append({"speaker": t["speaker"], "name": " ".join(toks), "first_person": True,
                                   "start": t.get("start", 0), "end": t.get("end", 0)})
        for k, pat in enumerate(LOWER_PATTERNS):
            text = t["text"].strip() if k == WHOLE_TURN else head.strip()  # "this is tom" = the whole segment
            for m in pat.finditer(text):
                w = m.group(1)
                if w.lower() not in COMMON_LOWER and w.lower() not in NOT_NAMES and w.lower() not in FUNCTION_WORDS:
                    out[t["speaker"]][w.capitalize()] += 1
                    if events is not None:
                        events.append({"speaker": t["speaker"], "name": w.capitalize(), "first_person": k in (WHOLE_TURN, 2),
                                       "start": t.get("start", 0), "end": t.get("end", 0)})
    return out


# ordinary English words that a pattern can catch at the start of a sentence ("If I'm here to draft it")
FUNCTION_WORDS = set("""if so and but or because when while what where who whom whose how why that this these those there then
than as at in on of to for with from by about into over under after before just also maybe perhaps well now here yes no
not all both each every some any we you they he she it i me my mine our your their his her its us them be is are was were
been being do does did done have has had can could will would should shall may might must let lets ok okay um uh oh hey
hi hello thanks thank please sorry actually really basically anyway though although unless until since once again still
even only very too much many more most less few other another such same next last first one two
quite rather pretty fairly extremely highly truly simply totally completely absolutely definitely certainly probably
possibly surely exactly almost nearly already always never often sometimes usually mostly really indeed especially
particularly literally obviously clearly honestly frankly generally typically essentially entirely fully mainly
merely partly slightly somewhat somehow somewhere anywhere everywhere nowhere""".split())


NOT_NAMES = set("""something everything nothing anything important interesting really great good fine right wrong true
correct going fun hard easy just what that this which where when how why going sure okay alright about also why the
part item one two three four five six seven eight nine ten first second last next meeting call board team""".split())


HANDOVER = (r"(?i)\b{name}\b(?=\s*(?:\?|,|$|\s(?:do you|did you|would you|can you|could you|anything|any|"
            r"you have|go ahead|please|what do you|want to|wants to|or any)))")
THANKS = re.compile(r"(?:^|[.?!]\s+|,\s+)(?i:(?:ok(?:ay)?|all right|great|yeah|yes|so|well|um|uh)[,.\s]+)*"
                    r"(?i:thank you|thanks|was that)\s*,?\s+([A-Za-z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)?)")
THANKS_WORDS = 25    # "thank you, X" / "was that X?" near the start of a turn refers to whoever just spoke


def resolve_known(said, known_names):
    """A spoken name -> the known full name it refers to, or None. 'Lisa' -> 'Lisa Thomas' (if only one Lisa),
    'John Coleman' -> 'Jonathan Colman' (surname nearly equal, first name starts the same)."""
    toks = [t for t in re.split(r"\s+", said.strip()) if t]
    if not toks:
        return None
    s = [namefold.fold(t).strip("'’") for t in toks]
    for kn in known_names:
        if namefold.fold(kn) == " ".join(s):
            return kn
    if len(s) == 1:
        hits = [kn for kn in known_names if namefold.fold(kn.split()[0]) == s[0]]
        if len(hits) == 1 and len(s[0]) >= 3:
            return hits[0]
        return namefold.match_known(toks[0], known_names) if len(s[0]) >= 5 else None
    hits = []
    for kn in known_names:
        k = namefold.fold(kn).split()
        if len(k) >= 2 and fuzz.ratio(k[-1], s[-1]) >= 85 and k[0][:1] == s[0][:1]:   # John Coleman ~ Jonathan Colman
            hits.append(kn)
    return hits[0] if len(hits) == 1 else None


def find_addressed(turns, known_names, window=12, gap_s=4.0, events=None):
    """{speaker_id: Counter(name)} from people being addressed by name.
    "…Lisa, turning it back to you." / "Caitlin, anything else?" -> the NEXT other speaker is probably that person;
    "Thank you, Jonathan." / "Was that John Coleman?" -> the PREVIOUS other speaker is probably that person.
    Only names already known (tiles, documents, introductions); a first name shared by two known people only counts
    when the full name is said."""
    first = defaultdict(list)
    for kn in known_names:
        if kn.split():
            first[kn.split()[0].lower()].append(kn)
    forms = []                                         # (regex-ready form, full name)
    for kn in known_names:
        forms.append((re.escape(kn), kn))
        f = kn.split()[0]
        if len(first[f.lower()]) == 1 and len(f) >= 3:
            forms.append((re.escape(f), kn))
    out = defaultdict(Counter)
    # runs: consecutive lines of one speaker read as one turn (a handover often sits a line before the end)
    runs = []
    for t in turns:
        if not t.get("speaker"):
            continue
        if runs and runs[-1]["speaker"] == t["speaker"]:
            runs[-1]["text"] += " " + t["text"]
            runs[-1]["end"] = t["end"]
        else:
            runs.append({"speaker": t["speaker"], "text": t["text"], "start": t["start"], "end": t["end"]})
    for i, r in enumerate(runs):
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        if nxt and nxt["start"] - r["end"] <= gap_s:
            tail = " ".join(r["text"].split()[-window:])
            for form, kn in forms:
                if re.search(HANDOVER.replace("{name}", form), tail):
                    out[nxt["speaker"]][kn] += 1
                    if events is not None:
                        events.append({"speaker": nxt["speaker"], "name": kn, "start": nxt["start"], "end": nxt["start"] + 2.0})
                    break
        prv = runs[i - 1] if i > 0 else None
        if prv and r["start"] - prv["end"] <= gap_s:
            m = THANKS.search(" ".join(r["text"].split()[:THANKS_WORDS]))
            kn = resolve_known(m.group(1), known_names) if m else None
            if not kn and m and len(m.group(1).split()) == 2:
                kn = resolve_known(m.group(1).split()[0], known_names)
            if kn:
                out[prv["speaker"]][kn] += 1
                if events is not None:
                    events.append({"speaker": prv["speaker"], "name": kn, "start": prv["end"] - 2.0, "end": prv["end"]})
    return out


_SELF_BEFORE = re.compile(r"(?i)\b(?:i'?m|i am|my name is|name'?s|this is|it'?s|me,?)\s*$")


def addressed_by(turns, known_names):
    """{speaker_id: Counter(name)}: names a voice itself says when calling on or thanking someone ("Jonathan, maybe
    you want to pick up on that", "Thank you, Lisa"). Whoever calls a person by name is not that person, so this
    vetoes a weak name match. Self-mentions ("I'm Jonathan,", "this is Jonathan,") do not count."""
    first = defaultdict(list)
    for kn in known_names:
        if kn.split():
            first[kn.split()[0].lower()].append(kn)
    forms = []
    for kn in known_names:
        forms.append((re.escape(kn), kn))
        f = kn.split()[0]
        if len(first[f.lower()]) == 1 and len(f) >= 3:
            forms.append((re.escape(f), kn))
    out = defaultdict(Counter)
    for t in turns:
        spk, text = t.get("speaker"), t.get("text") or ""
        if not spk:
            continue
        for form, kn in forms:
            for m in re.finditer(r"(?i)\b" + form + r"\b(?=\s*[,?])", text):
                if not _SELF_BEFORE.search(text[:m.start()]):
                    out[spk][kn] += 1
        m = THANKS.search(" ".join(text.split()[:THANKS_WORDS]))
        kn = resolve_known(m.group(1), known_names) if m else None
        if kn:
            out[spk][kn] += 1
    return out


def assign_addressed(addressed, current, min_votes=2, min_share=0.6, override_votes=3, override_share=0.75):
    """{speaker_id: name} from address votes. current: {speaker_id: (name or None, source)}.
    Unnamed speakers get a name when the votes clearly agree. A name matched from tile movement / highlight is
    replaced only by strong evidence (what people call that person outweighs a tiny moving thumbnail); names
    people said themselves are never replaced."""
    result = {}
    used = {n for n, src in current.values() if n}
    order = sorted(addressed, key=lambda s: -max(addressed[s].values()))
    for sid in order:
        c = addressed[sid]
        name, k = c.most_common(1)[0]
        share = k / sum(c.values())
        cur, src = current.get(sid, (None, None))
        if cur == name:
            continue
        if cur and len(cur.split()) == 1 and name.split()[0].lower() == cur.lower() and name not in used:
            result[sid] = {"name": name, "evidence": k, "of": sum(c.values()), "replaced": cur, "completed": True}
            used.add(name)                       # "Jason" (said it himself) -> "Jason Hayes" (as others call him)
            continue
        if cur is None:
            ok = k >= min_votes and share >= min_share
        else:
            ok = (src or "").startswith("screen") and k >= override_votes and share >= override_share
        owner = next((s for s, (n, _) in current.items() if n == name and s != sid), None)
        if ok and (name not in used or (owner and (current[owner][1] or "").startswith("screen")
                                        and max(addressed.get(owner, Counter()).get(name, 0), 0) == 0)):
            result[sid] = {"name": name, "evidence": k, "of": sum(c.values()), "replaced": cur}
            used.add(name)
            if owner and owner not in result:        # the other voice had this name only from the screen
                result[owner] = {"name": None, "evidence": 0, "of": 0, "replaced": name}
    return result


def assign(intros, known_names):
    """Pick one name per speaker; a name goes to the speaker who says it most. Full names from the
    screen/documents are preferred over a first name alone ('Tanya' -> 'Tanya Carter' if known).
    A speaker whose two best names weigh the same stays unnamed: a guess between two names is often wrong."""
    claims = []
    for sid, c in intros.items():
        top = c.most_common(2)
        if len(top) == 2 and top[0][1] == top[1][1]:
            continue
        for n, k in c.items():
            full = next((kn for kn in known_names if kn.lower() == n.lower()), None)
            if not full and len(n.split()) == 1:
                fs = [kn for kn in known_names if kn.split()[0].lower() == n.lower()]
                full = fs[0] if len(fs) == 1 else None
            if not full:
                full = next((kn for kn in known_names if fuzz.token_sort_ratio(kn, n) >= 88), None)
            if not full:
                full = namefold.match_known(n, known_names) or n        # "Thuvjorn" -> "Torbjörn Bergström" (spelling on the tile)
            claims.append((k, sid, full))
    result, used = {}, set()
    for k, sid, name in sorted(claims, reverse=True):
        if sid in result or name in used:
            continue
        result[sid] = {"name": name, "evidence": k}
        used.add(name)
    return result


def complete_name(n, known_names):
    """A heard name as the full name the participant list has ("Marco" -> "Marco Ellison"), else as heard."""
    full = next((kn for kn in known_names if kn.lower() == n.lower()), None)
    if not full and len(n.split()) == 1:
        fs = [kn for kn in known_names if kn.split()[0].lower() == n.lower()]
        full = fs[0] if len(fs) == 1 else None
    if not full:
        full = next((kn for kn in known_names if fuzz.token_sort_ratio(kn, n) >= 88), None)
    return full or namefold.match_known(n, known_names) or n


def intro_observations(turns, known_names):
    """Evidence from what people say about themselves, one observation per line: {speaker, name, kind, start, end}.
    kind 'self_introduction_first_person' ("my name is X", "I'm X", "X here", a whole turn "This is X.") counts once;
    'self_introduction' (a "this is X" inside a longer turn, which may introduce someone else) needs repeats."""
    ev = []
    find_intros(turns, events=ev)
    out = []
    for e in ev:
        out.append({"speaker": e["speaker"], "name": complete_name(e["name"], known_names),
                    "kind": "self_introduction_first_person" if e["first_person"] else "self_introduction",
                    "start": e["start"], "end": max(e["end"], e["start"] + 1.0)})
    return out


def address_observations(turns, known_names):
    """Evidence from being called by name ("Caitlin, anything else?" / "Thank you, Jonathan"): kind 'addressed'."""
    ev = []
    find_addressed(turns, known_names, events=ev)
    return [{**e, "kind": "addressed"} for e in ev]
