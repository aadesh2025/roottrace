# `packages/shared-types` — placeholder

Running two languages is the accepted trade-off recorded in
`docs/02-SYSTEM-ARCHITECTURE.md` §204. The mitigation is this package:
**one JSON Schema per contract, both TypeScript and Python types generated from
it**, so the API and the dashboard cannot drift apart silently.

Empty in Phase 1 on purpose. Generating types before there is a producer or a
consumer would mean hand-writing the schemas twice — once here and once as the
Pydantic models that `docs/05-API-SPEC.md` and `docs/03-PIPELINE-SPEC.md`
actually define. The schemas are derived from those models, not the reverse.

Populate when the first contract has both sides: an `api` router that emits it
and a `web` component that reads it.
