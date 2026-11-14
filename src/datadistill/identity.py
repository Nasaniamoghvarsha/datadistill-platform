"""Evidence-based naming. Face motion and a static name are never identity proof.

Ported from the friend's branch. Changes made when it was joined to this pipeline (13-14 Nov):
  * a clear first-person introduction ("my name is X", "I'm X", "X here") counts once, like captions: three repeats of
    a sentence nobody says three times would leave almost every meeting anonymous (replayed on the Ericsson recording,
    the original rule named none of the four people who really introduced themselves);
  * 'addressed' (called by name) is accepted evidence but needs repeats, like highlight;
  * a name with too little evidence no longer blocks a name with enough: only two names that BOTH qualify conflict;
  * names that did not qualify are returned as `candidate_name`, so a weak guess can be shown as such, never as a name.
"""
from collections import defaultdict
import math

STRONG = {"captions", "manual", "active_face", "highlight", "self_introduction", "self_introduction_first_person", "addressed"}
AUTHORITATIVE = {"captions", "manual"}
ONCE = {"captions", "manual", "self_introduction_first_person"}     # one observation is enough
MIN_OBSERVATIONS = 3
MIN_EVIDENCE_S = 6.0


def resolve(speaker_ids, observations):
    """Resolve a voice to a name with repeated, non-conflicting evidence.

    Observation: {speaker, name, kind, start, end, track?, score?}.
    Scores are diagnostic model scores, never advertised as calibrated probabilities.
    Same-name observations on different simultaneous tracks are rejected by the
    visual collector. Different voices are not merged merely because names match.
    """
    grouped = defaultdict(list)
    for o in observations:
        if (o.get("speaker") in speaker_ids and o.get("name") and o.get("kind") in STRONG
                and math.isfinite(float(o.get("start", 0))) and math.isfinite(float(o.get("end", 0)))
                and o.get("end", 0) > o.get("start", 0)):
            grouped[o["speaker"]].append(o)
    result = {}
    for sid in speaker_ids:
        evidence = grouped[sid]
        # Authoritative observations (captions, manual) settle it; otherwise every kind of evidence counts.
        direct = [o for o in evidence if o["kind"] in AUTHORITATIVE]
        candidates = defaultdict(list)
        for o in direct or evidence:
            candidates[o["name"]].append(o)
        accepted, weak = [], []
        for name, obs in candidates.items():
            distinct = {(round(o["start"], 1), round(o["end"], 1)) for o in obs}
            # Union intervals so duplicated detectors/overlapping clips don't manufacture evidence.
            stop, seconds = -float("inf"), 0.0
            for a_, b_ in sorted(distinct):
                seconds += max(0, b_ - max(a_, stop))
                stop = max(stop, b_)
            if any(o["kind"] in ONCE for o in obs) or (len(distinct) >= MIN_OBSERVATIONS and seconds >= MIN_EVIDENCE_S):
                accepted.append((name, obs, seconds))
            else:
                weak.append((name, len(distinct), seconds))
        # Two names that both have enough evidence are not settled by a majority guess.
        conflict = len(accepted) > 1
        name, support = (accepted[0][0], accepted[0][1]) if len(accepted) == 1 else (None, evidence)
        candidate = None
        if not name:
            pool = [(n, len({(round(o["start"], 1), round(o["end"], 1)) for o in obs})) for n, obs in candidates.items()]
            candidate = max(pool, key=lambda x: x[1])[0] if pool and not conflict else None
        result[sid] = {"person_id": f"P{sid[1:]}", "name": name,
                       "source": "+".join(sorted({o["kind"] for o in support})) if name else None,
                       "score": None, "identity_status": "conflict" if conflict else "supported" if name else "anonymous",
                       "candidate_name": candidate, "evidence": support}
    # One inferred display name on several voice tracks is a conflict to review,
    # not proof that the tracks are the same person or two people with that name.
    owners = defaultdict(list)
    for sid,item in result.items():
        if item.get('name'):owners[item['name'].casefold()].append(sid)
    for ids in owners.values():
        if len(ids)<2:continue
        for sid in ids:
            item=result[sid]
            if any(o['kind'] in {'manual','captions'} for o in item['evidence']):continue
            item.update(candidate_name=item['name'],name=None,source=None,identity_status='conflict')
    return result


def decorate(turns, meta):
    for t in turns:
        sid = t.get("speaker")
        words = t.get("words") or []
        statuses = {w.get("attribution", {}).get("status") for w in words}
        ambiguous = statuses & {"overlap", "multiple_speakers"}
        evidence = dict(t.get("attribution") or {})
        evidence.setdefault("status", ("overlap" if "overlap" in statuses else "multiple_speakers")
                            if ambiguous else "supported" if sid else "unresolved")
        evidence["person_id"] = meta.get(sid, {}).get("person_id")
        evidence["identity_status"] = meta.get(sid, {}).get("identity_status", "anonymous")
        evidence.setdefault("method", "audio_timing" if words else "turn_timing")
        if ambiguous:
            evidence["candidates"] = list({a["speaker"]: a for w in words
                                            for a in w.get("attribution", {}).get("candidates", [])}.values())
        t["attribution"] = evidence
    return turns


def notes(turns, speakers):
    """Extractive speaker notes with exact source IDs. No guessed action ownership."""
    out = []
    for s in speakers:
        if s.get("id") == "S0":
            continue
        contributions = [{"segment_id": t["id"], "start": t["start"], "end": t["end"], "text": t["text"],
                          "attribution_status": t.get("attribution", {}).get("status", "legacy")}
                         for t in turns if t.get("speaker") == s["id"]]
        out.append({"person_id": s.get("person_id", f"P{s['id'][1:]}"), "speaker": s["id"],
                    "name": s.get("name"), "label": s["label"], "contributions": contributions})
    return out
