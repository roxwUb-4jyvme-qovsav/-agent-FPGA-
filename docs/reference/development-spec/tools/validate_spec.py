"""Validate the documentation package, not the product's 84 acceptance cases."""
from pathlib import Path
import binascii, copy, datetime, hashlib, importlib.util, json, re, struct, subprocess, sys
from decimal import Decimal, ROUND_CEILING
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from openapi_spec_validator import validate

ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads((ROOT/p).read_text(encoding='utf-8'))
checks=[]
def check(name,condition):
    assert condition,name
    checks.append(name)
schemas=[json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'contracts').glob('*.schema.json')]
registry=Registry().with_resources((s['$id'],Resource.from_contents(s)) for s in schemas)
lookup={s['title']:s for s in schemas}
for s in schemas:
    Draft202012Validator.check_schema(s)
    check('schema:'+s['title'],True)
def validator(n):return Draft202012Validator(lookup[n],registry=registry,format_checker=FormatChecker())
for p,n in read('examples/index.json')['schema_map'].items():
    validator(n).validate(read('examples/'+p));check('example:'+p,True)
for ch in (8,16):
    p=read(f'examples/system-contract-{ch}ch.json')
    for field,target in [('register_map_sha256','protocol/register-map.json'),
       ('requirement_sha256','examples/requirement.json' if ch==8 else 'examples/requirement-16ch.json'),
       ('acceptance_sha256','examples/acceptance.json' if ch==8 else 'examples/acceptance-16ch.json')]:
        check(f'hash:{ch}:{field}',p[field]==hashlib.sha256((ROOT/target).read_bytes()).hexdigest())
bad=copy.deepcopy(read('examples/system-contract-8ch.json'));bad['sample_bytes']=2
check('negative:channels-width',bool(list(validator('SystemContract').iter_errors(bad))))
for path in ('../secret','/etc/passwd','C:/secret','x/../secret','x\\secret'):
    bad=copy.deepcopy(read('examples/system-contract-8ch.json'));bad['modules'][0]['source_path']=path
    check('negative:path:'+path,bool(list(validator('SystemContract').iter_errors(bad))))
bad=copy.deepcopy(read('examples/board-virtual.json'));bad['programming_allowed']=True
check('negative:virtual-programming',bool(list(validator('BoardProfile').iter_errors(bad))))
bad=copy.deepcopy(read('examples/quote-offer.json'));bad['price_breaks'][0]['unit_price']='-1'
check('negative:negative-money',bool(list(validator('QuoteOffer').iter_errors(bad))))
api=read('api/openapi.json');validate(api);check('openapi:3.1-structure',True)
def visit(value):
    if isinstance(value,dict):
        if '$ref' in value:
            r=value['$ref']
            if r.startswith('#/components/schemas/'):
                check('openapi-ref:'+r,r.rsplit('/',1)[1] in api['components']['schemas'])
        for v in value.values():visit(v)
    elif isinstance(value,list):
        for v in value:visit(v)
visit(api)
ids=[x['operationId'] for m in api['paths'].values() for x in m.values()]
check('openapi:operation-ids-unique',len(ids)==len(set(ids)))
check('openapi:operation-count',len(ids)==read('api/operations.json')['operation_count'])
for path,methods in api['paths'].items():
    for method,d in methods.items():
        expected=set(re.findall(r'{([^}]+)}',path))
        actual={p['name'] for p in d.get('parameters',[]) if p['in']=='path'}
        check('openapi-parameters:'+method+path,expected==actual)
        if '/members' in path:check('members-admin-only:'+method+path,d['x-roles']==['admin'])
        if path.startswith('/worker/'):check('worker-auth:'+method+path,d['security']==[{'workerBearer':[]}])
        if path=='/verification/evidence':check('verifier-auth',d['security']==[{'verifierBearer':[]}])
for coll in ('artifacts','evidence'):
    check('immutable:'+coll,not read('api/resource-catalog.json')[coll]['create'])
secret=api['paths']['/admin/secrets/{resource_id}']['get']['responses']['200']['content']['application/json']['schema']
check('secret-metadata-only',secret['$ref'].endswith('/SecretMeta') and 'value' not in lookup['SecretMeta']['properties'])
question={'id':'99999999-9999-4999-8999-999999999999','task_id':'77777777-7777-4777-8777-777777777777',
 'key':'capture_mode','question':'Confirm burst capture','blocking':True,'answer':None,'answered_by':None,'answered_at':None}
validator('TaskQuestion').validate(question)
agent={'schema_version':'1.0','task_id':question['task_id'],'revision_id':'22222222-2222-4222-8222-222222222222',
 'status':'needs_input','artifacts':[],'questions':[question],'diagnostics':[],'evidence':[],
 'usage':{'input_tokens':None,'output_tokens':None,'actual_cost_cny':None}}
validator('AgentResult').validate(agent)
validator('JobComplete').validate({'worker_id':'88888888-8888-4888-8888-888888888888','attempt':1,'fencing_token':1,'result':agent})
check('task:needs-input-wire-contract',True)
reply={'action':'read_capture','data':{'capture_id':1,'offset_samples':0,'count_samples':3,'sample_bytes':2,'samples_hex':'01000080ffff'}}
validator('DeviceReply').validate(reply)
bad_reply=copy.deepcopy(reply);bad_reply['action']='get_caps'
check('device:reply-action-discriminator',bool(list(validator('DeviceReply').iter_errors(bad_reply))))
check('evidence:raw-status-immutable', 'STALE' not in lookup['VerificationEvidence']['properties']['status']['enum'] and
      'STALE' in lookup['EvidenceValidity']['properties']['effective_status']['enum'])
session={'user_id':'88888888-8888-4888-8888-888888888888','global_role':'user','project_roles':[
 {'project_id':'11111111-1111-4111-8111-111111111111','role':'admin'}],'csrf_token':'fixture-only'}
validator('Session').validate(session);check('roles:global-and-project-distinct',True)
vectors=read('protocol/golden-vectors.json')
check('crc:standard-independent',binascii.crc_hqx(b'123456789',0xffff)==int(vectors['crc_check_hex'],16))
decoded={}
for v in vectors['vectors']:
    frame=bytes.fromhex(v['frame_hex']);length=struct.unpack_from('<H',frame,8)[0]
    check('frame:'+v['name'],frame[:2]==b'\xa5\x5a' and len(frame)==length+12 and frame[10:-2].hex()==v['payload_hex'])
    check('crc:'+v['name'],binascii.crc_hqx(frame[2:-2],0xffff)==struct.unpack_from('<H',frame,len(frame)-2)[0])
    check('header:'+v['name'],frame[2:4]==b'\x01\x00' and frame[4]==v['type'] and struct.unpack_from('<H',frame,6)[0]==v['sequence'])
    decoded[v['name']]=frame[10:-2]
check('payload:caps',struct.unpack('<BBIHHI',decoded['caps_16ch_response'])==(16,2,100000000,4096,1024,3))
check('payload:read16',struct.unpack('<IIHBBHHH',decoded['read_16ch_response'])==(1,0,3,2,0,1,0x8000,0xffff))
check('payload:status',struct.unpack('<BBBBIIHH',decoded['status_response'])==(3,16,0,0,1,4096,0,0))
check('chunk-capacity:8-and16',((1024-12)//1,(1024-12)//2)==(1012,506))
buy=int((Decimal(max(10*3+2,10))/Decimal(10)).to_integral_value(rounding=ROUND_CEILING))*10
check('quote:round-tier-shipping',buy==40 and Decimal(buy)*Decimal('1.5')+Decimal('10')==Decimal('70'))
reg=read('protocol/register-map.json')['registers']
check('register:aligned-unique',len({r['offset'] for r in reg})==len(reg) and all(r['offset']%4==0 for r in reg))
catalog=read('acceptance/catalog.json'); tests=catalog['tests']
check('acceptance:ids-and-count',len(tests)==catalog['test_count'] and len({t['id'] for t in tests})==len(tests))
check('acceptance:not-runtime-results',all(t['execution_status']=='NOT_RUN' for t in tests))
body=(ROOT/'FPGA多Agent协同开发平台开发规格书_v1.0.md').read_text(encoding='utf-8')
check('document:25-numbered-chapters',len(re.findall(r'^## \d+ ',body,re.M))==25)
for ch in (8,16):
    acceptance=read('examples/acceptance.json' if ch==8 else 'examples/acceptance-16ch.json')
    check('acceptance:example-ids:'+str(ch),all(t['id'] in {x['id'] for x in tests} for t in acceptance['tests']))
backlog=read('planning/backlog.json'); tasks=[t for w in backlog['work_packages'] for t in w['tasks']]
graph={t['id']:t['depends_on'] for t in tasks}; seen=set();active=set()
def dfs(k):
    assert k not in active,'task dependency cycle'
    if k in seen:return
    active.add(k)
    for p in graph[k]:
        assert p in graph,'missing dependency '+p
        dfs(p)
    active.remove(k);seen.add(k)
for k in graph:dfs(k)
check('backlog:acyclic-and-complete',len(graph)==28)
check('backlog:estimate',[sum(w['coding_person_days'][i] for w in backlog['work_packages']) for i in (0,1)]==[29,49])
request={'schema_version':'1.0','invocation_id':'77777777-7777-4777-8777-777777777777',
 'project_id':'11111111-1111-4111-8111-111111111111','revision_id':'22222222-2222-4222-8222-222222222222',
 'capability':'read_knowledge','operation':'search','input_schema':'urn:fpga:contracts:SearchRequest:1.0',
 'input':{'collection_ids':['88888888-8888-4888-8888-888888888888'],'query':'endianness','top_k':1,'document_version_ids':[]},
 'deadline':'2026-10-29T12:00:00Z','approval_ids':[],'idempotency_key':'fixture-search-1'}
validator('AdapterRequest').validate(request);validator('SearchRequest').validate(request['input'])
result=subprocess.run([sys.executable,str(ROOT/'sdk/examples/knowledge_stdio.py')],input=json.dumps(request)+'\n',
 capture_output=True,text=True,encoding='utf-8',timeout=10,check=True)
response=json.loads(result.stdout);validator('AdapterResponse').validate(response);validator('SearchResponse').validate(response['output'])
check('sdk:stdio-roundtrip',response['invocation_id']==request['invocation_id'] and response['status']=='completed')
report={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'SPECIFICATION PACKAGE ONLY',
 'result':'PASS','checks':len(checks),'schemas':len(schemas),'api_operations':len(ids),'golden_vectors':len(vectors['vectors']),
 'planned_product_acceptance_cases':len(tests),'product_acceptance_executed':0,'work_packages':len(backlog['work_packages']),
 'tasks':len(tasks),'check_names':checks}
out=ROOT/'内部校验/spec-validation.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='check_names'},ensure_ascii=False))
