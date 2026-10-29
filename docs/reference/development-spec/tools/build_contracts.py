"""Generate specification contracts and fixtures, not the application."""
from pathlib import Path
import datetime, hashlib, json, shutil, struct
ROOT=Path(__file__).resolve().parents[1]
PREFIX='urn:fpga:contracts:'
def write(rel,value):
    path=ROOT/rel; path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        back=ROOT/'内部校验'/'备份'/datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')/rel
        back.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,back)
    data=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(data,encoding='utf-8'); tmp.replace(path)
def obj(p,required=None):return dict(type='object',properties=p,required=list(p) if required is None else required,additionalProperties=False)
def enum(*v):return dict(type='string',enum=list(v))
def arr(t,**kw):return dict(type='array',items=t,**kw)
def integer(lo=0,hi=None):return dict(type='integer',minimum=lo,**({'maximum':hi} if hi is not None else {}))
def ref(n):return {'$ref':PREFIX+n+':1.0'}
def nullable(t):return {'anyOf':[t,{'type':'null'}]}
def common(p):return obj({'schema_version':{'const':'1.0'},**p})
S={'type':'string','minLength':1}; TXT={'type':'string'}; B={'type':'boolean'}
U={'type':'string','format':'uuid'}; T={'type':'string','format':'date-time'}
H={'type':'string','pattern':'^[0-9a-f]{64}$'}
M={'type':'string','pattern':r'^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$'}
P={'type':'string','pattern':r'^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*[:\\]).+$'}
s={}
s['ArtifactRef']=obj({'id':U,'sha256':H,'kind':S})
s['Evidence']=obj({'source_id':U,'source_uri':S,'locator':S,'content_sha256':H,'retrieved_at':T,'license':S,'acl_scope':S,'text':S})
s['RequirementSpec']=common({'project_id':U,'revision_id':U,'title':S,'goal':S,
 'facts':arr(obj({'key':S,'value':S,'unit':nullable(S),'status':enum('confirmed','proposed','unknown'),'source_refs':arr(U)})),
 'questions':arr(obj({'id':U,'key':S,'question':S,'blocking':B,'answer':nullable(S)})),
 'acceptance_ids':arr(S),'out_of_scope':arr(S),'approved_by':nullable(U),'approved_at':nullable(T)})
s['SystemContract']=common({'project_id':U,'revision_id':U,'requirement_sha256':H,'channels':{'type':'integer','enum':[8,16]},
 'clock_hz':{'const':100000000},'sample_dividers':{'const':[10,100]},'capture_depth':{'const':4096},
 'sample_bytes':{'type':'integer','enum':[1,2]},'trigger_modes':{'const':['software','rising']},
 'reset':{'const':'synchronous_active_high'},'wire_protocol':{'const':'capture/1.0'},'uart_baud':{'const':115200},
 'board_profile_id':nullable(U),'schematic_status':enum('reference_unbound','bound_unreviewed','electrically_reviewed'),
 'register_map_sha256':H,'acceptance_sha256':H,
 'modules':arr(obj({'name':S,'source_path':P,'owner':S,'input_artifacts':arr(U)}),minItems=1),'approved_by':nullable(U),'approved_at':nullable(T)})
s['SystemContract']['allOf']=[{'if':{'properties':{'channels':{'const':c}}},'then':{'properties':{'sample_bytes':{'const':c//8}}}} for c in (8,16)]
s['BoardProfile']=common({'id':U,'kind':enum('virtual','physical'),'name':S,'device_mpn':nullable(S),'package':nullable(S),
 'clock_hz':integer(1),'clock_source':S,'bank_voltages':arr(obj({'bank':S,'voltage_mv':integer(1)})),
 'pins':arr(obj({'signal':S,'pin':S,'bank':S,'io_standard':S})),'source_refs':arr(U),'tool_profile_id':nullable(U),
 'electrical_review_id':nullable(U),'programming_allowed':B})
s['BoardProfile']['allOf']=[{'if':{'properties':{'kind':{'const':'virtual'}}},'then':{'properties':{
 'programming_allowed':{'const':False},'device_mpn':{'type':'null'},'pins':{'maxItems':0}}}}]
s['AcceptanceSpec']=common({'project_id':U,'revision_id':U,'tests':arr(obj({'id':S,'requirement_keys':arr(S),
 'level':enum('unit','integration','eda','security','e2e','manual'),'fixture':S,'expected':S,'blocking':B,'protected':B,'timeout_seconds':integer(1,3600)}),minItems=1)})
s['TaskEnvelope']=common({'task_id':U,'run_id':U,'project_id':U,'revision_id':U,'attempt':integer(1,10),'fencing_token':integer(1),
 'agent_role':S,'goal':S,'input_artifacts':arr(ref('ArtifactRef')),'output_schema':S,'model_profile_version_id':U,
 'extension_version_ids':arr(U),'skill_version_ids':arr(U),'allowed_tools':arr(S),'workspace_relpath':P,'deadline':T,
 'max_cost_cny':M,'approval_ids':arr(U),'idempotency_key':S})
s['AgentResult']=common({'task_id':U,'revision_id':U,'status':enum('completed','needs_input','failed'),
 'artifacts':arr(ref('ArtifactRef')),'questions':arr(ref('TaskQuestion')),'diagnostics':arr(S),'evidence':arr(ref('Evidence')),
 'usage':obj({'input_tokens':nullable(integer()),'output_tokens':nullable(integer()),'actual_cost_cny':nullable(M)})})
s['ToolResult']=common({'call_id':U,'task_id':U,'revision_id':U,'status':enum('passed','failed','timeout','cancelled','blocked','not_run'),
 'tool_version':S,'input_sha256':H,'started_at':T,'finished_at':T,'exit_code':nullable({'type':'integer'}),
 'artifact_refs':arr(ref('ArtifactRef')),'log_ref':nullable(ref('ArtifactRef')),'summary':S})
s['VerificationEvidence']=common({'id':U,'project_id':U,'revision_id':U,'task_id':U,'test_id':S,'input_sha256':H,
 'suite_sha256':H,'environment_sha256':H,'tool_version':S,'template_id':S,'seed':integer(0,4294967295),
 'status':enum('NOT_RUN','PASS','FAIL','INDETERMINATE'),'mode':enum('rtl_simulation','hardware','replay','mock','static'),
 'exit_code':nullable({'type':'integer'}),'started_at':T,'finished_at':T,'report':ref('ArtifactRef'),'issuer_id':U,
 'auth_tag':obj({'algorithm':{'const':'Ed25519'},'key_id':S,'signature':{'type':'string','pattern':'^[A-Za-z0-9_-]{86}$'}})})
s['EvidenceValidity']=common({'evidence_id':U,'current_revision_id':U,
 'effective_status':enum('NOT_RUN','PASS','FAIL','INDETERMINATE','STALE'),'stale_reason':nullable(S),
 'signature_valid':B,'evaluated_at':T})
s['ModelProfile']=common({'id':U,'display_name':S,'provider_kind':enum('deepseek','dashscope','openai_compatible','mock'),
 'base_url':{'type':'string','format':'uri'},'model_id':S,'runtime_origin':enum('CN','test_only'),'secret_ref':nullable(U),
 'capabilities':arr(enum('text','vision','tools','json','embedding','rerank'),uniqueItems=True),'context_limit':integer(1),
 'max_output_tokens':integer(1),'timeout_seconds':integer(1,600),'price_input_per_million_cny':nullable(M),
 'price_output_per_million_cny':nullable(M),'daily_budget_cny':M,'fallback_profile_ids':arr(U,uniqueItems=True),'enabled':B})
s['ModelResponse']=common({'status':enum('completed','incomplete','refused','failed','cancelled','unknown'),
 'text':TXT,'requested_model':S,'actual_model':nullable(S),'provider_request_id':nullable(S),'input_tokens':nullable(integer()),
 'output_tokens':nullable(integer()),'usage_complete':B,'preflight_count_kind':enum('exact','estimated','unavailable'),
 'latency_ms':integer(),'error_code':nullable(S),'cost_state':enum('actual','estimated','unknown'),'actual_cost_cny':nullable(M)})
s['Permission']=obj({'capability':enum('read_artifact','write_candidate','network','git_write','cad_write','spawn_tool','read_knowledge','render_panel'),
 'scope':S,'approval_required':B})
s['PluginManifest']=common({'id':{'type':'string','pattern':'^[a-z][a-z0-9.-]{2,63}$'},
 'version':{'type':'string','pattern':r'^[0-9]+\.[0-9]+\.[0-9]+$'},'kind':enum('mcp','knowledge','model','quote','skill','ui','bundle'),
 'publisher':S,'license':S,'source_ref':S,'payload_sha256':H,'signature':nullable(S),'engine_api':{'const':'1.0'},
 'entrypoint':S,'transport':enum('stdio','streamable_http','https_json','static','skill'),'config_schema_path':P,
 'permissions':arr(ref('Permission')),'dependencies':arr(obj({'id':S,'version':S,'payload_sha256':H})),
 'healthcheck':obj({'method':S,'timeout_seconds':integer(1,60)}),
 'data_policy':obj({'retention_days':integer(0,3650),'export_allowed':B,'external_send':B}),
 'migration_steps':arr(obj({'from_version':S,'to_version':S,'script_path':P,'reversible':B}))})
s['ConnectionConfig']=common({'name':S,'kind':enum('mcp','knowledge','quote','registry'),
 'transport':enum('stdio','streamable_http','https_json','static'),'endpoint':nullable(S),'managed_package_id':nullable(U),
 'secret_ref':nullable(U),'schema_sha256':nullable(H),'permissions':arr(ref('Permission')),
 'config_artifact_id':nullable(U),'timeout_seconds':integer(1,600)})
s['SkillSpec']=common({'name':S,'version':S,'source_ref':S,'content_artifact_id':U,'content_sha256':H,'license':S,
 'dependencies':arr(S),'permissions':arr(ref('Permission'))})
s['Binding']=obj({'agent_role':S,'model_profile_version_id':U,'skill_version_ids':arr(U),'extension_version_ids':arr(U),
 'collection_ids':arr(U),'allowed_tools':arr(S)})
s['Collection']=obj({'name':S,'description':TXT,'embedding_profile_version_id':nullable(U),'embedding_dimension':nullable(integer(1,8192)),
 'acl_member_ids':arr(U),'external_connection_id':nullable(U)})
s['DocumentInput']=obj({'upload_id':U,'collection_id':nullable(U),'scope':enum('task_only','project_knowledge'),
 'license':S,'external_send_allowed':B})
s['QuoteOffer']=common({'id':U,'supplier':S,'country_class':enum('domestic','overseas'),'manufacturer':S,'mpn':S,
 'sku':nullable(S),'package':S,'condition':enum('new','used','unknown'),'currency':enum('CNY','USD','EUR','HKD'),
 'price_breaks':arr(obj({'min_qty':integer(1),'unit_price':M}),minItems=1),'moq':integer(1),'order_multiple':integer(1),
 'stock_qty':nullable(integer()),'lead_days':nullable(integer()),'tax_included':nullable(B),'tax_rate':nullable(M),
 'shipping':nullable(M),'observed_at':T,'expires_at':T,'source_uri':S,'evidence_sha256':H,
 'data_mode':enum('live','authorized_import','fixture'),'reuse_policy':S})
s['ChangeRequest']=obj({'parent_revision_id':U,'original_text':S,'proposed_requirement_artifact_id':U,'requested_budget_cny':M})
s['ApprovalRequest']=obj({'action':enum('freeze','apply_change','release','git_publish','enable_extension','external_send'),
 'target_id':U,'target_sha256':H,'expires_at':T,'reason':S})
s['ApprovalDecision']=obj({'expected_version':integer(1),'decision':enum('approve','reject'),'reason':S})
s['SearchRequest']=obj({'collection_ids':arr(U,minItems=1),'query':S,'top_k':integer(1,20),'document_version_ids':arr(U)})
s['SearchResponse']=obj({'hits':arr(ref('Evidence')),'partial':B,'warnings':arr(S)})
s['Event']=common({'id':integer(1),'project_id':U,'type':S,'resource_id':U,'resource_version':integer(1),
 'occurred_at':T,'request_id':U,'summary':S})
s['AuditEvent']=common({'id':U,'sequence':integer(1),'project_id':nullable(U),'audit_scope':enum('project','system'),
 'actor_id':U,'action':S,'target_id':nullable(U),'target_version':nullable(integer(1)),'occurred_at':T,'request_id':U,'summary':S})
s['RunSnapshot']=common({'run_id':U,'project_id':U,'revision_id':U,'input_artifacts':arr(ref('ArtifactRef')),
 'model_profile_version_ids':arr(U),'skill_version_ids':arr(U),'extension_version_ids':arr(U),
 'acceptance_sha256':H,'toolchain_sha256':H,'generator_sha256':H,'price_version_id':nullable(U),
 'deadline':T,'max_cost_cny':M,'created_at':T})
s['ModelRequest']=common({'request_id':U,'model_profile_version_id':U,
 'messages':arr(obj({'role':enum('system','user','assistant','tool'),'content':TXT,'artifact_refs':arr(ref('ArtifactRef'))}),minItems=1),
 'output_schema_id':nullable(S),'max_output_tokens':integer(1),'deadline':T,'budget_reservation_id':U})
s['QuoteLookup']=common({'manufacturer':S,'mpn':S,'package':S,'quantity':integer(1),
 'country_class':enum('domestic','overseas'),'currency':enum('CNY','USD','EUR','HKD'),'max_lead_days':nullable(integer())})
s['QuoteOffers']=obj({'offers':arr(ref('QuoteOffer')),'partial':B,'warnings':arr(S)})
s['AdapterRequest']=common({'invocation_id':U,'project_id':U,'revision_id':U,'capability':S,
 'operation':S,'input_schema':S,'input':{'type':'object'},'deadline':T,'approval_ids':arr(U),'idempotency_key':S})
s['AdapterResponse']=common({'invocation_id':U,'status':enum('completed','failed','timeout','denied'),
 'output_schema':S,'output':nullable({'type':'object'}),'diagnostics':arr(S),'request_id':nullable(S)})
s['ApiError']=obj({'error':obj({'code':S,'message':S,'request_id':U,'retryable':B,'field_errors':arr(obj({'path':S,'message':S}))})})
s['ActionResult']=obj({'resource_id':U,'state':S,'run_id':nullable(U),'approval_id':nullable(U),'event_cursor':integer()})
s['ProjectInput']=obj({'name':S,'description':TXT,'team_id':U})
s['ProjectPatch']=obj({'name':S,'description':TXT},required=[])
s['RevisionInput']=obj({'parent_id':nullable(U),'requirement_artifact_id':U,'contract_artifact_id':nullable(U),'acceptance_artifact_id':U})
s['RunRequest']=obj({'revision_id':U,'mode':enum('simulate','change','deliver'),'max_cost_cny':M})
s['DeviceSessionInput']=obj({'revision_id':U,'mode':enum('rtl_simulation','replay','mock','hardware'),
 'capture_artifact_id':nullable(U),'board_profile_id':nullable(U)})
s['DeviceCommand']=obj({'action':enum('get_caps','set_config','arm','read_capture','status','abort'),'sequence':integer(0,65535),
 'payload_hex':{'type':'string','pattern':'^([0-9a-f]{2})*$'}})
s['DeviceCapabilities']=obj({'channels':{'type':'integer','enum':[8,16]},'sample_bytes':{'type':'integer','enum':[1,2]},
 'clock_hz':integer(1),'max_depth':integer(16,4096),'max_payload_bytes':{'const':1024},'feature_bits':integer()})
s['DeviceStatus']=obj({'state':enum('IDLE','ARMED','CAPTURING','DONE','ABORTED','ERROR'),'channels':{'type':'integer','enum':[8,16]},
 'trigger_mode':integer(0,1),'overflow':B,'capture_id':integer(0,4294967295),'sample_count':integer(0,4096),'error_code':integer(0,65535)})
s['CaptureChunk']=obj({'capture_id':integer(1,4294967295),'offset_samples':integer(0,4095),'count_samples':integer(1,1012),
 'sample_bytes':{'type':'integer','enum':[1,2]},'samples_hex':{'type':'string','pattern':'^([0-9a-f]{2})+$'}})
s['DeviceReply']={'oneOf':[obj({'action':{'const':a},'data':sc}) for a,sc in [
 ('get_caps',ref('DeviceCapabilities')),('status',ref('DeviceStatus')),('read_capture',ref('CaptureChunk')),
 ('arm',obj({'capture_id':integer(1,4294967295)})),('abort',obj({'capture_id':integer(0,4294967295)})),('set_config',obj({}))]]}
s['DeviceCommandResult']=obj({'command_id':U,'session_id':U,'action':S,'sequence':integer(0,65535),
 'state':enum('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED'),'reply':nullable(ref('DeviceReply')),
 'error':nullable(ref('ApiError')),'response_frame_hex':nullable({'type':'string','pattern':'^([0-9a-f]{2})+$'}),
 'capture_ref':nullable(ref('ArtifactRef'))})
s['CaptureRecord']=common({'id':U,'session_id':U,'project_id':U,'revision_id':U,'capture_id':integer(1,4294967295),
 'mode':enum('rtl_simulation','replay','mock','hardware'),'channels':{'type':'integer','enum':[8,16]},
 'sample_bytes':{'type':'integer','enum':[1,2]},'clock_hz':integer(1),'divider':{'type':'integer','enum':[10,100]},
 'sample_count':integer(16,4096),'contract_sha256':H,'samples':ref('ArtifactRef'),'metadata':ref('ArtifactRef'),'captured_at':T})
s['MemberInput']=obj({'user_id':U,'role':enum('admin','developer','reviewer','viewer')})
s['UserInput']=obj({'username':S,'global_role':enum('admin','user'),'enabled':B})
s['SecretInput']=obj({'label':S,'value':S})
s['SecretMeta']=obj({'id':U,'label':S,'created_at':T,'rotated_at':nullable(T),'configured':B})
s['BudgetInput']=obj({'name':S,'scope':enum('global','project'),'project_id':nullable(U),'daily_limit_cny':M,'per_run_limit_cny':M})
s['UploadInit']=obj({'filename':S,'content_type':S,'bytes':integer(1,26214400),'sha256':H})
s['UploadTicket']=obj({'upload_id':U,'put_path':S,'expires_at':T})
s['UploadComplete']=obj({'sha256':H})
s['Login']=obj({'username':S,'password':S})
s['Session']=obj({'user_id':U,'global_role':enum('admin','user'),'project_roles':arr(obj({'project_id':U,
 'role':enum('admin','developer','reviewer','viewer')})),'csrf_token':S})
s['Health']=obj({'status':enum('ok','degraded'),'api_version':S,'database_ready':B,'scheduler_ready':B,'eda_ready':B,'cad_ready':B})
s['JobClaim']=obj({'worker_id':U,'capabilities':arr(S),'tool_profile_sha256':H})
s['JobLease']=obj({'job_id':nullable(U),'task':nullable(ref('TaskEnvelope')),'lease_until':nullable(T)})
s['JobHeartbeat']=obj({'worker_id':U,'attempt':integer(1),'fencing_token':integer(1),'progress_message':S})
s['JobComplete']=obj({'worker_id':U,'attempt':integer(1),'fencing_token':integer(1),'result':{'oneOf':[ref('ToolResult'),ref('AgentResult')]}})
s['TaskQuestion']=obj({'id':U,'task_id':U,'key':S,'question':S,'blocking':B,'answer':nullable(S),'answered_by':nullable(U),'answered_at':nullable(T)})
s['TaskAttempt']=obj({'attempt':integer(1,10),'fencing_token':integer(1),'worker_id':nullable(U),'state':S,
 'started_at':nullable(T),'finished_at':nullable(T),'cancel_confirmed':B,'result_artifact_id':nullable(U)})
s['TaskRecord']=obj({'task_id':U,'run_id':U,'version':integer(1),
 'state':enum('QUEUED','LEASED','RUNNING','SUCCEEDED','FAILED','WAITING_INPUT','CANCEL_REQUESTED','CANCELLED','LOST'),
 'envelope':ref('TaskEnvelope'),'questions':arr(ref('TaskQuestion')),'attempts':arr(ref('TaskAttempt')),
 'waiting_reason':nullable(S),'result':nullable({'oneOf':[ref('AgentResult'),ref('ToolResult')]})})
s['BackupRequest']=obj({'reason':S})
s['RestoreRequest']=obj({'backup_id':U,'approval_id':U,'reason':S})
s['Answer']=obj({'question_id':U,'revision_id':U,'value':S,'unit':nullable(S)})
s['CommandParameters']=obj({'target_version_id':nullable(U),'artifact_id':nullable(U),'approval_id':nullable(U),'reason':S,'preserve_history':B})
for n,sc in s.items():write(f'contracts/{n}.schema.json',{'$schema':'https://json-schema.org/draft/2020-12/schema','$id':PREFIX+n+':1.0','title':n,**sc})
def local(v):
    if isinstance(v,dict):return {k:('#/components/schemas/'+x[len(PREFIX):-4] if k=='$ref' and x.startswith(PREFIX) else local(x)) for k,x in v.items()}
    if isinstance(v,list):return [local(x) for x in v]
    return v
c=local(json.loads(json.dumps(s))); paths={}; catalog={}
def op(path,method,name,req=None,res='ActionResult',status='200',security='session',roles=None,extra=None):
    params=[{'name':x[1:-1],'in':'path','required':True,'schema':U} for x in path.split('/') if x.startswith('{')]
    if method in ('post','put','patch','delete') and security=='session':params.append({'name':'X-CSRF-Token','in':'header','required':True,'schema':S})
    if method in ('put','patch','delete') and name not in ('logout','uploadContent'):params.append({'name':'If-Match','in':'header','required':True,'schema':S})
    if method=='post' and name!='login':params.append({'name':'Idempotency-Key','in':'header','required':True,'schema':S})
    if name.startswith('list_'):params.extend([{'name':'cursor','in':'query','schema':S},{'name':'limit','in':'query','schema':integer(1,100)}])
    responses={status:{'description':'Success'}}
    if res:responses[status]['content']={'application/json':{'schema':{'$ref':'#/components/schemas/'+res}}}
    responses[status]['headers']={'ETag':{'description':'Resource version for subsequent conditional requests','schema':S}}
    for code in ('400','401','403','404','409','410','412','413','415','422','428','429','503'):
        responses[code]={'description':'See stable ApiError code','content':{'application/json':{'schema':{'$ref':'#/components/schemas/ApiError'}}}}
    d={'operationId':name,'summary':name,'parameters':params,'responses':responses,'x-roles':roles or ['developer','reviewer','admin'],
       'security':[] if security=='public' else [{('workerBearer' if security=='worker' else 'verifierBearer' if security=='verifier' else 'sessionCookie'):[]}]}
    if req:d['requestBody']={'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/'+req}}}}
    if extra:d.update(extra)
    paths.setdefault(path,{})[method]=d
def resource(coll,payload,scope='project',mutable=False,delete=False,commands=(),create=True,base=None):
    base=base or ('/projects/{project_id}/'+coll if scope=='project' else '/admin/'+coll)
    key=coll.replace('-','_'); name=''.join(x.title() for x in coll.split('-'))+'Resource'
    c[name]=obj({'id':U,'project_id':nullable(U),'version':integer(1),'etag':S,'state':S,
      'payload':{'$ref':'#/components/schemas/'+payload},'created_at':T,'updated_at':T})
    c[name+'List']=obj({'items':arr({'$ref':'#/components/schemas/'+name}),'next_cursor':nullable(S)})
    roles=['admin'] if scope=='admin' else ['viewer','developer','reviewer','admin']
    op(base,'get','list_'+key,res=name+'List',roles=roles); op(base+'/{resource_id}','get','get_'+key,res=name,roles=roles)
    if create:op(base,'post','create_'+key,payload,name,'202' if coll=='runs' else '201',roles=['admin'] if scope=='admin' else None)
    if mutable:op(base+'/{resource_id}','put','replace_'+key,payload,name,roles=['admin'] if scope=='admin' else None)
    if delete:op(base+'/{resource_id}','delete','delete_'+key,roles=['admin'] if scope=='admin' else ['reviewer','admin'])
    if commands:
        cn=name+'Command'; c[cn]=obj({'action':enum(*commands),'expected_version':integer(1),'parameters':{'$ref':'#/components/schemas/CommandParameters'}})
        op(base+'/{resource_id}/commands','post','command_'+key,cn,status='202',roles=['admin'] if scope=='admin' else None)
    catalog[coll]={'scope':scope,'payload_schema':payload,'resource_schema':name,'create':create,'replace_draft_only':mutable,
      'delete_if_unreferenced':delete,'commands':list(commands)}
op('/auth/session','post','login','Login','Session',security='public')
op('/auth/session','get','session',res='Session',roles=['viewer','developer','reviewer','admin'])
op('/auth/session','delete','logout',res=None,status='204',roles=['viewer','developer','reviewer','admin'])
resource('projects','ProjectInput',base='/projects')
paths['/projects/{project_id}']=paths.pop('/projects/{resource_id}')
for p in paths['/projects/{project_id}']['get']['parameters']:
    if p['name']=='resource_id':p['name']='project_id'
op('/projects/{project_id}','patch','patch_project','ProjectPatch','ProjectsResource')
resource('revisions','RevisionInput',commands=('validate','freeze','release','rollback'))
resource('runs','RunRequest',commands=('cancel','retry'))
resource('changes','ChangeRequest',commands=('preview','apply','cancel'))
resource('approvals','ApprovalRequest')
op('/projects/{project_id}/approvals/{resource_id}/decision','post','decide_approval','ApprovalDecision',roles=['reviewer','admin'])
resource('collections','Collection',mutable=True,delete=True,commands=('reindex','test','export'))
resource('documents','DocumentInput',delete=True,commands=('parse','retry','publish','revoke'))
resource('quotes','QuoteOffer',commands=('refresh','compare','export'))
resource('bindings','Binding',mutable=True,delete=True,commands=('test','enable','disable'))
resource('members','MemberInput',mutable=True,delete=True)
for endpoint,methods in paths.items():
    if '/members' in endpoint:
        for d in methods.values():d['x-roles']=['admin']
resource('device-sessions','DeviceSessionInput',commands=('close',))
resource('artifacts','ArtifactRef',create=False); resource('evidence','VerificationEvidence',create=False)
for coll,payload,commands in [
 ('model-profiles','ModelProfile',('test','enable','disable','rollback','export','import')),
 ('mcp-connections','ConnectionConfig',('discover','test','approve','enable','disable','rollback','start','stop','rotate')),
 ('knowledge-connections','ConnectionConfig',('test','approve','enable','disable','rollback','rotate')),
 ('quote-connections','ConnectionConfig',('test','approve','enable','disable','rollback','rotate')),
 ('extensions','PluginManifest',('validate','test','trust','revoke','approve','enable','disable','rollback','uninstall','export')),
 ('skills','SkillSpec',('validate','test','approve','enable','disable','rollback','uninstall','export')),
 ('registries','ConnectionConfig',('test','refresh','trust','revoke','enable','disable')),
 ('budgets','BudgetInput',('enable','disable')),('users','UserInput',('enable','disable'))]:
    resource(coll,payload,'admin',mutable=True,delete=True,commands=commands)
op('/admin/secrets','post','create_secret','SecretInput','SecretMeta','201',roles=['admin'])
op('/admin/secrets/{resource_id}','put','rotate_secret','SecretInput','SecretMeta',roles=['admin'])
op('/admin/secrets/{resource_id}','get','get_secret_metadata',res='SecretMeta',roles=['admin'])
op('/admin/secrets/{resource_id}','delete','revoke_secret',roles=['admin'])
op('/projects/{project_id}/clarifications/answers','post','answer_clarification','Answer')
op('/projects/{project_id}/knowledge/search','post','search_knowledge','SearchRequest','SearchResponse',roles=['viewer','developer','reviewer','admin'])
op('/projects/{project_id}/uploads','post','init_upload','UploadInit','UploadTicket','201')
op('/projects/{project_id}/uploads/{resource_id}/complete','post','complete_upload','UploadComplete')
op('/projects/{project_id}/uploads/{resource_id}/content','put','uploadContent',res=None,status='204',extra={
 'requestBody':{'required':True,'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}}})
op('/projects/{project_id}/artifacts/{resource_id}/content','get','download_artifact',res=None,roles=['viewer','developer','reviewer','admin'],extra={
 'responses':{'200':{'description':'ACL-checked content','content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}},'404':{'description':'Unavailable'}}})
op('/projects/{project_id}/device-sessions/{resource_id}/device-commands','post','device_command','DeviceCommand',status='202')
op('/projects/{project_id}/events','get','stream_events',res=None,roles=['viewer','developer','reviewer','admin'],extra={
 'responses':{'200':{'description':'SSE id=Event.id; data validates against Event','content':{'text/event-stream':{'schema':{'type':'string'}}}},'410':{'description':'Cursor expired; resync'}}})
paths['/projects/{project_id}/events']['get']['parameters'].append({'name':'Last-Event-ID','in':'header','schema':S})
op('/health','get','health',res='Health',security='public')
c['AuditList']=obj({'items':arr({'$ref':'#/components/schemas/AuditEvent'}),'next_cursor':nullable(S)})
op('/admin/audit','get','list_audit',res='AuditList',roles=['admin'])
op('/admin/backups','post','backup','BackupRequest',status='202',roles=['admin'])
op('/admin/restores','post','restore','RestoreRequest',status='202',roles=['admin'])
op('/worker/jobs/claim','post','claim_job','JobClaim','JobLease',security='worker',roles=['worker'])
op('/worker/jobs/{job_id}/heartbeat','post','heartbeat_job','JobHeartbeat',security='worker',roles=['worker'])
op('/worker/jobs/{job_id}/complete','post','complete_job','JobComplete',security='worker',roles=['worker'])
op('/worker/jobs/{job_id}/cancel-state','get','job_cancel_state',security='worker',roles=['worker'])
op('/projects/{project_id}/runs/{run_id}/snapshot','get','get_run_snapshot',res='RunSnapshot',roles=['viewer','developer','reviewer','admin'])
c['TaskList']=obj({'items':arr({'$ref':'#/components/schemas/TaskRecord'}),'next_cursor':nullable(S)})
op('/projects/{project_id}/runs/{run_id}/tasks','get','list_run_tasks',res='TaskList',roles=['developer','reviewer','admin'])
op('/projects/{project_id}/runs/{run_id}/tasks/{task_id}','get','get_task',res='TaskRecord',roles=['developer','reviewer','admin'])
op('/projects/{project_id}/runs/{run_id}/tasks/{task_id}/answers','post','answer_task_question','Answer')
op('/projects/{project_id}/device-sessions/{session_id}/commands/{command_id}','get','get_device_command',res='DeviceCommandResult',roles=['viewer','developer','reviewer','admin'])
c['CaptureList']=obj({'items':arr({'$ref':'#/components/schemas/CaptureRecord'}),'next_cursor':nullable(S)})
op('/projects/{project_id}/device-sessions/{session_id}/captures','get','list_captures',res='CaptureList',roles=['viewer','developer','reviewer','admin'])
op('/projects/{project_id}/device-sessions/{session_id}/captures/{resource_id}','get','get_capture',res='CaptureRecord',roles=['viewer','developer','reviewer','admin'])
op('/worker/jobs/{job_id}/uploads','post','worker_init_upload','UploadInit','UploadTicket','201',security='worker',roles=['worker'])
op('/worker/jobs/{job_id}/uploads/{resource_id}/content','put','worker_upload_content',res=None,status='204',security='worker',roles=['worker'],extra={
 'requestBody':{'required':True,'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}}})
paths['/worker/jobs/{job_id}/uploads/{resource_id}/content']['put']['parameters']=[p for p in paths['/worker/jobs/{job_id}/uploads/{resource_id}/content']['put']['parameters'] if p['name']!='If-Match']
op('/worker/jobs/{job_id}/uploads/{resource_id}/complete','post','worker_complete_upload','UploadComplete','ArtifactRef',security='worker',roles=['worker'])
op('/worker/jobs/{job_id}/artifacts/{resource_id}/content','get','worker_download_input',res=None,security='worker',roles=['worker'],extra={
 'responses':{'200':{'description':'Lease-bound allowlisted input bytes','content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}},'404':{'description':'Unavailable'}}})
op('/verification/evidence','post','ingest_verification_evidence','VerificationEvidence',security='verifier',roles=['verifier'])
op('/projects/{project_id}/evidence/{resource_id}/validity','get','get_evidence_validity',res='EvidenceValidity',roles=['viewer','developer','reviewer','admin'])
write('api/openapi.json',{'openapi':'3.1.0','info':{'title':'FPGA Agent Local Workbench','version':'1.0.0',
 'description':'Normative specification only. Not an implemented service. State and ACL rules in the specification also apply.'},
 'servers':[{'url':'http://127.0.0.1:8765/api/v1'}],'paths':paths,'components':{'schemas':c,'securitySchemes':{
 'sessionCookie':{'type':'apiKey','in':'cookie','name':'fpga_session'},'workerBearer':{'type':'http','scheme':'bearer'},
 'verifierBearer':{'type':'http','scheme':'bearer'}}}})
write('api/resource-catalog.json',catalog)
write('api/operations.json',{'operation_count':sum(map(len,paths.values())),
 'operation_ids':[d['operationId'] for methods in paths.values() for d in methods.values()]})
regs=[('CTRL',0,'wo','bit0 ARM pulse; bit1 ABORT pulse; ABORT wins; other bits zero'),
 ('STATUS',4,'ro','state bits0..2; overflow bit8; error bit9; other bits zero'),
 ('SAMPLE_DIV',8,'rw','10 or100; reset10; writes only IDLE/DONE/ABORTED/ERROR'),
 ('CHANNEL_MASK',12,'rw','reset all; nonzero; out-of-range bits rejected'),('TRIGGER_MODE',16,'rw','0 software;1 rising; reset0'),
 ('TRIGGER_CHANNEL',20,'rw','0..CHANNELS-1; enabled in rising; reset0'),('DEPTH',24,'rw','power of2 in16..4096; reset4096'),
 ('SAMPLE_COUNT',28,'ro','0..DEPTH; zero on ARM'),('CAPTURE_ID',32,'ro','uint32 nonzero per accepted ARM; reset0; wrap skips0'),
 ('ERROR_CODE',36,'ro','last error; clear by valid SET_CONFIG/reset'),('CAPABILITIES',40,'ro','CHANNELS bits0..7; sample_bytes bits8..15; protocol major bits16..23')]
write('protocol/register-map.json',{'schema_version':'1.0','address_unit':'byte','data_bits':32,'bus':'single_cycle_ready_valid',
 'registers':[dict(name=n,offset=o,access=a,meaning=m) for n,o,a,m in regs]})
def crc(data):
    v=0xffff
    for b in data:
        v^=b<<8
        for _ in range(8):v=((v<<1)^0x1021)&0xffff if v&0x8000 else (v<<1)&0xffff
    return v
def frame(t,seq,p=b''):
    h=bytes([1,0,t,0])+struct.pack('<HH',seq,len(p)); return b'\xa5\x5a'+h+p+struct.pack('<H',crc(h+p))
vectors=[]
for name,t,seq,p in [('get_caps_request',1,1,b''),
 ('caps_8ch_response',0x81,1,struct.pack('<BBIHHI',8,1,100000000,4096,1024,3)),
 ('caps_16ch_response',0x81,1,struct.pack('<BBIHHI',16,2,100000000,4096,1024,3)),
 ('set_config_8ch_software',2,2,struct.pack('<BBHHI',0,0,10,255,4096)),
 ('set_config_16ch_rising',2,2,struct.pack('<BBHHI',1,15,100,65535,4096)),
 ('set_config_response',0x82,2,b''),('arm_request',3,3,b''),('arm_response',0x83,3,struct.pack('<I',1)),
 ('read_16ch_request',4,4,struct.pack('<IIH',1,0,3)),
 ('read_16ch_response',0x84,4,struct.pack('<IIHBBHHH',1,0,3,2,0,1,0x8000,0xffff)),
 ('status_request',5,5,b''),('status_response',0x85,5,struct.pack('<BBBBIIHH',3,16,0,0,1,4096,0,0)),
 ('abort_request',6,7,b''),('abort_response',0x86,7,struct.pack('<I',1)),
 ('error_stale_capture',0xff,6,struct.pack('<BH',4,5))]:
    vectors.append(dict(name=name,type=t,sequence=seq,payload_hex=p.hex(),frame_hex=frame(t,seq,p).hex()))
write('protocol/golden-vectors.json',{'protocol':'capture/1.0','crc_check_ascii':'123456789','crc_check_hex':'29b1','vectors':vectors})
write('protocol/errors.json',{1:'BAD_LENGTH',2:'UNSUPPORTED_VERSION',3:'UNKNOWN_TYPE',4:'INVALID_CONFIG',5:'STALE_CAPTURE',
 6:'BUSY',7:'NOT_READY',8:'RANGE',9:'SEQUENCE_CONFLICT',10:'CAPTURE_ABORTED'})
project='11111111-1111-4111-8111-111111111111'; revision='22222222-2222-4222-8222-222222222222'; z='0'*64
req={'schema_version':'1.0','project_id':project,'revision_id':revision,'title':'8 通道仿真采集仪','goal':'先突发采集再上传',
 'facts':[{'key':'channels','value':'8','unit':None,'status':'proposed','source_refs':[]},
 {'key':'sample_rate','value':'10000000','unit':'Hz','status':'proposed','source_refs':[]}],
 'questions':[],'acceptance_ids':['AC-RTL-01','AC-RTL-02'],'out_of_scope':['实板烧录','完整 PCB','连续实时 UART 上传'],'approved_by':None,'approved_at':None}
acc={'schema_version':'1.0','project_id':project,'revision_id':revision,'tests':[
 {'id':'AC-RTL-01','requirement_keys':['channels'],'level':'eda','fixture':'independent-pattern','expected':'8 channels in exact order','blocking':True,'protected':True,'timeout_seconds':120},
 {'id':'AC-RTL-02','requirement_keys':['sample_rate'],'level':'eda','fixture':'clock-enable-boundaries','expected':'sample every10 cycles','blocking':True,'protected':True,'timeout_seconds':120}]}
write('examples/requirement.json',req); write('examples/acceptance.json',acc)
def dh(p):return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
contract={'schema_version':'1.0','project_id':project,'revision_id':revision,'requirement_sha256':dh('examples/requirement.json'),
 'channels':8,'clock_hz':100000000,'sample_dividers':[10,100],'capture_depth':4096,'sample_bytes':1,
 'trigger_modes':['software','rising'],'reset':'synchronous_active_high','wire_protocol':'capture/1.0','uart_baud':115200,
 'board_profile_id':None,'schematic_status':'reference_unbound','register_map_sha256':dh('protocol/register-map.json'),
 'acceptance_sha256':dh('examples/acceptance.json'),'modules':[{'name':'capture_top','source_path':'products/analyzer/rtl/capture_top.sv','owner':'rtl','input_artifacts':[]}],
 'approved_by':None,'approved_at':None}
write('examples/system-contract-8ch.json',contract)
req16=json.loads(json.dumps(req)); req16['title']='16 通道仿真采集仪'; req16['revision_id']='33333333-3333-4333-8333-333333333333'; req16['facts'][0]['value']='16'
acc16=json.loads(json.dumps(acc)); acc16['revision_id']=req16['revision_id']; acc16['tests'][0]['expected']='16 channels in little-endian sample order'
write('examples/requirement-16ch.json',req16); write('examples/acceptance-16ch.json',acc16)
write('examples/system-contract-16ch.json',{**contract,'revision_id':req16['revision_id'],'channels':16,'sample_bytes':2,
 'requirement_sha256':dh('examples/requirement-16ch.json'),'acceptance_sha256':dh('examples/acceptance-16ch.json')})
write('examples/board-virtual.json',{'schema_version':'1.0','id':'44444444-4444-4444-8444-444444444444','kind':'virtual',
 'name':'virtual-capture-100mhz','device_mpn':None,'package':None,'clock_hz':100000000,'clock_source':'simulation',
 'bank_voltages':[],'pins':[],'source_refs':[],'tool_profile_id':None,'electrical_review_id':None,'programming_allowed':False})
write('examples/plugin.manifest.json',{'schema_version':'1.0','id':'example.knowledge','version':'1.0.0','kind':'knowledge',
 'publisher':'team-example','license':'MIT','source_ref':'local-fixture-not-installed','payload_sha256':z,'signature':None,
 'engine_api':'1.0','entrypoint':'https://example.invalid/search','transport':'https_json','config_schema_path':'config.schema.json',
 'permissions':[{'capability':'read_knowledge','scope':'bound_project','approval_required':False}],'dependencies':[],
 'healthcheck':{'method':'health','timeout_seconds':5},'data_policy':{'retention_days':0,'export_allowed':True,'external_send':True},'migration_steps':[]})
write('examples/quote-offer.json',{'schema_version':'1.0','id':'55555555-5555-4555-8555-555555555555','supplier':'TEST DOMESTIC',
 'country_class':'domestic','manufacturer':'TEST','mpn':'TEST-PART','sku':None,'package':'TEST','condition':'new','currency':'CNY',
 'price_breaks':[{'min_qty':1,'unit_price':'2'},{'min_qty':40,'unit_price':'1.5'}],'moq':10,'order_multiple':10,
 'stock_qty':1000,'lead_days':3,'tax_included':True,'tax_rate':'0.13','shipping':'10','observed_at':'2026-10-29T00:00:00Z',
 'expires_at':'2026-10-30T00:00:00Z','source_uri':'fixture://rounding','evidence_sha256':z,'data_mode':'fixture','reuse_policy':'not a market quote'})
write('examples/index.json',{'notice':'Unapproved specification fixtures, not runtime evidence.','schema_map':{
 'requirement.json':'RequirementSpec','requirement-16ch.json':'RequirementSpec','acceptance.json':'AcceptanceSpec',
 'acceptance-16ch.json':'AcceptanceSpec','system-contract-8ch.json':'SystemContract','system-contract-16ch.json':'SystemContract',
 'board-virtual.json':'BoardProfile','plugin.manifest.json':'PluginManifest','quote-offer.json':'QuoteOffer'}})
print(json.dumps({'schemas':len(s),'api_operations':sum(map(len,paths.values())),'golden_vectors':len(vectors)}))
