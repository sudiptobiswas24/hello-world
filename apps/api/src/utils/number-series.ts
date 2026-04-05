import { db } from '../config/database'
export async function nextNumber(prefix: string, model: string, field = 'number'): Promise<string> {
  const rec = await (db as any)[model].findFirst({ orderBy: { [field]: 'desc' }, select: { [field]: true } })
  if (!rec || !rec[field]) return `${prefix}-0001`
  const parts = rec[field].split('-')
  const n = parseInt(parts[parts.length - 1]) + 1
  return `${prefix}-${String(n).padStart(4, '0')}`
}
export function generateLotNumber(prefix = 'LOT'): string {
  const d = new Date()
  return `${prefix}-${d.getFullYear()}${String(d.getMonth()+1).padStart(2,'0')}${String(d.getDate()).padStart(2,'0')}-${String(Math.floor(Math.random()*9999)).padStart(4,'0')}`
}
