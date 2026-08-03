## Choosing individuals parallelism

This is domain policy — how much work is worth giving one task — not
resource policy. Resource policy (per-environment memory budgets, vCPU
counts, who owns those numbers) lives in resource-policy.md; this section
assumes whatever that file's compute environment supplies.

- **Aim to keep every available core busy.** An `ind_jobs` well below the
  target environment's vCPU count leaves cores idle for no benefit.
- **Give each task at least ~10,000 variants for a cohort of ~1,000
  individuals.** A task carries a few seconds of fixed cost — container
  start, input scan, output compression — that a smaller chunk cannot
  amortise, so slicing thinner than this floor only adds overhead without
  adding useful work.
- **That floor scales inversely with cohort size.** Cost follows variants ×
  individuals, not variants alone, so a larger cohort needs fewer variants
  per task to reach the same fixed-cost floor: a cohort of ~2,000
  individuals only needs roughly half the variants per task — ~5,000 — to
  clear the same fixed-cost floor that ~10,000 variants clears for ~1,000
  individuals.
- **Keep each task under the configured per-task memory budget** — 512 MB
  for the `"medium"` preset (see resource-policy.md), the ceiling the cost
  model behind `recommend_parallelism` is calibrated against.
- **Call `recommend_parallelism` with the variant count and the target
  environment; do not compute the value by hand.** It is the one mechanism
  behind both `plan_workflow`'s and `generate_workflow`'s parallelism
  recommendation, so a value worked out by hand can disagree with what the
  tool would have produced for the same inputs.
- **Whatever `ind_jobs` you propose is only a hint.** `generate_workflow`
  clamps it to the memory- and core-safe range before it takes effect, so a
  proposed value that turns out too high costs throughput, not host
  stability — the clamp is unconditional and does not depend on getting the
  proposal right.
