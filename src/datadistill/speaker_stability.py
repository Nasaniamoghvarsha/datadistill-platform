"""Separate word-timing uncertainty, voice continuity, and evidence for a new voice.

No speaker-count limit. Raw model evidence is retained. Context labels never erase
word-level uncertainty or bridge a genuine competing-voice/overlap boundary.
"""
from collections import defaultdict
from . import diarization

MIN_ANCHOR_S = 2.0
MIN_CHANGE_S = 1.0
CONTEXT_DISTANCE_S = 1.5


def profiles(timeline):
    groups = defaultdict(list)
    for t in timeline:
        if t['end'] > t['start']:
            groups[t['speaker']].append(t)
    records = {}
    for sid, rows in groups.items():
        # Only clean intervals can establish a track; overlapping voices are not anchors.
        anchors = [t for t in rows if t['end']-t['start'] >= MIN_ANCHOR_S and not any(
            o['speaker'] != sid and min(t['end'], o['end']) > max(t['start'], o['start'])
            for o in timeline)]
        records[sid] = {'raw_cluster': sid, 'status': 'supported_voice' if anchors else 'tentative',
                        'speech_s': round(diarization._union_length([(t['start'],t['end']) for t in rows]),3),
                        'longest_interval_s': round(max(t['end']-t['start'] for t in rows),3),
                        'first_anchor_s': min((t['start'] for t in anchors), default=None)}
    order = sorted((s for s in records if records[s]['status']=='supported_voice'),
                   key=lambda s: records[s]['first_anchor_s'])
    mapping = {s:f'S{i+1}' for i,s in enumerate(order)}
    for s, rec in records.items():rec['voice_id'] = mapping.get(s)
    return records, mapping


def annotate(words, timeline):
    records, mapping = profiles(timeline)
    mapped = [{**t,'speaker':mapping[t['speaker']]} for t in timeline if t['speaker'] in mapping]
    strict = []
    for w in words:
        a,b = w['start'],min(w['end'],w['start']+1.2)
        raw = diarization.assignment(a,b,timeline)
        ev = {**raw, 'raw_assignment':raw, 'method':'word_timing', 'timing_status':'clear'}
        ev['candidates'] = [{**c,'speaker':mapping[c['speaker']]} for c in raw['candidates'] if c['speaker'] in mapping]
        ev['speaker'] = mapping.get(raw['speaker'])
        if raw['speaker'] and raw['speaker'] not in mapping:
            ev.update(status='tentative_voice',timing_status='uncertain',raw_cluster=raw['speaker'])
        elif not ev['speaker']:ev['timing_status']='uncertain'
        strict.append(ev)

    # A very short isolated change is a candidate interruption, not a proven new turn.
    runs=[]
    for i,ev in enumerate(strict):
        if runs and runs[-1][2]==ev['speaker']:runs[-1][1]=i+1
        else:runs.append([i,i+1,ev['speaker']])
    for k,(a,b,sid) in enumerate(runs):
        if not sid or k==0 or k==len(runs)-1:continue
        left,right=runs[k-1][2],runs[k+1][2]
        # A timing-uncertain word next to the fragment must not turn a sub-second
        # candidate into a confident handover. Look through at most two such words;
        # competing/overlapping evidence still blocks this contextual check.
        li,ri=a-1,b
        while li>=0 and strict[li]['speaker'] is None and a-li<=2 and strict[li]['status'] not in {'overlap','multiple_speakers'}:li-=1
        while ri<len(strict) and strict[ri]['speaker'] is None and ri-b<2 and strict[ri]['status'] not in {'overlap','multiple_speakers'}:ri+=1
        if left is None and li>=0 and a-li<=3:left=strict[li]['speaker']
        if right is None and ri<len(strict) and ri-b<=2:right=strict[ri]['speaker']
        duration=diarization._union_length([(w['start'],min(w['end'],w['start']+1.2)) for w in words[a:b]])
        if left and left==right and left!=sid and duration<MIN_CHANGE_S:
            for ev in strict[a:b]:
                ev.update(candidate_voice=sid,speaker=None,status='possible_interruption',timing_status='uncertain')

    contexts=[ev['speaker'] for ev in strict]
    missing=[]
    for i,ev in enumerate(strict):
        if ev['speaker']:continue
        if missing and missing[-1][1]==i:missing[-1][1]=i+1
        else:missing.append([i,i+1])
    for a,b in missing:
        if any(e['status'] in {'overlap','multiple_speakers'} for e in strict[a:b]):continue
        # Count spoken-word evidence, not wall-clock time: a pause is not a new identity.
        short_run=diarization._union_length([(w['start'],min(w['end'],w['start']+1.2)) for w in words[a:b]])<=2.5
        previous=a-1 if a else None
        following=b if b<len(words) else None
        near_left=previous is not None and words[a]['start']-words[previous]['end']<=CONTEXT_DISTANCE_S
        near_right=following is not None and words[following]['start']-words[b-1]['end']<=CONTEXT_DISTANCE_S
        context=None
        if near_left and near_right:
            l,r=strict[previous]['speaker'],strict[following]['speaker']
            same_evidence=all(e['candidates'] and {c['speaker'] for c in e['candidates']}=={l}
                              and e['status'] not in {'tentative_voice','possible_interruption'} for e in strict[a:b])
            if l==r and (short_run or same_evidence):context=l
        elif previous is None and near_right and short_run:
            # Opening timing uncertainty can share a display context, not a strict identity claim.
            context=strict[following]['speaker']
        elif following is None and near_left and short_run:
            context=strict[previous]['speaker']
        for i in range(a,b):contexts[i]=context
    for w,ev,ctx in zip(words,strict,contexts):
        ev['context_speaker']=ctx
        w['attribution']=ev
    return contexts, {'timeline':mapped,'raw_timeline':timeline,'cluster_profiles':records,
                      'raw_to_voice':mapping,'tentative_clusters':[r for r in records.values() if r['status']=='tentative'],
                      'word_stats':{'total':len(words),'assigned':sum(bool(e['speaker']) for e in strict),
                                    'unresolved':sum(not e['speaker'] for e in strict)},
                      'policy':'voice_continuity_v1'}


def to_turns(segments, contexts):
    from . import speakers
    turns=speakers.to_turns(segments,contexts,[None]*len(contexts))
    for t in turns:
        uncertain=[w for w in t.get('words',[]) if w.get('attribution',{}).get('speaker')!=t.get('speaker') or not t.get('speaker')]
        t['attribution']={'method':'voice_context_with_word_evidence',
                          'status':'contextual' if t.get('speaker') and uncertain else 'supported' if t.get('speaker') else 'unresolved',
                          'uncertain_word_count':len(uncertain),
                          'timing_status':'uncertain' if uncertain else 'clear'}
    return turns
