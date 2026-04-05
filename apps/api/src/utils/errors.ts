export class NotFoundError extends Error {
  statusCode = 404
  constructor(msg = 'Not found') { super(msg) }
}
export class BadRequestError extends Error {
  statusCode = 400
  constructor(msg = 'Bad request') { super(msg) }
}
export class ConflictError extends Error {
  statusCode = 409
  constructor(msg = 'Conflict') { super(msg) }
}
export function handleError(err: unknown): never {
  if (err instanceof NotFoundError || err instanceof BadRequestError || err instanceof ConflictError) throw err
  throw new Error(String(err))
}
