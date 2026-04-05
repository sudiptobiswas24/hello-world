import { FastifyInstance } from 'fastify'
import { authenticate } from '../../../middleware/authenticate'
import * as svc from './raw-materials.service'

export async function rawMaterialRoutes(app: FastifyInstance) {
  app.addHook('onRequest', authenticate)
  app.get('/', async (req, reply) => reply.send(await svc.findAll(req.query as any)))
  app.post('/', async (req, reply) => reply.code(201).send(await svc.create(req.body as any)))
  app.get('/:id', async (req, reply) => reply.send(await svc.findById((req.params as any).id)))
  app.patch('/:id', async (req, reply) => reply.send(await svc.update((req.params as any).id, req.body as any)))
  app.delete('/:id', async (req, reply) => { await svc.remove((req.params as any).id); reply.code(204).send() })
  app.get('/:id/stock', async (req, reply) => reply.send(await svc.getStockLevel((req.params as any).id)))
  app.get('/:id/lots', async (req, reply) => reply.send(await svc.getLots((req.params as any).id)))
  app.post('/:id/adjust', async (req, reply) => {
    const { qty, reason, warehouseId } = req.body as any
    reply.send(await svc.adjustStock((req.params as any).id, qty, reason, warehouseId))
  })
}
