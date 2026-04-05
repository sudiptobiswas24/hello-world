import { db } from '../../../../config/database'
import { NotFoundError } from '../../../../utils/errors'
import { getPagination, paginate } from '../../../../utils/pagination'

export async function list(filters: any) {
  const { skip, take, page, limit } = getPagination(filters)
  const where: any = {}
  if (filters.machineId) where.machineId = filters.machineId
  if (filters.shift) where.shift = filters.shift
  if (filters.from) where.productionDate = { gte: new Date(filters.from) }
  if (filters.to) where.productionDate = { ...(where.productionDate ?? {}), lte: new Date(filters.to) }
  const [data, total] = await Promise.all([
    db.extrusionLog.findMany({ where, skip, take, orderBy: { productionDate: 'desc' } }),
    db.extrusionLog.count({ where })
  ])
  return paginate(data, total, page, limit)
}

export async function create(data: any) {
  return db.$transaction(async (tx) => {
    const log = await tx.extrusionLog.create({ data })
    if (data.materialConsumptions && Array.isArray(data.materialConsumptions)) {
      for (const mc of data.materialConsumptions) {
        await tx.inventoryTransaction.create({
          data: {
            rawMaterialId: mc.rawMaterialId,
            qty: -Math.abs(mc.qty),
            type: 'PRODUCTION_CONSUMPTION',
            referenceType: 'EXTRUSION_LOG',
            referenceId: log.id,
            transactionDate: new Date(),
            reason: `Extrusion log ${log.id}`
          }
        })
      }
    }
    return log
  })
}

export async function findById(id: string) {
  const log = await db.extrusionLog.findUnique({ where: { id } })
  if (!log) throw new NotFoundError('Extrusion log not found')
  return log
}

export async function update(id: string, data: any) {
  await findById(id)
  return db.extrusionLog.update({ where: { id }, data })
}

export async function remove(id: string) {
  await findById(id)
  return db.extrusionLog.delete({ where: { id } })
}

export async function getEfficiency(machineId: string, dateFrom: string, dateTo: string) {
  const logs = await db.extrusionLog.findMany({
    where: { machineId, productionDate: { gte: new Date(dateFrom), lte: new Date(dateTo) } }
  })
  const totalTarget = logs.reduce((s: number, l: any) => s + (l.targetOutput ?? 0), 0)
  const totalActual = logs.reduce((s: number, l: any) => s + (l.actualOutput ?? 0), 0)
  const efficiency = totalTarget > 0 ? (totalActual / totalTarget) * 100 : 0
  return { machineId, dateFrom, dateTo, totalTarget, totalActual, efficiency: Math.round(efficiency * 100) / 100, logs: logs.length }
}
