# Convenience wrapper. `make` is not installed on every dev machine here, so the
# pnpm scripts in package.json are the canonical entrypoints; these targets just
# forward to them.
.PHONY: dev up down verify-stack spikes lint typecheck test verify

dev:           ; pnpm dev
up:            ; pnpm db:up
down:          ; pnpm db:down
verify-stack:  ; pnpm db:verify
spikes:        ; pnpm spikes
lint:          ; pnpm lint
typecheck:     ; pnpm typecheck
test:          ; pnpm test
verify:        ; pnpm verify
