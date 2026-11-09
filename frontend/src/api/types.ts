export type Priority = 'P0' | 'P1' | 'P2' | 'P3'

export interface RuntimeInfo {
  mock_mode: boolean
  provider: string
  base_url: string
  models: { chat: string; embedding: string; rerank: string }
  embedding_dimensions: number
  mock_boundary: { chat: string; embedding: string; rerank: string }
  api_key_configured: boolean
  langgraph_available: boolean
  workflow_engine: string
  checkpoint_path: string
  database: string
  database_type: string
  agent_loop: {
    max_steps: number
    max_tool_calls: number
    max_tokens: number
    confidence_threshold: number
    allowed_tools: string[]
  }
}

export interface Ticket {
  id: string
  tenant_id?: string | null
  user_id?: string | null
  order_id?: string | null
  category?: string | null
  priority?: Priority | null
  status?: string | null
  description?: string | null
  assigned_group?: string | null
  sla_due_at?: string | null
  source?: string | null
  created_at: string
}

export interface Refund {
  id: string
  tenant_id?: string | null
  user_id?: string | null
  order_id?: string | null
  idempotency_key?: string | null
  amount?: number | null
  status?: string | null
  external_refund_id?: string | null
  reason?: string | null
  created_at: string
}

export interface Approval {
  id: string
  task_id?: string | null
  order_id?: string | null
  approver_role?: string | null
  sequence_no?: number | null
  status?: string | null
  approver_user_id?: string | null
  comment?: string | null
  created_at: string
  updated_at: string
}

export interface DocumentVersion {
  id: string
  document_id: string
  version_number: number
  label?: string | null
  status: string
  effective_from?: string | null
  source_filename?: string | null
  checksum?: string | null
  size_bytes?: number | null
  mime_type?: string | null
  error_code?: string | null
  error_message?: string | null
  published_at?: string | null
  created_at: string
  updated_at: string
  has_source_file: boolean
  content_text?: string | null
  document_title?: string | null
  category?: string | null
  product_type?: string | null
  visibility?: string | null
}

export interface DocumentSummary {
  id: string
  tenant_id?: string | null
  title?: string | null
  category?: string | null
  product_type?: string | null
  visibility: string
  allowed_roles?: string[] | null
  status: string
  owner_id?: string | null
  created_at: string
  updated_at: string
  version_count: number
  published_version?: DocumentVersion | null
}

export interface DocumentDetail extends DocumentSummary {
  versions: DocumentVersion[]
}

export interface TraceStep {
  id: number
  trace_id?: string | null
  node?: string | null
  status?: string | null
  latency_ms?: number | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  error?: string | null
  created_at: string
}

export interface SlaRiskItem {
  id: string
  priority?: Priority | null
  category?: string | null
  assigned_group?: string | null
  sla_due_at?: string | null
  remaining_minutes: number
  state: 'breached' | 'at_risk' | 'on_track'
}

export interface SlaRisk {
  window_minutes: number
  breached: number
  at_risk: number
  on_track: number
  tickets: SlaRiskItem[]
}

export interface DashboardData {
  tickets_total: number
  open_tickets: number
  refund_total: number
  waiting_approval: number
  ticket_by_priority: Array<{ priority: Priority; count: number }>
  ticket_by_category: Array<{ category: string; count: number }>
  sla_risk: SlaRisk
}

export interface MockCase {
  case_key: string
  title: string
  user_id: string
  message: string
}

export interface TaskRecord {
  id: string
  trace_id?: string | null
  tenant_id?: string | null
  user_id?: string | null
  intent?: string | null
  status?: string | null
  current_step?: string | null
  payload: Record<string, unknown>
  result: Record<string, unknown>
  error?: string | null
  created_at: string
  updated_at: string
}

export interface TaskEvent {
  id: number
  event_type: string
  created_at: string
  received_at: number
  node?: string
  error?: string
  output?: unknown
  [key: string]: unknown
}

export interface ChatResponse {
  mode?: string
  intent?: string
  answer?: string
  task_id?: string
  trace_id?: string
  status?: string
  [key: string]: unknown
}

export interface EvaluationCheck {
  field: string
  expected: unknown
  actual: unknown
  passed: boolean
}

export interface EvaluationCase {
  id: string
  name: string
  category: string
  passed: boolean
  error?: string
  trace_id?: string
  checks: EvaluationCheck[]
  expected: unknown
  actual: unknown
}

export interface EvaluationReport {
  summary?: { total: number; passed: number; failed: number; pass_rate: number }
  cases: EvaluationCase[]
}

export interface BadCase {
  id: number
  trace_id?: string | null
  query: string
  error_type: string
  root_cause: string
  expected_result: Record<string, unknown>
  fixed: boolean
  created_at: string
}
