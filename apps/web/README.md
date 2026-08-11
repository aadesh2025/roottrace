# `apps/web` — placeholder

**Do not scaffold Next.js here yet.** This directory holds the minimum a `pnpm`
workspace needs to resolve — a private `package.json` and this file.

## Why it is empty

`docs/15-V1-BUILD-PLAN.md` §3 (T1.1, note A2) is explicit: the dashboard begins
at **Phase 16**, last in the build order. A Next.js scaffold created in Phase 1
would sit untouched for the entire build and its dependency tree would be stale
before its first use.

The stated consequence, rather than a hidden one: `eslint` and `tsc --noEmit`
have nothing to check until Phase 16, so the TypeScript half of `make check` is
a no-op until then. The tooling is wired now so the first component added is
linted by the commit that adds it.

## When work starts here

| Read | For |
|---|---|
| `docs/09-FRONTEND-DASHBOARD.md` | Every page, panel, and state |
| `docs/10-DESIGN-SYSTEM.md` | Tokens and component specs — never hardcode a colour that is not a token |
| `docs/05-API-SPEC.md` | REST surface and WebSocket frames (frozen since Phase 4) |

Stack is fixed: Next.js 14 App Router · TypeScript · Tailwind · shadcn/ui ·
Monaco · Recharts. Light theme only.

`snake_case` crosses the wire; conversion to `camelCase` happens in exactly one
place — `apps/web/lib/api-client.ts`.
