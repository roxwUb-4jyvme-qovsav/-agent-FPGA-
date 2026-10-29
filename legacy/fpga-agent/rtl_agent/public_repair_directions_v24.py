"""Short Repair directions from validated public probes and the actual RTL.

This module renders feedback only. It does not change probe expectations,
diagnostic status, RTL, candidate selection, model calls or any budget.
"""
from __future__ import annotations
import hashlib
import re
from .interface_contract import _clean_rtl

IDENT=r'[A-Za-z_][A-Za-z0-9_]*'


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _excerpt(code, receiver, prefer_operator=False):
    clean=_clean_rtl(code)
    found=[]
    for m in re.finditer(r'\b'+re.escape(receiver)+r'\s*<=\s*([^;]{1,600});',clean):
        expression=m.group(1)
        if prefer_operator and not re.search(r'[+*]|==',expression):continue
        text=' '.join(code[m.start():m.end()].split())
        if len(text)>240:continue
        found.append({'line':code[:m.start()].count('\n')+1,'text':text,'source_excerpt_sha256':_sha(text)})
    return found[:1]


def _observation(e):
    inputs=', '.join(f'{n}=0x{v:x}' for n,v in e['inputs'].items())
    return f"Observed public probe: {inputs}; {e['output']}=0x{e['actual_hex']}, required 0x{e['expected_value']:x} ({e['width']} bits)."


def _pipeline(plan, evidence, code):
    clk,rst,ce,valid,left,right,product,out_valid=[p['name'] for p in plan['ports']]
    selected=next((e for e in evidence if e['inputs'].get(rst)==1 and e['output']==product and e['expected_value']==0),evidence[0])
    reset_case=selected['inputs'].get(rst)==1
    if reset_case:
        action=(f'Repair the synchronous reset assignments that drive {product}. At a rising {clk} with {rst}=1, '
                f'{product} MUST become zero even when {ce}=0, and every valid bit must clear. '
                'The invalid-bubble freedom does not apply to this explicit reset requirement. '
                f'Trace the register driving {product} and assign its reset value in the reset path; clearing only {out_valid} is insufficient.')
    else:
        action=(f'Repair the enabled three-stage data/valid recurrence: the captured unsigned product and {valid} '
                f'must reach {product}/{out_valid} on the third enabled edge including capture. '
                f'{ce}=0 holds the complete pipeline; {rst} overrides {ce}. Do not add or remove a latency stage.')
    clean=_clean_rtl(code);facts=[];receiver=product
    matches=list(re.finditer(r'\bassign\s+'+re.escape(product)+r'\s*=\s*('+IDENT+r')\s*;',clean))
    if len(matches)==1:
        m=matches[0];receiver=m.group(1);text=' '.join(code[m.start():m.end()].split())
        facts.append({'line':code[:m.start()].count('\n')+1,'text':text,'source_excerpt_sha256':_sha(text)})
    facts.extend(_excerpt(code,receiver,True))
    return action,selected,facts


def _arbiter(plan, evidence, code):
    clk,rst,advance,req,grant=[p['name'] for p in plan['ports']];n=plan['ports'][3]['width']
    states=[]
    for e in evidence:
        m=re.fullmatch(r'priority before edge (\d+); selected grant before edge 0x([0-9a-f]+); priority after edge (\d+); one-hot first request in circular order',e['public_context'])
        if not m:continue
        before,onehot,after=int(m.group(1)),int(m.group(2),16),int(m.group(3))
        if not onehot or onehot&(onehot-1) or not 0<=before<n or not 0<=after<n:continue
        winner=onehot.bit_length()-1
        if winner>=n or after!=(winner+1)%n or e['inputs'].get(rst)!=0 or e['inputs'].get(advance)!=1:continue
        states.append((e,before,winner,after))
    if not states:return None
    selected,before,winner,after=next((s for s in states if s[1]!=s[2]),states[0])
    action=(f'Repair the priority update, using the INDEX OF THE GRANTED REQUEST before the edge. '
            f'For {advance}=1 and nonzero {grant}, next priority = (granted_index + 1) modulo {n}, exactly once. '
            'Do not increment an already incremented decoded value. Do not substitute old_priority+1: that fails when inactive requests are skipped. '
            f'In this public-model step, old priority is {before}, granted index is {winner}, so required next priority is {after}. '
            f'{grant} must remain combinational and reflect the new priority immediately after {clk}; hold priority when no update is enabled.')
    m=re.search(r'Internal priority pointer ('+IDENT+r') ranges',plan['property'])
    facts=_excerpt(code,m.group(1),True) if m else []
    return action,selected,facts


def _sequence(plan, evidence, code):
    clk,rst,en,serial,match=[p['name'] for p in plan['ports']]
    m=re.search(r' equal ([01]{2,8})\.',plan['property'])
    if not m:return None
    pattern=m.group(1);selected=next((e for e in evidence if e['expected_value']==1 and e['inputs'].get(en)==1),evidence[0])
    action=(f'Repair the SAME-EDGE {match} decision. For an accepted edge, form the logical next accepted history '
            f'including the CURRENT {serial}, and test that next history against the public pattern {pattern}. '
            'A nonblocking <= assignment does not update a history register before another RHS in the same clocked block reads it. '
            'Comparing only old history therefore delays the pulse; moving if/else blocks without changing that dependency cannot fix it. '
            f'Preserve overlap and the minimum {len(pattern)} accepted-bit requirement. {rst} clears history and {match}; '
            f'{en}=0 holds history but clears {match}.')
    facts=_excerpt(code,match,True)
    # A concrete direction is emitted only for one unique shift recurrence in
    # this candidate and a match assignment that reads that same old register.
    clean=_clean_rtl(code)
    recurrence=re.compile(r'\b('+IDENT+r')\s*<=\s*(\{\s*\1\s*\[\s*'+str(len(pattern)-2)+r'\s*:\s*0\s*\]\s*,\s*'+re.escape(serial)+r'\s*\})\s*;')
    updates=list(recurrence.finditer(clean))
    if len(updates)==1:
        update=updates[0];history=update.group(1)
        decisions=list(re.finditer(r'\b'+re.escape(match)+r'\s*<=\s*([^;]{1,600});',clean))
        reads=[d for d in decisions if re.search(r'\b'+re.escape(history)+r'\b',d.group(1))]
        if len(reads)==1 and '==' in reads[0].group(1):
            next_expression=' '.join(code[update.start(2):update.end(2)].split())
            text=' '.join(code[update.start():update.end()].split())
            facts.append({'line':code[:update.start()].count('\n')+1,'text':text,'source_excerpt_sha256':_sha(text)})
            action=(f'CHANGE the decision that drives {match}; returning the existing expression is not a repair. '
                    f'Your own update computes next accepted history as {next_expression}, but your {match} RHS reads old {history}. '
                    f'Use the next-history value {next_expression}, including CURRENT {serial}, for the comparison with the public pattern {pattern} on THIS accepted edge. '
                    'A nonblocking <= assignment evaluates its RHS from pre-edge state; assigning the history register first does not update the later comparison of old history. '
                    f'This is a registered same-edge pulse requirement, not an extra-cycle Moore comparison of old {history}. '
                    f'Keep all {len(pattern)} pattern bits, the minimum {len(pattern)} accepted-bit requirement, and overlapping matches. '
                    f'{rst} clears history and {match}; {en}=0 holds history and clears {match}. '
                    'Do not change interface or add a latency cycle. An implementation must still pass the remaining checks.')
    return action,selected,facts


RENDERERS={'enabled_multiply_pipeline':_pipeline,'round_robin':_arbiter,'accepted_bit_sequence':_sequence}


def render_repair_direction(plan,evidence,candidate_text):
    """Return None on malformed/unbound inputs; no invented source facts."""
    if not isinstance(plan,dict) or plan.get('status')!='READY' or plan.get('kind') not in RENDERERS:return None
    if not isinstance(candidate_text,str) or not candidate_text.strip() or len(candidate_text)>200000:return None
    if not isinstance(evidence,list) or not evidence:return None
    try:
        outputs={p['name']:p for p in plan['ports'] if p['direction']=='output'}
        for e in evidence:
            i=e['check_id'];assert type(i) is int and 0<=i<len(plan['checks'])
            check=plan['checks'][i];name=e['output'];assert name in outputs
            assert e['inputs']==check['inputs'] and e['public_context']==check['public_context']
            assert type(e['expected_value']) is int and e['expected_value']==check['expected'][name]
            assert e['width']==outputs[name]['width'] and re.fullmatch(r'[0-9a-fA-FxXzZ]+',e['actual_hex'])
            assert len(e['actual_hex'])<=(e['width']+3)//4
            assert re.search('[xXzZ]',e['actual_hex']) or int(e['actual_hex'],16)!=e['expected_value']
        result=RENDERERS[plan['kind']](plan,evidence,candidate_text)
        if result is None:return None
        action,selected,facts=result
        source='\n'.join(f"Your RTL line {f['line']}: {f['text']}" for f in facts[:2])
        feedback=(f"Repair required for the explicit public {plan['kind']} property.\n"+action+'\n'+
                  _observation(selected)+'\n'+selected['public_context']+'\n'+source+
                  '\nMake a behavioral correction to the current candidate; do not return the same failing implementation. '
                  f"Return exactly one complete {plan['top_module']} module, with all required RTL. "
                  'Keep interface, widths, signedness and all other public behavior. The remaining checks and independent acceptance still apply.')
        if len(feedback)>1900:return None
        return {'feedback':feedback,'kind':plan['kind'],'candidate_sha256':_sha(candidate_text),
                'selected_check_id':selected['check_id'],'candidate_source_facts':facts,
                'evidence_source':'VALIDATED_PUBLIC_PROBE_AND_CURRENT_CANDIDATE_ONLY'}
    except (AssertionError,KeyError,TypeError,ValueError,IndexError,OverflowError,RecursionError):
        return None
