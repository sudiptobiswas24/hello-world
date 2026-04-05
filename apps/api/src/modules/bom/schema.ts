export const createBomSchema = {
  body: {
    type: 'object',
    required: ['productId', 'version', 'items'],
    properties: {
      productId: { type: 'string' },
      version: { type: 'string' },
      description: { type: 'string' },
      isActive: { type: 'boolean' },
      items: {
        type: 'array',
        items: {
          type: 'object',
          required: ['rawMaterialId', 'qty', 'unit'],
          properties: {
            rawMaterialId: { type: 'string' },
            qty: { type: 'number' },
            unit: { type: 'string' },
            scrapPct: { type: 'number' },
            stage: { type: 'string' },
          }
        }
      }
    }
  }
}
