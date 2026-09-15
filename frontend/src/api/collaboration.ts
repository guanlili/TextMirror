/** 有界协作审校；报告属于原始结果，不是可编辑审阅快照。 */
import request from '@/utils/request'
import type { ProofreadIssue, TextProofreadResponse } from '@/api/proofread'
import type { SubmitResponse } from '@/api/tasks'
export { createFactCheckId as createCollaborationId } from '@/api/factCheck'

export const MAX_COLLABORATION_CHARS = 8000
export type CollaborationRoleId = 'rules' | 'language' | 'consistency' | 'reviewer'
export type CollaborationRoleStatus = 'pending' | 'running' | 'success' | 'failed' | 'skipped' | 'cancelled'
export interface CollaborationRole {
  readonly id: CollaborationRoleId
  readonly name: string
  readonly status: CollaborationRoleStatus
  readonly message: string
  readonly issue_count: number
  readonly elapsed_ms: number
  readonly usage: Readonly<Record<string, number>>
}
export interface CollaborationFinding extends Readonly<ProofreadIssue> {
  readonly source?: string | null
  readonly found_by?: readonly string[] | null
  readonly review_status: 'not_reviewed' | 'confirmed' | 'disputed'
  readonly review_note: string
}
export interface CollaborationReport {
  readonly status: 'running' | 'complete' | 'partial'
  readonly roles: readonly CollaborationRole[]
  readonly findings: readonly CollaborationFinding[]
  readonly reviewed_count: number
  readonly review_limit: number
  readonly config_id: number | null
  readonly model_name: string
}
export interface CollaborationResult extends TextProofreadResponse {
  readonly collaboration: CollaborationReport
}
export interface CollaborationInput {
  text: string
  domain: string
  config_id?: number
}
export interface CollaborationCreate extends CollaborationInput { request_id: string }
export interface CollaborationSnapshot {
  task_id: string
  text: string
  domain: string
  config_id: number | null
}
const config = (signal?: AbortSignal) => ({ signal, headers: { 'X-Silent-Error': 'true' } })
export function createCollaborationApi(data: CollaborationCreate, signal?: AbortSignal): Promise<SubmitResponse> {
  return request.post('/proofread/collaborate', {
    text: data.text, domain: data.domain,
    ...(data.config_id === undefined ? {} : { config_id: data.config_id }), request_id: data.request_id,
  }, config(signal))
}
export function getCollaborationSnapshotApi(taskId: string, signal?: AbortSignal): Promise<CollaborationSnapshot> {
  return request.get(`/proofread/collaborate/${encodeURIComponent(taskId)}`, config(signal))
}
