export const meta = {
  name: 'implement-plan',
  description: 'Decompose a section of a 1000genome-workflow RFC into tasks, implement each with Sonnet, independently verify, iterate until green',
  whenToUse: 'When the user asks to implement a section of an RFC (e.g. RFC-003 §7) with the multi-agent implement/verify loop.',
  phases: [
    { title: 'Plan', detail: 'decompose the RFC section into ordered implementation tasks' },
    { title: 'Implement', detail: 'one Sonnet implementer per task', model: 'sonnet' },
    { title: 'Verify', detail: 'independent gate + equivalence + non-emptiness check', model: 'sonnet' },
  ],
}

// args: { rfc: 'RFC-003-curated-parameter-policy.md', section: '7. Implementation sketch', maxIterations: 3 }
const rfc = (args && args.rfc) || 'RFC-003-curated-parameter-policy.md'
const sectionName = (args && args.section) || '7. Implementation sketch'
const maxIter = (args && args.maxIterations) || 3

const TASKS_SCHEMA = {
  type: 'object',
  required: ['tasks'],
  properties: {
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'description', 'acceptance', 'testCommand'],
        properties: {
          title: { type: 'string' },
          description: { type: 'string', description: 'Complete, self-contained instructions: what to build, in which module, which RFC section to consult, and how output equivalence must be demonstrated' },
          files: { type: 'array', items: { type: 'string' }, description: 'Expected files to create or modify' },
          acceptance: { type: 'string', description: 'Concrete, checkable acceptance criteria, including the equivalence or non-emptiness property to preserve' },
          testCommand: { type: 'string', description: 'A command that must pass in under a few minutes without Docker, network, or a full integration run, e.g. "python3 -m pytest workflow-composer/tests/ -q"' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['pass', 'issues'],
  properties: {
    pass: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' }, description: 'Required fixes, each with file:line and concrete defect description; empty if pass' },
  },
}

phase('Plan')
const plan = await agent(
  `Read ${rfc} in full and decompose its section "${sectionName}" into 3–8 ` +
  `implementation tasks, ordered so each builds only on its predecessors. ` +
  `Each task must be small enough for one focused coding session and testable ` +
  `with the pytest suite or a direct script invocation. ` +
  `NEVER emit a task whose test command needs a full integration run, a Docker ` +
  `image rebuild, an FTP download, or an LLM API call — those take minutes to ` +
  `hours and are human-supervised. Unit-level tests against ` +
  `workflow-composer/tests/, or replaying one chunk of individuals.py against ` +
  `the preserved baseline at tests/integration/workflow-eur-afr-hla-baseline/, ` +
  `are the right granularity. ` +
  `Where a task changes an analysis script or chunking, its acceptance criteria ` +
  `must name the output-equivalence check and the comparison method (extracted ` +
  `trees, never .tar.gz bytes; union comparison if chunk boundaries move; never ` +
  `byte-comparison of the unseeded mutation_overlap/frequency outputs). ` +
  `Check what already exists under workflow-composer/ and worker-base-image/ ` +
  `and do not re-plan work that is already implemented and tested.`,
  { label: `plan:${sectionName}`, schema: TASKS_SCHEMA }
)
if (!plan || !plan.tasks || plan.tasks.length === 0) {
  return { rfc, section: sectionName, error: 'planning produced no tasks' }
}
log(`${plan.tasks.length} tasks planned for ${sectionName}`)

// Tasks are dependency-ordered, so run them sequentially; the
// implement -> verify -> fix loop iterates within each task.
const results = []
let aborted = false
for (let i = 0; i < plan.tasks.length; i++) {
  const t = plan.tasks[i]
  log(`Task ${i + 1}/${plan.tasks.length}: ${t.title}`)
  let feedback = ''
  let verdict = null
  for (let iter = 1; iter <= maxIter; iter++) {
    await agent(
      `Implement this task (iteration ${iter}/${maxIter}).\n\n` +
      `Specification: ${rfc}, section "${sectionName}".\n\n` +
      `Title: ${t.title}\n\nDescription:\n${t.description}\n\n` +
      `Expected files: ${(t.files || []).join(', ') || 'per RFC'}\n` +
      `Acceptance criteria: ${t.acceptance}\n` +
      `Test command (must pass): ${t.testCommand}\n` +
      (feedback ? `\nA reviewer found these issues in the previous iteration — fix them all:\n${feedback}\n` : ''),
      { label: `impl:${t.title}`, phase: 'Implement', agentType: 'implementer' }
    )
    verdict = await agent(
      `Verify the just-implemented task "${t.title}".\n` +
      `Specification: ${rfc}, section "${sectionName}".\n` +
      `Acceptance criteria: ${t.acceptance}\n` +
      `Test command: ${t.testCommand}\n` +
      `Expected files: ${(t.files || []).join(', ') || 'unspecified'}\n` +
      `Follow your verification steps. pass=false if the gate fails, the ` +
      `equivalence claim is unreproduced or used an invalid method, the output ` +
      `is present but empty, acceptance criteria are unmet, the change diverges ` +
      `from the RFC, or you find a correctness bug.`,
      { label: `verify:${t.title}`, phase: 'Verify', agentType: 'verifier', schema: VERDICT_SCHEMA }
    )
    if (verdict && verdict.pass) break
    feedback = verdict ? verdict.issues.join('\n') : 'verifier did not return a verdict; re-run the gate and self-check'
    log(`Task "${t.title}" failed verification (iteration ${iter}): ${verdict ? verdict.issues.length + ' issues' : 'no verdict'}`)
  }
  const passed = Boolean(verdict && verdict.pass)
  results.push({ task: t.title, passed, openIssues: passed ? [] : (verdict ? verdict.issues : ['no verdict']) })
  if (!passed) {
    log(`Stopping after task "${t.title}": not green after ${maxIter} iterations — needs human review`)
    aborted = true
    break
  }
}

return {
  rfc,
  section: sectionName,
  completed: results.filter(r => r.passed).length,
  total: plan.tasks.length,
  aborted,
  results,
}
