import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, upload } from './client'
import { useRuntime } from '../store/useRuntime'
import type {
  Approval,
  BadCase,
  ChatResponse,
  DashboardData,
  DocumentDetail,
  DocumentSummary,
  DocumentUploadResponse,
  DocumentVersion,
  EvaluationReport,
  MockCase,
  Refund,
  RuntimeInfo,
  TaskRecord,
  Ticket,
  TraceStep,
} from './types'

export const queryKeys = {
  runtime: ['runtime'] as const,
  dashboard: ['dashboard'] as const,
  mockCases: ['mock-cases'] as const,
  tickets: ['tickets'] as const,
  refunds: ['refunds'] as const,
  approvals: ['approvals'] as const,
  documents: ['documents'] as const,
  document: (documentId?: string) => ['documents', documentId ?? 'none'] as const,
  documentVersion: (documentId?: string, versionId?: string) =>
    ['documents', documentId ?? 'none', 'versions', versionId ?? 'none'] as const,
  evaluation: ['evaluation'] as const,
  badCases: ['bad-cases'] as const,
  traces: (traceId?: string) => ['traces', traceId ?? 'all'] as const,
  tasks: ['task'] as const,
  task: (taskId?: string) => ['task', taskId ?? 'none'] as const,
}

export function useRuntimeInfo() {
  return useQuery({
    queryKey: queryKeys.runtime,
    queryFn: () => api.get<RuntimeInfo>('/runtime'),
    staleTime: 60_000,
  })
}

/** Operational metrics poll so the console stays current without manual refreshes. */
export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<DashboardData>('/dashboard'),
    refetchInterval: 10_000,
  })
}

export function useMockCases() {
  return useQuery({
    queryKey: queryKeys.mockCases,
    queryFn: () => api.get<MockCase[]>('/mock-cases'),
    staleTime: 60_000,
  })
}

export function useTickets() {
  return useQuery({
    queryKey: queryKeys.tickets,
    queryFn: () => api.get<Ticket[]>('/tickets'),
    refetchInterval: 10_000,
  })
}

export function useRefunds() {
  return useQuery({
    queryKey: queryKeys.refunds,
    queryFn: () => api.get<Refund[]>('/refunds'),
    refetchInterval: 10_000,
  })
}

export function useApprovals() {
  return useQuery({
    queryKey: queryKeys.approvals,
    queryFn: () => api.get<Approval[]>('/approvals'),
    refetchInterval: 5_000,
  })
}

/** Document-level catalogue; the drawer lazily loads the version history. */
export function useDocuments() {
  return useQuery({
    queryKey: queryKeys.documents,
    queryFn: () => api.get<DocumentSummary[]>('/documents'),
    staleTime: 10_000,
  })
}

export function useDocument(documentId?: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.document(documentId),
    queryFn: () => api.get<DocumentDetail>(`/documents/${documentId}`),
    enabled: Boolean(documentId) && enabled,
    // Poll only while a version is queued or being parsed, then stop.
    refetchInterval: (query) => {
      const versions = query.state.data?.versions ?? []
      return versions.some(
        (version) => version.status === 'draft' || version.status === 'processing',
      )
        ? 2_000
        : false
    },
  })
}

export function useDocumentVersion(documentId?: string, versionId?: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.documentVersion(documentId, versionId),
    queryFn: () => api.get<DocumentVersion>(`/documents/${documentId}/versions/${versionId}`),
    enabled: Boolean(documentId && versionId) && enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'draft' || status === 'processing' ? 2_000 : false
    },
  })
}

/** Multipart document upload; the backend returns 202 and parses asynchronously. */
export function useUploadDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (formData: FormData) =>
      upload<DocumentUploadResponse>('/documents', formData),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents })
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(data.document_id) })
    },
  })
}

export function useUploadDocumentVersion(documentId?: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (formData: FormData) => {
      if (!documentId) throw new Error('document id is required for a new version')
      return upload<DocumentUploadResponse>(`/documents/${documentId}/versions`, formData)
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents })
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(data.document_id) })
    },
  })
}

export function useRetryDocumentVersion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { documentId: string; versionId: string }) =>
      api.post<DocumentUploadResponse>(
        `/documents/${input.documentId}/versions/${input.versionId}/retry`,
      ),
    onSuccess: (_data, input) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents })
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(input.documentId) })
    },
  })
}

export function useEvaluation() {
  return useQuery({
    queryKey: queryKeys.evaluation,
    queryFn: () => api.get<EvaluationReport>('/evaluation'),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

export function useBadCases() {
  return useQuery({
    queryKey: queryKeys.badCases,
    queryFn: () => api.get<BadCase[]>('/bad-cases'),
    refetchInterval: 10_000,
  })
}

export function useTraces(traceId?: string) {
  return useQuery({
    queryKey: queryKeys.traces(traceId),
    queryFn: () => api.get<TraceStep[]>(traceId ? `/traces?trace_id=${encodeURIComponent(traceId)}` : '/traces'),
    refetchInterval: traceId ? false : 10_000,
  })
}

export function useTask(taskId?: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: () => api.get<TaskRecord>(`/tasks/${taskId}`),
    enabled: Boolean(taskId) && enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && status !== 'completed' && status !== 'failed' ? 2_000 : false
    },
  })
}

export function useRunMockCase() {
  return useMutation({
    mutationFn: (caseKey: string) =>
      api.post<ChatResponse>('/mock-cases/run', { case_key: caseKey }),
  })
}

export function useRunChat() {
  return useMutation({
    mutationFn: (message: string) => api.post<ChatResponse>('/chat', { message }),
  })
}

export function useDecideApproval() {
  const queryClient = useQueryClient()
  const user = useRuntime((state) => state.user)
  return useMutation({
    mutationFn: (input: { id: string; status: 'approved' | 'rejected'; comment?: string }) =>
      api.post(`/approvals/${input.id}/decision`, {
        status: input.status,
        approver_user_id: user,
        comment: input.comment ?? 'console decision',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
      void queryClient.invalidateQueries({ queryKey: queryKeys.refunds })
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks })
    },
  })
}

export function useCreateBadCase() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      trace_id?: string | null
      query: string
      error_type: string
      root_cause?: string
      expected_result?: Record<string, unknown>
    }) => api.post<BadCase>('/bad-cases', input),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.badCases }),
  })
}

export function useMarkBadCaseFixed() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.patch<BadCase>(`/bad-cases/${id}`, { fixed: true }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.badCases }),
  })
}
