import { FastifyInstance } from 'fastify'
import { authenticate } from '../../../../middleware/authenticate'
import * as svc from './job-cards.service'

export async function jobCardRoutes(app: FastifyInstance) {
  app.addHook('onRequest', authenticate)
  app.get('/', async (req, reply) => reply.send(await svc.list(req.query as any)))
  app.post('/', async (req, reply) => reply.code(201).send(await svc.create(req.body as any)))
  app.get('/:id', async (req, reply) => reply.send(await svc.findById((req.params as any).id)))
  app.patch('/:id', async (req, reply) => reply.send(await svc.update((req.params as any).id, req.body as any)))
  app.delete('/:id', async (req, reply) => { await svc.remove((req.params as any).id); reply.code(204).send() })
  app.post('/:id/complete', async (req, reply) => reply.send(await svc.complete((req.params as any).id)))
}
