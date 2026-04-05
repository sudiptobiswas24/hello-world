import { db } from '../../../config/database'
import { NotFoundError } from '../../../utils/errors'
import { getPagination, paginate } from '../../../utils/pagination'

export async function findAll(filters: any) {
  const { skip, take, page, limit } = getPagination(filters)
  const where: any = {}
  if (filters.search) where.OR = [{ name: { contains: filters.search, mode: 'insensitive' } }, { code: { contains: filters.search, mode: 'insensitive' } }]
  if (filters.category) where.category = filters.category
  const [data, total] = await Promise.all([
    db.product.findMany({ where, skip, take, orderBy: { name: 'asc' } }),
    db.product.count({ where })
  ])
  return paginate(data, total, page, limit)
}

export async function create(data: any) {
  return db.product.create({ data })
}

export async function findById(id: string) {
  const p = await db.product.findUnique({ where: { id } })
  if (!p) throw new NotFoundError('Product not found')
  return p
}

export async function update(id: string, data: any) {
  await findById(id)
  return db.product.update({ where: { id }, data })
}

export async function remove(id: string) {
  await findById(id)
  return db.product.delete({ where: { id } })
}
