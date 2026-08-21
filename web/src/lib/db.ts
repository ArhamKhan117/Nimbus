/**
 * One Prisma client, reused across hot reloads and warm lambdas.
 *
 * Without the global cache, every hot reload in development opens a new pool and Postgres runs out
 * of connections long before you run out of patience. On Vercel it matters for a different reason:
 * a warm function reuses the module scope, so a client created per request would leak connections
 * under any real traffic.
 */
import { PrismaClient } from "@prisma/client";

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const db =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === "development" ? ["warn", "error"] : ["error"],
  });

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = db;

/** Append-only audit trail. Never throws: an audit write must not fail an activation. */
export async function logEvent(kind: string, detail = ""): Promise<void> {
  try {
    await db.event.create({ data: { kind, detail: detail.slice(0, 500) } });
  } catch {
    // Swallowed on purpose. See above.
  }
}
