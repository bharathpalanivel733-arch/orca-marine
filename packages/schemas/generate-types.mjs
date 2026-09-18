/**
 * Generate TypeScript types from the exported JSON Schema (PLAN.md Phase 0.1).
 *
 * Compiles the single bundled schema (generated/contracts.json) so shared nested
 * models are emitted exactly once. Run via `pnpm schemas:generate`, which refreshes
 * the JSON from the Pydantic models first.
 */
import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const bundlePath = join(here, "generated", "contracts.json");

const BANNER = `/**
 * GENERATED FILE — do not edit by hand.
 *
 * Source of truth: packages/schemas/orca_schemas (Pydantic).
 * Regenerate with: pnpm schemas:generate
 */`;

const schema = JSON.parse(await readFile(bundlePath, "utf8"));
const ts = await compile(schema, "OrcaContracts", {
  bannerComment: BANNER,
  additionalProperties: false,
  declareExternallyReferenced: true,
  style: { singleQuote: false },
});

const outPath = join(here, "generated", "types.ts");
await writeFile(outPath, ts, "utf8");
console.log(`wrote generated/types.ts`);
