"""Bounded two-state rotation-expression proof, NOT a general SV verifier.

No file/network/process access. Only an explicitly bound public operation is
checked. Unsupported syntax always abstains; never infer success from EDA.
"""
import hashlib
import math
import re
import time

_ID = r'[A-Za-z_][A-Za-z_0-9]*'
HARD_LIMITS = {'bytes':262144, 'tokens':256, 'depth':24, 'width':64,
               'count_bits':6, 'vector_bits':256, 'steps':100000, 'seconds':1.0}


class Abstain(Exception):
    pass


class Budget:
    def __init__(self, limits, clock):
        self.limits = dict(HARD_LIMITS)
        if limits is not None:
            if not isinstance(limits, dict) or set(limits)-set(HARD_LIMITS):
                raise Abstain('INVALID_LIMITS')
            for k,v in limits.items():
                if type(v) not in (int,float) or not math.isfinite(v) or v < 0 or v > HARD_LIMITS[k]:
                    raise Abstain('INVALID_LIMITS')
                if k != 'seconds' and type(v) is not int:
                    raise Abstain('INVALID_LIMITS')
                self.limits[k] = v
        self.clock = clock
        self.deadline = clock()+self.limits['seconds']
        self.steps = 0

    def tick(self, n=1):
        self.steps += n
        if self.steps > self.limits['steps']:
            raise Abstain('STEP_BUDGET_EXHAUSTED')
        if self.clock() >= self.deadline:
            raise Abstain('TIME_BUDGET_EXHAUSTED')


def _ports(text):
    headers = list(re.finditer(r'\bmodule\s+('+_ID+r')\s*\(([^()]*)\)\s*;', text, re.S))
    if len(headers) != 1:
        raise Abstain('UNSUPPORTED_MODULE_HEADER')
    ports = {}
    for part in headers[0][2].split(','):
        m = re.fullmatch(r'\s*(input|output)\s+(?:(?:wire|logic|reg)\s+)?'
             r'(?:(signed|unsigned)\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?('+_ID+r')\s*', part)
        if not m or m[2] == 'signed' or m[5] in ports:
            raise Abstain('UNSUPPORTED_PORT')
        hi,lo = (int(m[3]),int(m[4])) if m[3] else (0,0)
        if lo != 0 or hi < lo:
            raise Abstain('UNSUPPORTED_PORT_RANGE')
        ports[m[5]] = (m[1],hi+1)
    return headers[0], ports


def _target(spec, code, contract, budget):
    if not re.search(r'(?i)\bcombinational\b', spec):
        raise Abstain('MISSING_COMBINATIONAL_CONTRACT')
    if re.search(r'(?i)\b(illustrative|ambiguous|conflicting)\b', spec):
        raise Abstain('AMBIGUOUS_PUBLIC_CONTRACT')
    rules = re.findall(r'(?i)\bRotate\s+left\s+uses\s+('+_ID+r')\s+modulo\s+(\d+)\s*\.',spec)
    modes = re.findall(r'(?i)\b('+_ID+r')\s*=\s*(\d+)\s+rotate\s+left\b',spec)
    if rules != [(contract['count'],str(contract['width']))] or modes != [(contract['selector'],str(contract['mode_value']))]:
        raise Abstain('PUBLIC_RULE_NOT_UNIQUE_OR_CHANGED')
    if '`' in code or '"' in code:
        raise Abstain('MACRO_OR_STRING_UNSUPPORTED')
    masked = re.sub(r'/\*.*?\*/|//[^\n]*',lambda m: re.sub(r'[^\n]',' ',m[0]),code,flags=re.S)
    if len(re.findall(r'\bmodule\b',masked)) != 1 or len(re.findall(r'\bendmodule\b',masked)) != 1:
        raise Abstain('UNSUPPORTED_MODULE_COUNT')
    h,ports = _ports(masked)
    public_h,public = _ports(spec)
    if h[1] != public_h[1] or ports != public:
        raise Abstain('PUBLIC_INTERFACE_MISMATCH')
    w,obj,count,selector = (contract[k] for k in ['width','operand','count','selector'])
    if ports.get(obj) != ('input',w) or ports.get(count,(None,0))[0] != 'input' or ports.get(selector,(None,0))[0] != 'input':
        raise Abstain('PUBLIC_INPUT_MISMATCH')
    cb = ports[count][1]
    if not 0 < w <= budget.limits['width'] or not 0 < cb <= budget.limits['count_bits']:
        raise Abstain('DOMAIN_COMPLEXITY_LIMIT')
    outputs = [name for name,p in ports.items() if p[0]=='output']
    if len(outputs)!=1 or ports[outputs[0]][1]!=w:
        raise Abstain('UNSUPPORTED_OUTPUT_INTERFACE')
    tail = masked[h.end():]
    end = re.search(r'\bendmodule\b',tail)
    if not end or tail[end.end():].strip():
        raise Abstain('UNSUPPORTED_MODULE_TAIL')
    body = tail[:end.start()]
    declaration = r'\s*(?:logic|reg|wire)\s+(?:\[\s*\d+\s*:\s*0\s*\]\s*)?'+_ID+r'\s*;'
    # New public-property callers opt into a strictly checked combinational
    # subset. Existing compiler-risk callers retain their original frontend.
    strict = contract.get('syntax_profile') == 'PURE_COMBINATIONAL_CASE_V1'
    if strict and not 0 < ports[selector][1] <= budget.limits['count_bits']:
        raise Abstain('SELECTOR_COMPLEXITY_LIMIT')
    opener = r'(?:always_comb|always\s*@\s*(?:\*|\(\s*\*\s*\)))' if strict else r'always_comb'
    top = re.fullmatch(r'(?P<decls>(?:'+declaration+r')*)\s*'+opener+r'\s+begin\s*'
        r'case\s*\(\s*'+re.escape(selector)+r'\s*\)(?P<arms>.*?)\bendcase\s+end\s*'
        r'(?P<aliases>(?:assign\s+'+_ID+r'\s*=\s*'+_ID+r'\s*;\s*)*)',body,re.S)
    if not top or len(re.findall(r'\bcase\b',body))!=1:
        raise Abstain('UNSUPPORTED_CONTROL_FLOW')
    decls = {}
    for m in re.finditer(r'\b(?:logic|reg|wire)\s+(?:\[\s*(\d+)\s*:\s*0\s*\]\s*)?('+_ID+r')\s*;',top['decls']):
        if m[2] in decls or m[2] in ports:
            raise Abstain('DUPLICATE_DECLARATION')
        decls[m[2]] = int(m[1])+1 if m[1] else 1
    arms = top['arms']
    labels = list(re.finditer(r"(?m)^\s*(\d+\s*'[bBoOdDhH][0-9a-fA-F]+|\d+|default)\s*:",arms))
    if not labels or arms[:labels[0].start()].strip():
        raise Abstain('UNSUPPORTED_CASE_LABELS')
    seen=set();selected=None
    for i,m in enumerate(labels):
        token=m[1].replace(' ','')
        if token=='default':value='default'
        elif "'" in token:
            bits,number=token.split("'");radix={'b':2,'o':8,'d':10,'h':16}[number[0].lower()]
            value=int(number[1:],radix)
            if not 0<int(bits)<=32 or value>=1<<int(bits):raise Abstain('TRUNCATED_CASE_LABEL')
        else:value=int(token)
        if value in seen or (value!='default' and value>=1<<ports[selector][1]):
            raise Abstain('DUPLICATE_OR_INVALID_CASE_LABEL')
        seen.add(value)
        if value==contract['mode_value']:
            selected=arms[m.end():labels[i+1].start() if i+1<len(labels) else len(arms)]
    if selected is None:raise Abstain('TARGET_CASE_MISSING')
    assignment = re.fullmatch(r'\s*(?:begin\s+)?('+_ID+r')\s*=\s*([^;]+);\s*(?:end\s*)?',selected,re.S)
    if not assignment or selected.strip().startswith('begin') != selected.strip().endswith('end'):
        raise Abstain('TARGET_NOT_SINGLE_UNCONDITIONAL_ASSIGNMENT')
    receiver,rhs=assignment[1],assignment[2].strip()
    if strict:
        if 'default' not in seen and len(seen) != 1 << ports[selector][1]:
            raise Abstain('INCOMPLETE_COMBINATIONAL_CASE')
        for i,m in enumerate(labels):
            arm=arms[m.end():labels[i+1].start() if i+1<len(labels) else len(arms)]
            a=re.fullmatch(r'\s*(?:begin\s+)?('+_ID+r')\s*=\s*([^;]+);\s*(?:end\s*)?',arm,re.S)
            if (not a or a[1]!=receiver or arm.strip().startswith('begin')!=arm.strip().endswith('end') or
                    re.search(r'[@#]|\b(?:if|for|while|repeat|wait|force|release)\b',a[2]) or
                    re.search(r'\$(?!(?:signed|unsigned)\s*\()',a[2]) or
                    re.search(r'(?<![\w$])'+_ID+r'\s*\(',a[2])):
                raise Abstain('UNSUPPORTED_COMBINATIONAL_ARM')
    aliases=re.findall(r'assign\s+('+_ID+r')\s*=\s*('+_ID+r')\s*;',top['aliases'])
    if receiver==outputs[0]:
        if aliases:raise Abstain('ADDITIONAL_OUTPUT_DRIVER')
    elif decls.get(receiver)==w and aliases==[(outputs[0],receiver)]:pass
    else:raise Abstain('UNSUPPORTED_OUTPUT_BINDING')
    writers=re.findall(r'\b('+_ID+r')\s*(?:<=|(?<![=!<>])=(?!=))',arms)
    if not writers or any(name!=receiver for name in writers):
        raise Abstain('INPUT_OR_ADDITIONAL_OBJECT_DRIVER')
    line=masked[:h.end()+top.start('arms')].count('\n')+1
    return rhs,{'width':w,'count_bits':cb,'operand':obj,'count':count,'receiver':receiver,
                'expression':rhs,'case_body_start_line':line}


_TOKEN = re.compile(r"\d+'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|\d+|"+_ID+r'|<<<|>>>|<<|>>|[+\-%|{},()\[\]:]')

class Parser:
    PRECEDENCE={'|':1,'<<':2,'>>':2,'+':3,'-':3,'%':4}
    def __init__(self,text,budget):
        self.b=budget;self.tokens=[];pos=0
        while pos<len(text):
            if text[pos].isspace():pos+=1;continue
            m=_TOKEN.match(text,pos)
            if not m:raise Abstain('UNSUPPORTED_EXPRESSION_TOKEN')
            self.tokens.append(m[0]);pos=m.end();budget.tick()
            if len(self.tokens)>budget.limits['tokens']:raise Abstain('TOKEN_COMPLEXITY_LIMIT')
        self.i=0

    def pop(self,expected=None):
        if self.i>=len(self.tokens):raise Abstain('PARSE_INCOMPLETE')
        t=self.tokens[self.i];self.i+=1
        if expected and t!=expected:raise Abstain('PARSE_TOKEN_MISMATCH')
        return t

    def parse(self,minimum=0,depth=0):
        self.b.tick()
        if depth>self.b.limits['depth']:raise Abstain('DEPTH_COMPLEXITY_LIMIT')
        token=self.pop()
        if token=='(':
            node=self.parse(0,depth+1);self.pop(')')
        elif token=='{':
            nodes=[self.parse(0,depth+1)]
            while self.i<len(self.tokens) and self.tokens[self.i]==',':
                self.pop(',');nodes.append(self.parse(0,depth+1))
            self.pop('}')
            if any(n[0]=='lit' and n[3] for n in nodes):raise Abstain('UNSIZED_CONCAT_LITERAL')
            node=('concat',nodes)
        elif re.fullmatch(_ID,token):
            node=('id',token)
            if self.i<len(self.tokens) and self.tokens[self.i]=='[':
                self.pop('[');hi=self.pop();self.pop(':');lo=self.pop();self.pop(']')
                if not hi.isdecimal() or not lo.isdecimal():raise Abstain('DYNAMIC_SLICE_UNSUPPORTED')
                node=('slice',token,int(hi),int(lo))
        else:
            if "'" in token:
                bits,number=token.split("'")
                if number[0] in 'sS' or re.search('[xXzZ?]',number):raise Abstain('SIGNED_OR_FOUR_STATE_LITERAL')
                radix={'b':2,'o':8,'d':10,'h':16}[number[0].lower()]
                width=int(bits)
                if not 0<width<=64:raise Abstain('LITERAL_WIDTH_LIMIT')
                value=int(number[1:].replace('_',''),radix)&((1<<width)-1)
                signed=False
            elif token.isdecimal():value=int(token);width=32;signed=True
            else:raise Abstain('UNSUPPORTED_EXPRESSION')
            if value>4096:raise Abstain('LITERAL_VALUE_LIMIT')
            node=('lit',value,width,signed)
        while self.i<len(self.tokens):
            op=self.tokens[self.i];level=self.PRECEDENCE.get(op,-1)
            if level<minimum:break
            self.pop();node=('op',op,node,self.parse(level+1,depth+1))
        return node


class Engine:
    def __init__(self,tree,context,budget):self.tree=tree;self.c=context;self.b=budget

    def width(self,n):
        self.b.tick()
        if n[0]=='id':
            if n[1]==self.c['operand']:return self.c['width']
            raise Abstain('UNKNOWN_DATA_IDENTIFIER')
        if n[0]=='lit':return n[2]
        if n[0]=='slice':
            if n[1]!=self.c['operand'] or not 0<=n[3]<=n[2]<self.c['width']:raise Abstain('SLICE_OUT_OF_RANGE')
            return n[2]-n[3]+1
        if n[0]=='concat':return sum(self.width(x) for x in n[1])
        if n[0]=='op' and n[1] in ('<<','>>'):return self.width(n[2])
        if n[0]=='op' and n[1]=='|':return max(self.width(n[2]),self.width(n[3]))
        raise Abstain('DATA_ARITHMETIC_UNSUPPORTED')

    def count(self,n,count):
        self.b.tick()
        if n[0]=='id' and n[1]==self.c['count']:return count,self.c['count_bits'],False
        if n[0]=='lit':return n[1],n[2],n[3]
        if n[0]!='op' or n[1] not in ('+','-','%'):raise Abstain('COUNT_EXPRESSION_UNSUPPORTED')
        a,wa,sa=self.count(n[2],count);b,wb,sb=self.count(n[3],count)
        w=max(wa,wb);signed=sa and sb
        if n[1]=='+':v=a+b
        elif n[1]=='-':v=a-b
        else:
            if b==0:raise Abstain('COUNT_DIVIDE_BY_ZERO')
            v=a%b
        if signed and v<0:raise Abstain('NEGATIVE_SIGNED_COUNT_UNSUPPORTED')
        return v&((1<<w)-1),w,signed

    def vector(self,n,count,context=0):
        width=max(context,self.width(n))
        if width>self.b.limits['vector_bits']:raise Abstain('VECTOR_COMPLEXITY_LIMIT')
        self.b.tick(width)
        if n[0]=='id':v=[frozenset((i,)) for i in range(self.c['width'])]
        elif n[0]=='lit':v=[frozenset((-1,)) if n[1]>>i&1 else frozenset() for i in range(n[2])]
        elif n[0]=='slice':v=[frozenset((i,)) for i in range(n[3],n[2]+1)]
        elif n[0]=='concat':
            v=[]
            # Concatenation operands are self-determined, including shifts.
            for child in reversed(n[1]):v.extend(self.vector(child,count))
        elif n[0]=='op' and n[1] in ('<<','>>'):
            v=self.vector(n[2],count,width);amount=self.count(n[3],count)[0]
            if amount>=width:v=[frozenset()]*width
            elif n[1]=='<<':v=[frozenset()]*amount+v[:width-amount]
            else:v=v[amount:]+[frozenset()]*amount
        elif n[0]=='op' and n[1]=='|':
            a=self.vector(n[2],count,width);b=self.vector(n[3],count,width)
            v=[frozenset((-1,)) if -1 in x or -1 in y else x|y for x,y in zip(a,b)]
        else:raise Abstain('DATA_EXPRESSION_UNSUPPORTED')
        return (v+[frozenset()]*width)[:width]


def _run(contract,spec,candidate,budget):
    expression,context=_target(spec,candidate,contract,budget)
    parser=Parser(expression,budget);tree=parser.parse()
    if parser.i!=len(parser.tokens):raise Abstain('PARSE_TRAILING_TOKENS')
    engine=Engine(tree,context,budget);w=context['width'];checked=0
    for count in range(1<<context['count_bits']):
        vector=engine.vector(tree,count,w)[:w];expected=[frozenset(((i-count)%w,)) for i in range(w)]
        for i,(observed,required) in enumerate(zip(vector,expected)):
            budget.tick()
            if observed!=required:
                required_bit=next(iter(required))
                if -1 in observed:data=0
                elif required_bit not in observed:data=1<<required_bit
                else:data=1<<next(bit for bit in observed if bit!=required_bit)
                actual=sum(int(-1 in terms or any(data>>bit&1 for bit in terms))<<j for j,terms in enumerate(vector))
                rotate=count%w;public=((data<<rotate)|(data>>(w-rotate if rotate else w)))&((1<<w)-1)
                assert actual!=public
                return {'status':'UNRESOLVED','reason_code':'PUBLIC_ROTATION_COUNTEREXAMPLE',
                    'reason':'A public-domain count/data assignment disproves the target rotation expression.',
                    'evidence':{'context':context,'counts_completed':checked,'counterexample':{'count':count,'data':data,
                      'observed_value':actual,'public_rule_value':public,'output_bit':i,
                      'observed_source_bits':sorted(observed),'required_source_bit':required_bit,
                      'source':'DERIVED_FROM_PUBLIC_RULE_AND_BIT_DEPENDENCIES_NOT_EVALUATOR_DATA'}},
                    'target_property_proven':False}
        checked+=1
    return {'status':'RESOLVED','reason_code':'EXACT_FINITE_BIT_DEPENDENCY_PROOF',
        'reason':'All legal counts and all two-state data assignments satisfy only the declared target rotation expression.',
        'evidence':{'context':context,'counts_completed':checked,'data_bits_symbolic':w,
                    'method':'Exact OR-of-source-bit dependencies, not random or one-hot sampling'},'target_property_proven':True}


def analyze(contract,spec,candidate,limits=None,clock=time.monotonic):
    result={'schema_version':1,'status':'INDETERMINATE','reason_code':'UNSUPPORTED_OR_INVALID_INPUT',
      'reason':'No target-property proof available.','functional_proof':False,'target_property_proven':False,
      'scope':'BOUND_PUBLIC_ROTATION_CASE_UNSIGNED_TWO_STATE_EXPRESSION_ONLY',
      'evidence':{},'budget':{'steps_used':0}}
    budget=None
    try:
        budget=Budget(limits,clock);budget.tick()
        if not isinstance(spec,str) or not isinstance(candidate,str) or not candidate.strip():raise Abstain('EMPTY_OR_INVALID_INPUT')
        if len(spec.encode())+len(candidate.encode())>budget.limits['bytes']:raise Abstain('INPUT_SIZE_LIMIT')
        result['candidate_sha256']=hashlib.sha256(candidate.encode()).hexdigest()
        got=_run(contract,spec,candidate,budget)
        if (not isinstance(got,dict) or set(got)!={'status','reason_code','reason','evidence','target_property_proven'} or
            got.get('status') not in ('RESOLVED','UNRESOLVED') or
            not isinstance(got.get('evidence'),dict) or not isinstance(got.get('reason'),str) or
            not isinstance(got.get('reason_code'),str) or
            got.get('target_property_proven') != (got.get('status')=='RESOLVED')):
            raise Abstain('OUTPUT_CONTRACT_INVALID')
        result.update(got)
    except Abstain as exc:result.update(status='INDETERMINATE',reason_code=str(exc),reason='Unsupported, invalid or bounded-out evidence; no proof claimed.',target_property_proven=False,evidence={})
    except Exception as exc:result.update(status='INDETERMINATE',reason_code='PARSER_OR_INTERNAL_EXCEPTION',reason=type(exc).__name__,target_property_proven=False,evidence={})
    finally:
        if budget:result['budget']={'steps_used':budget.steps,'limits':budget.limits}
    return result
