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
 */
export type HealthStatus = "ok" | "degraded" | "down";
/**
 * How much the value itself can be relied on, independent of its age.
 */
export type DataQuality = "observed" | "interpolated" | "gap_filled" | "suspect" | "missing";
/**
 * Canonical variable names.
 *
 * Adapters translate their upstream naming (``SST``, ``analysed_sst``, ``thetao``,
 * ``VHM0``) into these, which is what lets one variable be served by several sources
 * that use different vocabularies.
 */
export type MarineVariable =
  | "sea_surface_temperature"
  | "chlorophyll"
  | "significant_wave_height"
  | "peak_wave_period"
  | "mean_wave_direction"
  | "swell_height"
  | "wind_speed"
  | "wind_direction"
  | "current_speed"
  | "current_direction"
  | "sea_surface_salinity"
  | "mixed_layer_depth"
  | "depth_of_20c_isotherm"
  | "sea_level";
/**
 * Whether the number describes the past or the future.
 *
 * The reliability layer (PLAN.md Phase 10.1) backtests *forecasts* against buoy
 * *observations*; without this distinction on the record, the two cannot be paired.
 */
export type MeasurementKind = "observation" | "analysis" | "forecast";
/**
 * How much the value itself can be relied on, independent of its age.
 */
export type DataQuality1 = "observed" | "interpolated" | "gap_filled" | "suspect" | "missing";
/**
 * Whether the number describes the past or the future.
 *
 * The reliability layer (PLAN.md Phase 10.1) backtests *forecasts* against buoy
 * *observations*; without this distinction on the record, the two cannot be paired.
 */
export type MeasurementKind1 = "observation" | "analysis" | "forecast";

export interface OrcaContracts {
  BoundingBox?: BoundingBox;
  Cadence?: Cadence;
  ComponentHealth?: ComponentHealth;
  DataQuality?: DataQuality;
  HealthStatus?: HealthStatus;
  MarineVariable?: MarineVariable;
  MeasurementKind?: MeasurementKind;
  ObservationRecord?: ObservationRecord;
  ProblemDetail?: ProblemDetail;
  RunContext?: RunContext;
  ServiceHealth?: ServiceHealth;
  SourceDescriptor?: SourceDescriptor;
  TimeWindow?: TimeWindow;
}
/**
 * Geographic query extent in WGS84 degrees.
 */
export interface BoundingBox {
  min_lat: number;
  max_lat: number;
  min_lon: number;
  max_lon: number;
}
/**
 * How often a source refreshes, and how much lateness is tolerated.
 *
 * Staleness is a deterministic function of cadence and the clock — never a judgement
 * call, and never something an LLM decides.
 */
export interface Cadence {
  /**
   * Nominal interval between issues.
   */
  period: string;
  /**
   * Lateness tolerated beyond one period before data counts as stale.
   */
  grace?: string;
}
/**
 * Condition of one dependency (database, cache, object store, upstream feed).
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
 * The common record every adapter emits (PLAN.md Phase 1.1).
 *
 * Carries its own provenance — which source, issued when, valid when, under what
 * licence — so a number can always be traced back without consulting the adapter that
 * produced it.
 */
export interface ObservationRecord {
  variable: MarineVariable;
  /**
   * None only when quality is 'missing'.
   */
  value: number | null;
  unit: string;
  lat: number;
  lon: number;
  /**
   * Positive down; None = surface.
   */
  depth_m?: number | null;
  /**
   * Instant the value describes.
   */
  valid_time: string;
  /**
   * Instant the source published it.
   */
  issued_time: string;
  /**
   * The SourceDescriptor.source_id that produced this record.
   */
  source: string;
  /**
   * Upstream dataset identifier, e.g. an ERDDAP griddap id.
   */
  dataset_id?: string | null;
  quality?: DataQuality1;
  kind?: MeasurementKind1;
  license: string;
}
/**
 * Error payload (RFC 9457 shape) carrying the run id for support and replay.
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
 */
export interface ServiceHealth {
  service: string;
  version: string;
  status: HealthStatus;
  checked_at?: string;
  components?: ComponentHealth[];
}
/**
 * Registry metadata describing what one source can serve.
 *
 * The planner consults these to choose a source instead of calling a hard-wired
 * endpoint (PLAN.md G3 / Phase 3.1); the degradation chain uses ``authority_rank`` to
 * decide what to fall back to.
 */
export interface SourceDescriptor {
  /**
   * Stable identifier, e.g. 'incois_erddap'.
   */
  source_id: string;
  name: string;
  /**
   * 1 = authoritative Indian agency; higher = further down the fallback chain.
   */
  authority_rank: number;
  variables: MarineVariable[];
  coverage: BoundingBox;
  cadence: Cadence;
  license: string;
  requires_auth?: boolean;
  /**
   * Text the UI must display when this source is used.
   */
  attribution?: string | null;
}
/**
 * Half-open time extent ``[start, end)`` for a query.
 */
export interface TimeWindow {
  start: string;
  end: string;
}
