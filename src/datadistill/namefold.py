"""Names across English, Swedish and French spelling, and across speech recognition errors.

Speech recognition has no spelling: "Torbjörn" is heard as "Thuvjorn", "Björn" as "Bjorn", "François" as "Francois".
The name written on the participant's tile is the authority for the spelling. This module
- folds accents and language-specific letters (å ä ö é è ç ø æ ß ...) so "Åsa" = "Asa", "Hélène" = "Helene";
- compares names by sound-alike skeleton (consonants) as well as by letters;
- matches a name heard in speech to the known names (never a guess between two close candidates);
- corrects the spelling of a known name inside transcript lines.
"""
import re
import unicodedata

from rapidfuzz import fuzz

_SPECIAL = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "þ": "th", "ð": "d",
                          "ł": "l", "Ł": "L", "đ": "d", "ı": "i", "’": "'", "‘": "'"})


def fold(s):
    """Lower case without accents: 'Åsa Lindqvist' -> 'asa lindqvist'."""
    s = unicodedata.normalize("NFKD", (s or "").translate(_SPECIAL))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


_SOUND = [("sch", "s"), ("sj", "s"), ("tj", "s"), ("ch", "s"), ("sh", "s"), ("ph", "f"), ("th", "t"), ("ck", "k"),
          ("qu", "k"), ("w", "v"), ("q", "k"), ("x", "ks"), ("z", "s"), ("c", "k"), ("y", "i")]


def skeleton(s):
    """Sound-alike consonant skeleton: 'Torbjörn' -> 'trbjrn', 'Thuvjorn' -> 'tvjrn'; the first letter is kept
    even if it is a vowel, vowels elsewhere are dropped, doubled letters collapse."""
    f = re.sub(r"[^a-z ]", "", fold(s))
    out = []
    for w in f.split():
        for a, b in _SOUND:
            w = w.replace(a, b)
        sk = w[:1] + re.sub(r"[aeiou]", "", w[1:])
        out.append(re.sub(r"(.)\1+", r"\1", sk))
    return " ".join(out)


def similarity(a, b):
    """0..100: how alike two names are by letters (accents ignored) or by sound-alike skeleton."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    letters = fuzz.ratio(fa, fb)
    sa, sb = skeleton(a), skeleton(b)
    sound = fuzz.ratio(sa, sb) if sa[:1] == sb[:1] and min(len(sa), len(sb)) >= 4 else 0.0
    return max(letters, sound * 0.95)


def same_name(a, b, min_score=82):
    return similarity(a, b) >= min_score


# Everyday short forms of first names, Swedish and English: people say "Tobbe", the tile says "Torbjörn". Folded, one form
# per nickname; a nickname only ever stands for the names listed (never guessed between: the caller needs a clear winner).
NICKNAMES = {
    "tobbe": ["torbjorn"], "kalle": ["karl", "carl"], "lasse": ["lars"], "micke": ["mikael", "michael"],
    "mickey": ["michael", "mikael"], "nisse": ["nils"], "pelle": ["per", "peter"], "janne": ["jan", "johan"], "olle": ["olof", "olov"],
    "calle": ["carl", "karl"], "fredde": ["fredrik", "frederik"], "freddie": ["fredrik", "frederick"], "gustaf": ["gustav"],
    "stina": ["kristina", "christina"], "kicki": ["kristina", "christina"], "lotta": ["charlotta", "charlotte"], "lisen": ["elisabeth"],
    "bengt": ["bengt"], "jonte": ["jonathan", "jonas"], "andi": ["andreas"], "andy": ["andreas", "andrew", "anders"],
    "bob": ["robert"], "rob": ["robert"], "bobby": ["robert"], "bill": ["william"], "will": ["william"], "billy": ["william"],
    "jim": ["james"], "jimmy": ["james"], "mike": ["michael"], "dave": ["david"], "tom": ["thomas"], "tommy": ["thomas"],
    "joe": ["joseph"], "tony": ["anthony", "antonio"], "steve": ["stephen", "steven"], "chris": ["christopher", "christian", "kristoffer"],
    "alex": ["alexander", "alexandra"], "sam": ["samuel"], "ben": ["benjamin"], "nick": ["nicholas", "nicklas", "niklas"],
    "dan": ["daniel"], "matt": ["matthew", "mattias"], "pete": ["peter"], "rick": ["richard", "erik"], "dick": ["richard"],
    "liz": ["elizabeth"], "beth": ["elizabeth"], "kate": ["katherine", "katarina", "catherine"], "kathy": ["katherine", "katarina"],
    "sue": ["susan"], "jen": ["jennifer"], "jenny": ["jennifer"], "pat": ["patrick", "patricia"], "ed": ["edward", "edvard"],
    "ted": ["edward", "theodor"], "hank": ["henry", "henrik"], "harry": ["henry", "harald"], "fran": ["francois", "francis", "frances"],
}


def _forms(token):
    """The token as it is, plus the full first names a nickname stands for."""
    f = fold(token)
    return [token] + NICKNAMES.get(f, [])


def match_known(said, known_names, min_letters=80, min_sound=66, margin=12):
    """A name heard in speech -> the one known full name it most likely is, or None.
    Compares the heard word(s) with every first and last name of the known people: by letters (accents ignored,
    >= 80) or, for longer names, by sound-alike skeleton (>= 66, and the letters must still overlap: "Thuvjorn" ~
    "Torbjörn"). Needs a clear winner: the best candidate leads the runner-up by `margin` points, so two similar
    names are never guessed between."""
    toks = [t for t in re.split(r"\s+", (said or "").strip()) if len(t) >= 3]
    toks = [f for t in toks for f in _forms(t)]
    if not toks:
        return None
    best = []
    for kn in known_names:
        score = 0.0
        for t in toks:
            for p in (x for x in kn.split() if len(x) >= 3):
                letters = fuzz.ratio(fold(t), fold(p))
                sound = fuzz.ratio(skeleton(t), skeleton(p)) if len(t) >= 6 and len(p) >= 6 and letters >= 60 else 0.0
                if letters >= min_letters:
                    score = max(score, letters)
                elif sound >= min_sound:
                    score = max(score, sound * 0.95)
        if score:
            best.append((score, kn))
    best.sort(reverse=True)
    if not best:
        return None
    if len(best) > 1 and best[0][0] - best[1][0] < margin:
        return None
    return best[0][1]


_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*")


def correct_spellings(turns, known_names, is_common_word, min_score=76):
    """Rewrite names in transcript lines to the spelling of the known names ("Thuvjorn" -> "Torbjörn").
    Only capitalised words that are not ordinary words of the main language are touched, and only when one known
    first or last name is clearly the closest. Returns the list of fixes ({"from", "to", "count"}); lines get
    `original_text` the first time they change, so nothing is lost."""
    parts = {}
    for kn in known_names:
        for p in kn.split():
            if len(p) >= 4 and p[:1].isupper():
                parts.setdefault(p, kn)
    if not parts:
        return []
    cache, fixes = {}, {}

    def fix(word):
        if word in cache:
            return cache[word]
        res = None
        w = word.strip("'’-")
        if len(w) >= 4 and w[:1].isupper() and w not in parts and not is_common_word(w):
            ranked = sorted(((fuzz.ratio(fold(w), fold(p)), p) for p in parts), reverse=True)
            if ranked and ranked[0][0] >= min_score and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 10):
                res = ranked[0][1]
        cache[word] = res
        return res

    for t in turns:
        text = t.get("text") or ""
        new = _WORD.sub(lambda m: fix(m.group(0)) or m.group(0), text)
        if new != text:
            for m in _WORD.finditer(text):
                r = fix(m.group(0))
                if r:
                    key = (m.group(0), r)
                    fixes[key] = fixes.get(key, 0) + 1
            t.setdefault("original_text", text)
            t["text"] = new
    return [{"from": a, "to": b, "count": n} for (a, b), n in sorted(fixes.items(), key=lambda kv: -kv[1])]
