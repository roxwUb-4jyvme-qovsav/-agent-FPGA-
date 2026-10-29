export interface ResourceDefinition { key: string; label: string; description: string; schema?: string; scope: 'project' | 'admin'; commandSchema?: string; update?: boolean; remove?: boolean }

export const resources: Record<string, ResourceDefinition> = {
  revisions: { key: 'revisions', label: '工程版本', description: '需求、接口契约与验收规范形成同一条可追溯的版本基线。', schema: 'RevisionInput', scope: 'project', commandSchema: 'RevisionsResourceCommand' },
  changes: { key: 'changes', label: '变更审查', description: '提交变更意图，核对语义差异、影响范围与预算。', schema: 'ChangeRequest', scope: 'project', commandSchema: 'ChangesResourceCommand' },
  approvals: { key: 'approvals', label: '批准记录', description: '批准绑定目标哈希、动作与有效期；旧批准不能覆盖新版本。', schema: 'ApprovalRequest', scope: 'project' },
  artifacts: { key: 'artifacts', label: '工程工件', description: '按不可变内容哈希保存的源码、图纸、日志与交付文件。', scope: 'project' },
  evidence: { key: 'evidence', label: '独立验证证据', description: '保留原始证据与当前有效性，未测、失效和无法判定各自可见。', scope: 'project' },
  collections: { key: 'collections', label: '知识集合', description: '管理检索集合、来源与项目访问范围。', schema: 'Collection', scope: 'project', commandSchema: 'CollectionsResourceCommand', update: true, remove: true },
  documents: { key: 'documents', label: '来源文档', description: '文档关联内容工件与来源，解析失败不会变成已入库。', schema: 'DocumentInput', scope: 'project', commandSchema: 'DocumentsResourceCommand', remove: true },
  quotes: { key: 'quotes', label: '器件报价', description: '保留供应商、币种、阶梯价、有效期与来源证据。', schema: 'QuoteOffer', scope: 'project', commandSchema: 'QuotesResourceCommand' },
  bindings: { key: 'bindings', label: 'Agent 绑定', description: '为工程角色绑定精确模型、Skills、扩展与知识集合版本。', schema: 'Binding', scope: 'project', commandSchema: 'BindingsResourceCommand', update: true, remove: true },
  members: { key: 'members', label: '项目成员', description: '项目访问角色与全局管理角色分别控制。', schema: 'MemberInput', scope: 'project', update: true, remove: true },
  'model-profiles': { key: 'model-profiles', label: '模型配置', description: '登记模型能力、费用与密钥引用。新配置默认预算为 0。', schema: 'ModelProfile', scope: 'admin', commandSchema: 'ModelProfilesResourceCommand', update: true, remove: true },
  'mcp-connections': { key: 'mcp-connections', label: 'MCP 连接', description: '管理受控连接、工具权限与 Schema 变更。', schema: 'ConnectionConfig', scope: 'admin', commandSchema: 'McpConnectionsResourceCommand', update: true, remove: true },
  'knowledge-connections': { key: 'knowledge-connections', label: '知识连接', description: '配置外部知识提供方，来源访问需符合登记权限。', schema: 'ConnectionConfig', scope: 'admin', commandSchema: 'KnowledgeConnectionsResourceCommand', update: true, remove: true },
  'quote-connections': { key: 'quote-connections', label: '报价连接', description: '登记授权报价数据源与连接凭据引用。', schema: 'ConnectionConfig', scope: 'admin', commandSchema: 'QuoteConnectionsResourceCommand', update: true, remove: true },
  skills: { key: 'skills', label: 'Skills', description: '固定内容哈希与版本，审查来源、依赖和权限后使用。', schema: 'SkillSpec', scope: 'admin', commandSchema: 'SkillsResourceCommand', update: true, remove: true },
  extensions: { key: 'extensions', label: '插件扩展', description: '插件包以独立受控适配器运行，登记不等于信任或启用。', schema: 'PluginManifest', scope: 'admin', commandSchema: 'ExtensionsResourceCommand', update: true, remove: true },
  registries: { key: 'registries', label: '扩展源', description: '管理可追溯的软件包来源与连接。', schema: 'ConnectionConfig', scope: 'admin', commandSchema: 'RegistriesResourceCommand', update: true, remove: true },
  budgets: { key: 'budgets', label: '费用与预算', description: '显式设置项目或全局限额；未知消费需人工核对。', schema: 'BudgetInput', scope: 'admin', commandSchema: 'BudgetsResourceCommand', update: true, remove: true },
  users: { key: 'users', label: '用户管理', description: '仅管理员可管理全局账号。没有默认通用密码。', schema: 'UserInput', scope: 'admin', commandSchema: 'UsersResourceCommand', update: true, remove: true },
};
