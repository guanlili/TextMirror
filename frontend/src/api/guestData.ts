import request from '@/utils/request'

export interface GuestDataSummary {
  records: number
  feedbacks: number
  documents: number
  total: number
  can_claim: boolean
}

export interface GuestDataCounts {
  records: number
  feedbacks: number
  documents: number
  tasks: number
}

export function getGuestDataSummaryApi(): Promise<GuestDataSummary> {
  return request.get('/guest-data/summary')
}

export function exportGuestDataApi(): Promise<Blob> {
  return request.get('/guest-data/export', { responseType: 'blob' })
}

export function claimGuestDataApi(): Promise<GuestDataCounts> {
  return request.post('/guest-data/claim')
}

export function deleteGuestDataApi(): Promise<GuestDataCounts> {
  return request.delete('/guest-data', { data: { confirm: 'DELETE' } })
}
