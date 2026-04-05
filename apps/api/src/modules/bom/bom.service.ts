import { db } from '../../../config/database'
import { NotFoundError } from '../../../utils/errors'
import { getPagination, paginate } from '../../../utils/pagination'

export async function findAll(filters: any) {
  const { skip, take, page, limit } = getPagination(filters)
  const where: any = {}
  if (filters.productId) where.productId = filters.productId
  if (filters.isActive !== undefined) where.isActive = filters.isActive === 'true'
  const [data, total] = await Promise.all([
    db.bomHeader.findMany({ where, skip, take, include: { product: true, items: { include: { rawMaterial: true } } } }),
    db.bomHeader.count({ where })
  ])
  return paginate(data, total, page, limit)
}

export async function create(data: any) {
  const { items, ...header } = data
  return db.$transaction(async (tx) => {
    const bom = await tx.bomHeader.create({ data: { ...header, items: { create: items } }, include: { items: true } })
    return bom
  })
}

export async function findById(id: string) {
  const bom = await db.bomHeader.findUnique({ where: { id }, include: { product: true, items: { include: { rawMaterial: true } } } })
  if (!bom) throw new NotFoundError('BOM not found')
  return bom
}

export async function update(id: string, data: any) {
  const { items, ...header } = data
  await findById(id)
  return db.$transaction(async (tx) => {
    if (items) {
      await tx.bomItem.deleteMany({ where: { bomHeaderId: id } })
      await tx.bomItem.createMany({ data: items.map((i: any) => ({ ...i, bomHeaderId: id })) })
    }
    return tx.bomHeader.update({ where: { id }, data: header, include: { items: { include: { rawMaterial: true } } } })
  })
}

export async function remove(id: string) {
  await findById(id)
  return db.$transaction(async (tx) => {
    await tx.bomItem.deleteMany({ where: { bomHeaderId: id } })
    return tx.bomHeader.delete({ where: { id } })
  })
}

export async function getActiveBomForProduct(productId: string) {
  const bom = await db.bomHeader.findFirst({ where: { productId, isActive: true }, include: { items: { include: { rawMaterial: true } } } })
  if (!bom) throw new NotFoundError('No active BOM for product')
  return bom
}

export async function calculateCost(bomId: string) {
  const bom = await findById(bomId)
  let totalCost = 0
  const breakdown = bom.items.map((item: any) => {
    const unitCost = item.rawMaterial.standardCost ?? 0
    const effectiveQty = item.qty * (1 + (item.scrapPct ?? 0) / 100)
    const cost = effectiveQty * unitCost
    totalCost += cost
    return { rawMaterialId: item.rawMaterialId, name: item.rawMaterial.name, qty: item.qty, effectiveQty, unitCost, cost }
  })
  return { bomId, totalCost, breakdown }
}
