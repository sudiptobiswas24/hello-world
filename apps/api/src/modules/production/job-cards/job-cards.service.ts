import { db } from '../../../../config/database'
import { NotFoundError, BadRequestError } from '../../../../utils/errors'
import { getPagination, paginate } from '../../../../utils/pagination'

export async function list(filters: any) {
  const { skip, take, page, limit } = getPagination(filters)
  const where: any = {}
  if (filters.workOrderId) where.workOrderId = filters.workOrderId
  if (filters.status) where.status = filters.status
  if (filters.operatorId) where.operatorId = filters.operatorId
  const [data, total] = await Promise.all([
    db.jobCard.findMany({ where, skip, take, include: { workOrder: true }, orderBy: { createdAt: 'desc' } }),
    db.jobCard.count({ where })
  ])
  return paginate(data, total, page, limit)
}

export async function create(data: any) {
  return db.jobCard.create({ data: { ...data, status: 'OPEN' }, include: { workOrder: true } })
}

export async function findById(id: string) {
  const jc = await db.jobCard.findUnique({ where: { id }, include: { workOrder: { include: { product: true } } } })
  if (!jc) throw new NotFoundError('Job card not found')
  return jc
}

export async function update(id: string, data: any) {
  await findById(id)
  return db.jobCard.update({ where: { id }, data })
}

export async function remove(id: string) {
  await findById(id)
  return db.jobCard.delete({ where: { id } })
}

export async function complete(id: string) {
  const jc = await findById(id)
  if ((jc as any).status === 'CLOSED') throw new BadRequestError('Job card already closed')
  return db.jobCard.update({ where: { id }, data: { status: 'CLOSED', completedAt: new Date() } })
}
