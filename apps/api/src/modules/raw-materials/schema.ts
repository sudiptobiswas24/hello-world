export const createRawMaterialSchema = {
  body: {
    type: 'object',
    required: ['name', 'unit'],
    properties: {
      name: { type: 'string' },
      code: { type: 'string' },
      unit: { type: 'string' },
      hsnCode: { type: 'string' },
      category: { type: 'string' },
      reorderLevel: { type: 'number' },
      reorderQty: { type: 'number' },
      standardCost: { type: 'number' },
      warehouseId: { type: 'string' },
    }
  }
}
