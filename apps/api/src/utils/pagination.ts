export function getPagination(query: { page?: string; limit?: string }) {
  const page = Math.max(1, parseInt(query.page ?? '1'))
  const limit = Math.min(200, Math.max(1, parseInt(query.limit ?? '20')))
  const skip = (page - 1) * limit
  return { page, limit, skip, take: limit }
}
export function paginate<T>(data: T[], total: number, page: number, limit: number) {
  return { data, total, page, limit, pages: Math.ceil(total / limit) }
}
