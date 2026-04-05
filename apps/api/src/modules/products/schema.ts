export const createProductSchema = {
  body: {
    type: 'object',
    required: ['name', 'unit'],
    properties: {
      name: { type: 'string' },
      code: { type: 'string' },
      unit: { type: 'string' },
      hsnCode: { type: 'string' },
      category: { type: 'string' },
      gstRate: { type: 'number' },
      standardCost: { type: 'number' },
      sellingPrice: { type: 'number' },
      width: { type: 'number' },
      length: { type: 'number' },
      gsm: { type: 'number' },
      description: { type: 'string' },
    }
  }
}
