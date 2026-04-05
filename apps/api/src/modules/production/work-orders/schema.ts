export const createWorkOrderSchema = {
  body: {
    type: 'object',
    required: ['productId', 'plannedQty', 'plannedStart'],
    properties: {
      productId: { type: 'string' },
      bomId: { type: 'string' },
      plannedQty: { type: 'number' },
      plannedStart: { type: 'string', format: 'date-time' },
      plannedEnd: { type: 'string', format: 'date-time' },
      notes: { type: 'string' },
    }
  }
}
