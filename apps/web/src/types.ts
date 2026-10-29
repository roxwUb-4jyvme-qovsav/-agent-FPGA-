export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Data = Record<string, unknown>;

export interface Session {
  user_id: string;
  global_role: 'admin' | 'user';
  project_roles: { project_id: string; role: 'admin' | 'developer' | 'reviewer' | 'viewer' }[];
  csrf_token: string;
}

export interface Resource<T = Data> {
  id: string;
  project_id: string | null;
  version: number;
  etag: string;
  state: string;
  payload: T;
  created_at: string;
  updated_at: string;
}

export interface ResourceList<T = Resource> { items: T[]; next_cursor: string | null }
export interface ProjectInput { name: string; description: string; team_id: string }
export type Project = Resource<ProjectInput>;
export interface SetupStatus { initialized: boolean; team_id: string }
export interface Health { status: string; api_version: string; database_ready: boolean; scheduler_ready: boolean; eda_ready: boolean; cad_ready: boolean }
export interface TaskRecord {
  task_id: string; run_id: string; version: number; state: string;
  envelope: { goal: string; agent_role: string; revision_id: string; deadline: string; attempt: number; max_cost_cny: string };
  questions: { id: string; question: string; blocking: boolean; answer: string | null }[];
  waiting_reason: string | null;
  attempts: Data[];
  result: unknown;
}
export interface CaptureChunk { capture_id: number; offset_samples: number; count_samples: number; sample_bytes: number; samples_hex: string }
export interface CommandResult { command_id: string; action: string; sequence: number; state: string; reply: { action: string; data: Data } | null; error: Data | null; capture_ref: Data | null }
export interface Schema {
  type?: string; $ref?: string; const?: unknown; enum?: unknown[];
  properties?: Record<string, Schema>; required?: string[]; items?: Schema;
  anyOf?: Schema[]; oneOf?: Schema[]; minimum?: number; maximum?: number;
  minLength?: number; minItems?: number; format?: string; pattern?: string;
}
