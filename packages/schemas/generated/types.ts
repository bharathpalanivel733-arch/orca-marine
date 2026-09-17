/**
 * GENERATED FILE — do not edit by hand.
 *
 * Source of truth: packages/schemas/orca_schemas (Pydantic).
 * Regenerate with: pnpm schemas:generate
 */

/**
 * Service or dependency condition.
 *
 * ``degraded`` is a first-class state, not a soft failure: ORCA is designed to keep
 * operating with missing or stale inputs and to say so (ARCHITECTURE.md §7).
 *
 * This interface was referenced by `OrcaContracts`'s JSON-Schema
 * via the `definition` "HealthStatus".
 */
export type HealthStatus = "ok" | "degraded" | "down";

export interface OrcaContracts {
  [k: string]: unknown;
}
/**
 * Condition of one dependency (database, cache, object store, upstream feed).
 *
 * This interface was referenced by `OrcaContracts`'s JSON-Schema
 * via the `definition` "ComponentHealth".
 */
export interface ComponentHealth {
  name: string;
  status: HealthStatus;
  /**
   * Human-readable reason, required when not ok.
   */
  detail?: string | null;
  latency_ms?: number | null;
}
/**
 * Error payload (RFC 9457 shape) carrying the run id for support and replay.
 *
 * This interface was referenced by `OrcaContracts`'s JSON-Schema
 * via the `definition` "ProblemDetail".
 */
export interface ProblemDetail {
  type?: string;
  title: string;
  status: number;
  detail?: string | null;
  run_id?: string | null;
}
/**
 * Identity of a single end-to-end run, propagated to every service and log line.
 *
 * This interface was referenced by `OrcaContracts`'s JSON-Schema
 * via the `definition` "RunContext".
 */
export interface RunContext {
  /**
   * Unique id for this run.
   */
  run_id?: string;
  /**
   * Set when this run was spawned by another run.
   */
  parent_run_id?: string | null;
  /**
   * Service that created or received the run context.
   */
  service: string;
  started_at?: string;
}
/**
 * Aggregate health of one ORCA service.
 *
 * This interface was referenced by `OrcaContracts`'s JSON-Schema
 * via the `definition` "ServiceHealth".
 */
export interface ServiceHealth {
  service: string;
  version: string;
  status: HealthStatus;
  checked_at?: string;
  components?: ComponentHealth[];
}
