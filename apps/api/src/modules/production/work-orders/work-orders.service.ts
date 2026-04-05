import { db } from '../../../../config/database'
import { NotFoundError, BadRequestError } from '../../../../utils/errors'
import { getPagination, paginate } from '../../../../utils/pagination'
import { nextNumber } from '../../../../utils/number-series'

export async function list(filters: any) {
  const { skip, take, page, limit } = getPagination(filters)
  const where: any = {}
  if (filters.status) where.status = filters.status
  if (filters.productId) where.productId = filters.productId
  if (filters.from) where.plannedStart = { gte: new Date(filters.from) }
  const [data, total] = await Promise.all([
    db.workOrder.findMany({ where, skip, take, include: { product: true, bom: true }, orderBy: { createdAt: 'desc' } }),
    db.workOrder.count({ where })
  ])
  return paginate(data, total, page, limit)
}

export async function create(data: any) {
  const woNumber = await nextNumber('WO', 'workOrder', 'woNumber')
  return db.workOrder.create({ data: { ...data, woNumber, status: 'PLANNED' }, include: { product: true } })
}

export async function findById(id: string) {
  const wo = await db.workOrder.findUnique({ where: { id }, include: { product: true, bom: { include: { items: { include: { rawMaterial: true } } } }, jobCards: true } })
  if (!wo) throw new NotFoundError('Work order not found')
  return wo
}

export async function update(id: string, data: any) {
  await findById(id)
  return db.workOrder.update({ where: { id }, data })
}

export async function remove(id: string) {
  const wo = await findById(id)
  if (wo.status !== 'PLANNED') throw new BadRequestError('Only PLANNED work orders can be deleted')
  return db.workOrder.delete({ where: { id } })
}

export async function start(id: string) {
  const wo = await findById(id)
  if (wo.status !== 'PLANNED') throw new BadRequestError('Work order must be in PLANNED status')
  return db.workOrder.update({ where: { id }, data: { status: 'IN_PROGRESS', actualStart: new Date() } })
}

export async function complete(id: string, actualQty: number) {
  const wo = await findById(id)
  if (wo.status !== 'IN_PROGRESS') throw new BadRequestError('Work order must be IN_PROGRESS')
  return db.$transaction(async (tx) => {
    const updated = await tx.workOrder.update({
      where: { id },
      data: { status: 'COMPLETED', actualEnd: new Date(), actualQty }
    })
    await tx.inventoryTransaction.create({
      data: {
        productId: wo.productId,
        qty: actualQty,
        type: 'WO_COMPLETION',
        referenceType: 'WORK_ORDER',
        referenceId: id,
        transactionDate: new Date(),
        reason: `WO ${wo.woNumber} completed`
      }
    })
    return updated
  })
}
