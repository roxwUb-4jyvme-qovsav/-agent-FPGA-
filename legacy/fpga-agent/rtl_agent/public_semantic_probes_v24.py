"""Bounded probes derived from a small, explicit public specification grammar.

No task identifier, candidate text, evaluator file, reference RTL or historical
answer selects a probe. An unsupported or additional clause makes us abstain.
Passing probes never establishes full RTL correctness or official L2.
"""
from __future__ import annotations

import hashlib
import json
import re

from .interface_contract import _split_commas, _declaration

WORDS = {1: 'One', 2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five', 6: 'Six', 7: 'Seven', 8: 'Eight'}


def _normal(text):
    return ' '.join(text.split())


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _body(spec):
    # These optional administrative wrappers contain no functional authority.
    text = spec.strip()
    text = re.sub(r'\APublic development RTL task [A-Za-z_][A-Za-z0-9_]*\. Implement synthesizable Verilog\.\s*', '', text)
    text = re.sub(r'\AReturn only the complete RTL\.\s*', '', text)
    header = re.fullmatch(r'The exact top-level interface is:\s*(module\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^;]+\);)\s*(.*?)\s*(?:Return only the complete RTL, including any required child modules\.)?', text, re.S)
    if not header:
        return None
    body = header.group(2).strip()
    suffix = 'Return only the complete RTL, including any required child modules.'
    if body.endswith(suffix):
        body = body[:-len(suffix)].strip()
    return _normal(body)


def _shape(ports, directions, widths, signed):
    return (len(ports) == len(directions) and
            all(p['direction'] == d and p['width'] == w and p['signed'] == s
                for p, d, w, s in zip(ports, directions, widths, signed)))


def _saturation(body, ports):
    if len(ports) != 4:
        return None
    w = ports[0]['width']
    if type(w) is not int or not 2 <= w <= 16 or not _shape(ports, ['input', 'input', 'output', 'output'], [w,w,w,1], [True,True,True,False]):
        return None
    a,b,y,ov = [p['name'] for p in ports]
    lo,hi = -(1 << (w-1)), (1 << (w-1))-1
    text = (f"Combinational signed saturating addition. Interpret {a} and {b} as two's complement integers. "
            f'Compute their mathematical sum. If sum>{hi}, {y}={hi} and {ov}=1. If sum<{lo}, {y}={lo} and {ov}=1. '
            f'Otherwise {y} is the exact sum and {ov}=0. No clock and no latency.')
    if body != text:
        return None
    values = sorted({lo,lo+1,-1,0,1,hi-1,hi})
    mask = (1 << w)-1
    checks = []
    for left in values:
        for right in values:
            total = left+right
            checks.append({'inputs': {a:left & mask,b:right & mask},
                           'expected': {y:max(lo,min(hi,total)) & mask,ov:int(total<lo or total>hi)},
                           'public_context': f'signed operands {left} and {right}; mathematical sum {total}; saturation interval [{lo},{hi}]'})
    return {'kind':'signed_saturation','clock':None,'checks':checks,
            'property':text,'reason':'Complete explicit signed saturation rule with width-consistent bounds'}


def _round_robin(body, ports):
    if len(ports)!=5:return None
    n=ports[3]['width']
    if type(n) is not int or not 2<=n<=5 or not _shape(ports,['input']*4+['output'],[1,1,1,n,n],[False]*5):return None
    clk,rst,advance,req,grant=[x['name'] for x in ports]
    m=re.search(r'Internal priority pointer ([A-Za-z_][A-Za-z0-9_]*) ranges',body)
    if not m:return None
    pointer=m.group(1);order=','.join(pointer+(f'+{i}' if i else '') for i in range(n))
    text=(f'{WORDS[n]}-request round-robin arbiter. {grant} is COMBINATIONAL, one-hot-or-zero. '
          f'Internal priority pointer {pointer} ranges 0..{n-1} and synchronously resets to 0 at rising {clk} when {rst}=1. '
          f'Search {req} in circular order {order} modulo {n} and grant the first asserted request; no {req} means {grant}=0. '
          f'At rising {clk} when {rst}=0 and {advance}=1 and {grant}!=0, set pointer to (granted_index+1) modulo {n}. '
          f'Otherwise hold pointer. Reset does not combinationally gate {grant}. '
          f'After each edge {grant} immediately reflects the new pointer and current {req}; {advance} never gates {grant}.')
    if body!=text:return None
    full=(1<<n)-1
    sequence=[(1,1,full),(0,0,full)]+[(0,1,full)]*(2*n+1)
    sequence += [(0,a,mask) for mask in range(1<<n) for a in (0,1)]
    sequence += [(0,1,0),(0,0,full),(1,1,full),(0,0,full)]
    checks=[];priority=0
    def select(start,mask):
        return next((1<<((start+i)%n) for i in range(n) if mask&(1<<((start+i)%n))),0)
    for reset,step,mask in sequence:
        old=priority;before=select(old,mask)
        if reset:priority=0
        elif step and before:priority=(before.bit_length())%n
        checks.append({'inputs':{rst:reset,advance:step,req:mask},'expected':{grant:select(priority,mask)},
                       'public_context':f'priority before edge {old}; selected grant before edge 0x{before:x}; priority after edge {priority}; one-hot first request in circular order'})
    return {'kind':'round_robin','clock':clk,'checks':checks,'property':text,
            'reason':'Complete explicit synchronous round-robin priority and combinational grant rule'}


def _multiply_pipeline(body, ports):
    if len(ports)!=8:return None
    w=ports[4]['width']
    if type(w) is not int or not 1<=w<=8 or not _shape(ports,['input']*6+['output']*2,[1,1,1,1,w,w,2*w,1],[False]*8):return None
    clk,rst,ce,valid,left,right,product,out_valid=[x['name'] for x in ports]
    text=(f'All registers use rising {clk} and synchronous active-high {rst} with priority over {ce}. '
          f'A three-stage unsigned multiply pipeline: when {ce}=1, stage1 captures {left}*{right} and {valid}, '
          f'stage2 captures the previous stage1, stage3 captures the previous stage2. {product} and {out_valid} expose stage3. '
          'A sample captured at the first enabled edge appears immediately after the third enabled edge (including its capture edge). '
          f'When {ce}=0 ALL stages and outputs hold. {rst} clears every valid bit and {product} to zero. '
          f'Bubbles advance with {out_valid}=0; {product} value during an invalid bubble is otherwise unconstrained.')
    if body!=text:return None
    maximum=(1<<w)-1
    sequence=[(1,0,1,maximum,maximum),(0,0,1,1,1),
              (0,1,1,1,maximum),(0,1,1,maximum,maximum),(0,0,1,0,maximum),
              (0,1,0,maximum,1),(0,1,1,0,maximum),(0,0,0,maximum,maximum),
              (0,1,1,maximum,0),(0,1,1,1,1),(0,1,0,0,0),
              (1,1,1,maximum,maximum),(0,0,0,maximum,maximum)]
    sequence += [(0,enabled,bit,a,b) for enabled,bit,a,b in
                 [(1,1,maximum,maximum),(0,1,0,0),(1,1,1,maximum),
                  (1,0,0,0),(0,0,maximum,maximum),(1,1,maximum,1),
                  (1,1,1,1),(1,0,0,0),(1,0,0,0),(1,0,0,0)]]
    stages=[(0,0)]*3;defined=True;checks=[]
    for reset,enabled,bit,a,b in sequence:
        if reset:stages=[(0,0)]*3;defined=True
        elif enabled:
            stages=[(a*b,bit)]+stages[:-1];defined=bool(stages[-1][1])
        value,valid_bit=stages[-1]
        checks.append({'inputs':{rst:reset,ce:enabled,valid:bit,left:a,right:b},
                       'expected':{product:value if defined else None,out_valid:valid_bit},
                       'public_context':f'After this rising edge public valid pipeline is {[v for _,v in stages]}; reset has priority over enable; invalid-bubble product is not checked'})
    return {'kind':'enabled_multiply_pipeline','clock':clk,'checks':checks,'property':text,
            'reason':'Complete explicit three-stage unsigned multiply, enable hold, reset priority and bubble contract'}


def _accepted_sequence(body, ports):
    if not _shape(ports,['input']*4+['output'],[1]*5,[False]*5):return None
    clk,rst,en,bit_in,match=[x['name'] for x in ports]
    m=re.search(r'ACCEPTED bits including current '+re.escape(bit_in)+r' equal ([01]{2,8})\.',body)
    if not m:return None
    pattern=m.group(1);count=WORDS[len(pattern)].lower()
    text=(f'At rising {clk}, synchronous active-high {rst} clears recognition history and {match}. '
          f'Otherwise {match} is a one-clock-cycle pulse set to 1 exactly when {en}=1 and the last {count} ACCEPTED bits including current {bit_in} equal {pattern}. '
          f'Bits are accepted only with {en}=1. Allow overlapping matches. {en}=0 holds recognition history but clears {match} (it does not hold a previous pulse). '
          f'Before {count} bits since reset, {match} must be zero. No other ports.')
    if body!=text:return None
    overlap=max([i for i in range(1,len(pattern)) if pattern[:i]==pattern[-i:]] or [0])
    sequence=[(1,1,1)]+[(0,1,int(x)) for x in pattern]+[(0,0,1)]
    sequence += [(0,1,int(x)) for x in pattern[overlap:]]+[(0,0,0)]
    for i,value in enumerate('01'+pattern+pattern+'10'):
        if i%3==0:sequence.append((0,0,int(value)^1))
        sequence.append((0,1,int(value)))
    sequence += [(1,0,1)]+[(0,1,int(x)) for x in pattern]+[(0,0,1)]
    history='';checks=[]
    for reset,enabled,value in sequence:
        if reset:history='';pulse=0
        elif enabled:history=(history+str(value))[-len(pattern):];pulse=int(history==pattern)
        else:pulse=0
        checks.append({'inputs':{rst:reset,en:enabled,bit_in:value},'expected':{match:pulse},
                       'public_context':f'accepted suffix {history or "EMPTY"} including current accepted bit; target {pattern}; disabled edge clears pulse and holds history; pre-pattern history is insufficient'})
    return {'kind':'accepted_bit_sequence','clock':clk,'checks':checks,'property':text,
            'reason':'Complete explicit accepted-bit pattern, overlap, same-edge pulse and enable/reset contract'}


DERIVERS = (_saturation,_round_robin,_multiply_pipeline,_accepted_sequence)


def derive_public_probe(spec):
    abstain = {'status':'INDETERMINATE','source':'public_spec_only',
               'reason':'Unsupported or ambiguous public behavioral contract'}
    if not isinstance(spec,str) or not spec.strip() or len(spec)>30000:
        return {**abstain,'reason':'Missing, invalid or over-limit public specification'}
    try:
        body = _body(spec)
        headers = re.findall(r'\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;]+)\);',spec)
        if body is None or len(headers)!=1:
            return abstain
        top,header=headers[0]
        ports=[];inherited=None
        for segment in _split_commas(header):
            port=_declaration(segment,inherited)
            if port is None:return abstain
            ports.append(port);inherited=port
        if len({v['name'] for v in ports})!=len(ports):return abstain
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',p['name']) for p in ports):
            return abstain
        matches = [v for derive in DERIVERS if (v := derive(body,ports)) is not None]
        if len(matches) != 1:
            return abstain
        plan = matches[0]
        if not 1 <= len(plan['checks']) <= 192:
            return {**abstain,'reason':'Public probe complexity limit exceeded'}
        plan.update(status='READY',source='public_spec_only',requirement_source=body,
                    requirement_sha256=_hash(spec),top_module=top,ports=ports)
        for i,check in enumerate(plan['checks']):
            check['id']=i
        plan['plan_sha256']=_hash(json.dumps(plan,sort_keys=True,separators=(',',':')))
        return plan
    except (KeyError,TypeError,ValueError,OverflowError,RecursionError):
        return {**abstain,'reason':'Public probe parser cannot establish a complete contract'}


def render_public_probe(plan, candidate_sha256, top='TopModule', reserved_names=()):
    if plan.get('status')!='READY' or plan.get('top_module')!=top or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',top):
        raise ValueError('Bound public plan and matching top are required')
    if not re.fullmatch(r'[0-9a-f]{64}',candidate_sha256):
        raise ValueError('Actual candidate SHA256 required')
    ports=plan['ports']
    used={p['name'] for p in ports}|set(reserved_names)|{top}
    def private(name):
        while name in used:name += '_private'
        used.add(name)
        return name
    tb_top=private('rtl_agent_public_probe');dut=private('dut');errors=private('probe_errors')
    nonce=_hash(plan['plan_sha256']+candidate_sha256)[:24]
    lines=['`timescale 1ps/1ps',f'module {tb_top};']
    for p in ports:
        dtype='reg' if p['direction']=='input' else 'wire'
        sign=' signed' if p['signed'] else ''
        rng=f" [{p['width']-1}:0]" if p['width']>1 else ''
        lines.append(f"  {dtype}{sign}{rng} {p['name']};")
    lines += [f"  {top} {dut}("+', '.join(f".{p['name']}({p['name']})" for p in ports)+');',
              f'  integer {errors}=0;','  initial begin']
    clock=plan['clock']
    if clock:lines.append(f'    {clock}=0;')
    outputs={p['name']:p for p in ports if p['direction']=='output'}
    widths={p['name']:p['width'] for p in ports}
    for check in plan['checks']:
        for name,value in check['inputs'].items():
            lines.append(f"    {name}={widths[name]}'h{value:x};")
        lines.append(f'    #5; {clock}=1; #1;' if clock else '    #10;')
        for name,value in check['expected'].items():
            if value is None:continue
            width=outputs[name]['width']
            lines += [f"    if ({name} !== {width}'h{value:x}) begin",
                      f'      {errors}={errors}+1;',
                      f'      if ({errors}<=8) $display("RTL_AGENT_PUBLIC_MISMATCH_{nonce} check={check["id"]} output={name} actual=%h", {name});',
                      '    end']
        if clock:lines.append(f'    #4; {clock}=0;')
    lines += [f'    $display("RTL_AGENT_PUBLIC_DONE_{nonce} mismatches=%0d", {errors});',
              '    $finish;','  end','endmodule','']
    return {'testbench':'\n'.join(lines),'top':tb_top,'nonce':nonce}


def parse_public_probe_log(plan, nonce, log, candidate_text=None):
    """Only candidate-bound, well-formed simulator observations form feedback."""
    uncertain={'status':'SELFTEST_SUSPECT','reason':'Public probe did not produce consistent completion evidence'}
    if plan.get('status')!='READY' or not re.fullmatch(r'[0-9a-f]{24}',nonce):
        return uncertain
    ends=re.findall(rf'(?m)^RTL_AGENT_PUBLIC_DONE_{nonce} mismatches=(\d+)\s*$',log)
    rows=re.findall(rf'(?m)^RTL_AGENT_PUBLIC_MISMATCH_{nonce} check=(\d+) output=([A-Za-z_][A-Za-z0-9_]*) actual=([0-9a-fA-FxXzZ]+)\s*$',log)
    if len(ends)!=1 or len(str(ends[0]))>6:
        return uncertain
    total=int(ends[0])
    if total>sum(v is not None for c in plan['checks'] for v in c['expected'].values()) or len(rows)!=min(total,8):
        return uncertain
    outputs={p['name']:p for p in plan['ports'] if p['direction']=='output'}
    evidence=[];seen=set()
    for raw_id,name,actual in rows:
        if len(raw_id)>6:return uncertain
        idx=int(raw_id)
        if not 0<=idx<len(plan['checks']) or name not in outputs or (idx,name) in seen:return uncertain
        seen.add((idx,name));check=plan['checks'][idx];expected=check['expected'].get(name)
        width=outputs[name]['width']
        if expected is None or len(actual)>(width+3)//4 or (not re.search('[xXzZ]',actual) and int(actual,16)==expected):return uncertain
        evidence.append({'check_id':idx,'inputs':check['inputs'],'output':name,
                         'actual_hex':actual,'expected_value':expected,'width':width,
                         'public_context':check['public_context']})
    if not total:
        return {'status':'SELFTEST_PASS','reason':'Bounded public behavioral probes passed; full function and official L2 remain unproven','counterexamples':[]}
    detail='\n'.join(f"Public probe inputs {e['inputs']}: {e['output']} observed 0x{e['actual_hex']}, public rule requires 0x{e['expected_value']:x} ({e['width']} bits). {e['public_context']}." for e in evidence[:3])
    feedback=('A self-generated probe derived only from the explicit public specification observed a mismatch. '
              'This is diagnostic evidence, not an independent evaluator verdict. Check the observations against the public rule before changing RTL.\n'
              f"Target property: {plan['property']}\n"+detail+
              '\nCorrect the public behavioral property, preserving the interface, signedness, widths, reset/enable priority and edge timing. '
              'Return exactly one complete TopModule RTL implementation; a passed probe does not replace the remaining checks.')[:2000]
    result={'status':'RTL_SUSPECT','reason':'Candidate disagreed with bounded public behavioral probes; not an oracle',
            'counterexamples':evidence,'mismatch_count':total,'repair_feedback':feedback}
    if plan['kind']!='signed_saturation' and isinstance(candidate_text,str):
        # Directions use source facts only when the exact candidate bytes bind
        # to the observation nonce. Signed feedback stays byte-identical.
        if _hash(plan['plan_sha256']+_hash(candidate_text))[:24]==nonce:
            try:
                from .public_repair_directions_v24 import render_repair_direction
                direction=render_repair_direction(plan,evidence,candidate_text)
            except Exception:
                direction=None
            if direction is not None:
                result['repair_feedback']=direction['feedback']
                result['repair_direction']={k:v for k,v in direction.items() if k!='feedback'}
                result['feedback_rendering_status']='ACTIONABLE_PUBLIC_DIRECTION'
            else:result['feedback_rendering_status']='INDETERMINATE_LEGACY_FEEDBACK_RETAINED'
        else:result['feedback_rendering_status']='CANDIDATE_BINDING_MISMATCH_LEGACY_FEEDBACK_RETAINED'
    return result
