import { FastifyRequest, FastifyReply } from 'fastify'
export function authorize(...roles: string[]) {
  return async (req: FastifyRequest, reply: FastifyReply) => {
    const user = (req as any).user
    if (!user || !roles.includes(user.role)) {
      reply.code(403).send({ error: 'Forbidden' })
    }
  }
}
