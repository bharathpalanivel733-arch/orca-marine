"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import type {
  ComponentHealth,
  HealthStatus,
  ServiceHealth,
} from "@orca/schemas/generated/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const STATUS_STYLES: Record<HealthStatus, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  degraded: "text-amber-600 dark:text-amber-400",
  down: "text-red-600 dark:text-red-400",
};

export default function StatusPage() {
  const [health, setHealth] = useState<ServiceHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const probe = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/readyz`, { cache: "no-store" });
      if (!response.ok) throw new Error(`API responded ${response.status}`);
      setHealth((await response.json()) as ServiceHealth);
    } catch (cause) {
      setHealth(null);
      setError(cause instanceof Error ? cause.message : "API unreachable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-8 px-4 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">ORCA</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Agentic reasoning and trust layer over INCOIS, IMD and ISRO marine feeds.
        </p>
      </header>

      <section className="rounded-lg border border-[var(--color-border)] p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-medium">Dev stack</h2>
          <Button size="sm" variant="outline" onClick={probe} disabled={loading}>
            {loading ? "Checking…" : "Re-check"}
          </Button>
        </div>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400">
            {error} — start the API with <code>pnpm api:dev</code>.
          </p>
        )}

        {health && (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              {health.service} v{health.version} ·{" "}
              <span className={STATUS_STYLES[health.status]}>{health.status}</span>
            </p>
            <ul className="flex flex-col gap-2">
              {(health.components ?? []).map((component: ComponentHealth) => (
                <li
                  key={component.name}
                  className="flex items-baseline justify-between gap-4 text-sm"
                >
                  <span>{component.name}</span>
                  <span className={STATUS_STYLES[component.status]}>
                    {component.status}
                    {component.detail ? (
                      <span className="text-[var(--color-muted-foreground)]">
                        {" "}
                        — {component.detail}
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <p className="text-xs text-[var(--color-muted-foreground)]">
        Phase 0 scaffold. No marine data source is connected, and no advisory,
        safety score or geofence verdict is produced yet.
      </p>
    </main>
  );
}
