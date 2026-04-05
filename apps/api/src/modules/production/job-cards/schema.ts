export const createJobCardSchema = {
  body: {
    type: 'object',
    required: ['workOrderId', 'machineId', 'shift'],
    properties: {
      workOrderId: { type: 'string' },
      machineId: { type: 'string' },
      operatorId: { type: 'string' },
      shift: { type: 'string', enum: ['MORNING', 'AFTERNOON', 'NIGHT'] },
      plannedQty: { type: 'number' },
      notes: { type: 'string' },
    }
  }
}
