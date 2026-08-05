# Capacity Planning from Scientific Intent

## Question

Given the workflow's scientific scope and estimated input volume, how much useful parallelism exists, and therefore how many vCPUs should be provisioned?

## Inputs already available from INTERPRET

The existing `ResearchIntent` supplies the required scientific scope:

- `regions` or `chromosomes` determine the number of independent workflow branches;
- region coordinates identify the data whose volume must be estimated;
- `populations` determine downstream population-task fan-out and contribute to the cohort-size estimate.

INTERPRET does not need to choose or extract resource parameters. It must reliably map the research question to populations, regions, and chromosomes using domain knowledge.

## Deterministic derivation

```text
ResearchIntent
  ↓
number of regions/chromosomes
estimated variants per region/chromosome
estimated individuals
population fan-out
  ↓
sensible chunk count per branch
available workflow parallelism
serial fraction / critical path
  ↓
useful vCPU capacity
  ↓
cluster size to provision
```

The deterministic planner should:

1. Estimate input volume for every region or chromosome.
2. Choose a useful chunk count based on volume and per-chunk overhead.
3. Construct the expected DAG parallelism from branches, chunks, and populations.
4. Apply the calibrated performance model and Amdahl's-law limit.
5. Return the useful number of vCPUs to provision.

Amdahl's law supplies the governing bound: capacity should not exceed the point where the workflow's serial portion—particularly merge—dominates and additional vCPUs provide negligible speedup.

After extraction, actual row and individual counts can refine task sizing and concurrency. The cluster-capacity estimate must nevertheless be produced before provisioning.

## Knowledge placement

Prose consumed during INTERPRET remains domain knowledge:

- population mappings;
- named-region coordinates;
- rules for resolving a question into regions or chromosomes.

The performance policy is not an interpreter decision. Its assumptions, evidence, and calibration belong in maintainer-facing prose; its operative coefficients and constraints belong in structured configuration consumed by the deterministic capacity model.

## Conclusion

No additional resource fields are required in `ResearchIntent`. The immediate requirement is to connect the existing scientific intent to a deterministic, workflow-global performance model that derives useful parallelism and the vCPU capacity to provision.
