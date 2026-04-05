import { db } from '../../../config/database'
import { NotFoundError } from '../../../utils/errors'
import { getPagination, paginate } from '../../../utils/pagination'

export async function findAll(filters: any) {
  const { skip, take, page, limit } = getPagination(filters)
  const where: any = {}
  if (filters.search) where.OR = [{ name: { contains: filters.search, mode: 'insensitive' } }, { code: { contains: filters.search, mode: 'insensitive' } }]
  if (filters.category) where.category = filters.category
  if (filters.warehouseId) where.warehouseId = filters.warehouseId
  const [data, total] = await Promise.all([
    db.rawMaterial.findMany({ where, skip, take, orderBy: { name: 'asc' }, include: { _count: { select: { lots: true } } } }),
    db.rawMaterial.count({ where })
  ])
  return paginate(data, total, page, limit)
}

export async function create(data: any) {
  return db.rawMaterial.create({ data })
}

export async function findById(id: string) {
  const rm = await db.rawMaterial.findUnique({ where: { id }, include: { lots: { where: { remainingQty: { gt: 0 } } } } })
  if (!rm) throw new NotFoundError('Raw material not found')
  return rm
}

export async function update(id: string, data: any) {
  await findById(id)
  return db.rawMaterial.update({ where: { id }, data })
}

export async function remove(id: string) {
  await findById(id)
  return db.rawMaterial.delete({ where: { id } })
}

export async function getStockLevel(id: string) {
  await findById(id)
  const agg = await db.inventoryTransaction.aggregate({
    where: { rawMaterialId: id },
    _sum: { qty: true }
  })
  return { rawMaterialId: id, currentStock: agg._sum.qty ?? 0 }
}

export async function getLots(id: string) {
  await findById(id)
  return db.rawMaterialLot.findMany({ where: { rawMaterialId: id }, orderBy: { createdAt: 'desc' } })
}

export async function adjustStock(id: string, qty: number, reason: string, warehouseId: string) {
  await findById(id)
  return db.inventoryTransaction.create({
    data: {
      rawMaterialId: id,
      qty,
      type: qty > 0 ? 'ADJUSTMENT_IN' : 'ADJUSTMENT_OUT',
      reason,
      warehouseId,
      transactionDate: new Date(),
      referenceType: 'MANUAL_ADJUSTMENT'
    }
  })
}
