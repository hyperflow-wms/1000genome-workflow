# Running the capacity experiments on Kubernetes

Notes from preparing and attempting the alice-k8s-datalake deployment.

**The cluster is a vcluster, and almost everything its API reports about
capacity and placement is the host's, not the tenant's.** The CPU quota that
first blocked this has since been raised to 64. What blocks it now is storage:
the host provisioner stopped binding claims of any size. §0 is what was
measured; §1 onward assumes a working volume.

---

## 0. What the vcluster actually gives you

`kubectl get nodes` shows 8 nodes and 70 vCPU, and `kubectl top nodes` shows them
nearly idle. Both are true and both are misleading — those are the host's node
objects synced into the tenant's view. The tenant's own limits live in a
ResourceQuota and a LimitRange in the *host* namespace, neither visible from
inside: `kubectl get resourcequota -A` and `get limitrange -A` both return
nothing. They surface only when something is rejected, as a `SyncError` event on
the pending object.

Measured by scheduling `pause` containers until they stopped being admitted:

| limit | value | how it fails |
|---|---|---|
| `limits.cpu` | **64** total | `exceeded quota: vc-bbalis, ... limited: limits.cpu=64` |
| per-container CPU | **1** | `must be less than or equal to cpu limit of 1` |
| per-container memory | **1Gi** | `must be less than or equal to memory limit of 1Gi` |
| Pod Security | **baseline** | `violates PodSecurity "baseline:latest"` |

64 CPU is enough for the full ladder: 40 worker slots at one core each, plus
infrastructure. The per-container caps are what shape the design — a whole core
per task is the most that can be asked for, which happens to be exactly what the
single-threaded analysis workers want, and 1Gi bounds how coarsely the
individuals stage may be split.

Four behaviours cost real time to discover, and each defeats an obvious approach:

- **`nodeSelector` is not enforced.** A pod requesting
  `hyperflow-wms/nodepool=hfmaster` — a label carried only by `server-0` — was
  placed on `workers-1`. The host scheduler decides placement and ignores labels
  written through the vcluster. The `hfmaster`/`hfworker` split the charts assume
  is therefore cosmetic here, and the engine cannot be isolated from task pods.
- **Declaring no CPU limits does not help.** The worker Job template sets requests
  only, but the host LimitRange defaults a limit onto every container and the
  quota counts `limits.cpu`. Verified: 12 requests-only 1-CPU pods, 2 ran, 10
  stayed Pending on the quota error.
- **The NFS provisioner cannot run at all.** `nfs-server-provisioner` needs the
  `DAC_READ_SEARCH` and `SYS_RESOURCE` capabilities, and the baseline Pod
  Security Standard rejects any pod adding non-default capabilities. This is not
  a tuning problem; that chart is unusable here.
- **Static PVs do not work.** A PVC created in the vcluster is synced to the host
  and provisioned there, so it needs a *host* storage class. A `hostPath` PV
  defined in the tenant is accepted by the API and never materialises.

The good news that follows from the third point: **the host default storage class
serves ReadWriteMany directly**, so the whole ganesha layer is unnecessary. A
plain PVC with no `storageClassName` bound RWX in about 25 seconds. That is what
`cluster/00-prereqs.yaml` now claims, and `hyperflow-ops` is not installed at all.

### Only the host can provision storage

**PersistentVolume objects cannot exist in this vcluster.** `kubectl apply`
reports one created and it is gone the moment you look:

```
persistentvolume/pv-existence-test created
Error from server (NotFound): persistentvolumes "pv-existence-test" not found
```

That single fact rules out two whole classes of approach, so claim from a
host-side class and do not try to be clever:

- **Static PVs are impossible.** A `hostPath` PV is accepted and silently
  discarded, leaving its PVC Pending forever against a class nothing serves.
- **Any in-cluster dynamic provisioner is impossible**, because provisioning ends
  in creating a PV. A tenant-side nfs-ganesha provisioner was driven all the way
  to `ProvisioningSucceeded` — it created `pvc-1f396b83-...`, logged it, and the
  object never existed. The claim stayed Pending.

Two chart patches were needed to get that far. Neither was sufficient, but both
are real defects worth keeping:

1. The nfs-server-provisioner chart hardcodes `DAC_READ_SEARCH` and
   `SYS_RESOURCE` in `templates/statefulset.yaml:76-80`, not exposed through
   values, and the host enforces the `baseline` Pod Security Standard, which
   rejects any pod adding non-default capabilities. Moving them to a
   `containerSecurityContext` value and leaving it empty fixes it — and costs
   nothing: the provisioner logs `RLIMIT_NOFILE rlimit.Cur 1048576` without
   `SYS_RESOURCE`.
2. **A StatefulSet cannot run on this vcluster at all.** Its pods reach the host
   with their resource requests stripped, the host LimitRange defaults them to
   30m, and that is below the same LimitRange's own 50m minimum:
   `minimum cpu usage per Container is 50m, but request is 30m`. A minimal
   StatefulSet requesting a whole core reproduces it; an identical bare Pod runs.
   Converting the workload to a Deployment makes it start.

### Use the `nfs-xattr` storage class, never `nfs`

The shared volume must be claimed from **`nfs-xattr`** (kernel nfsd on the ZFS
node). The `nfs` class is nfs-ganesha 4.0.8, and it cannot run this workflow.

Ganesha 4.0.8 performs *chunked* readdir, and its dirent cache assumes a
directory cookie stays valid between chunks. A concurrent write to that
directory invalidates the cookie, and the server answers with an I/O error that
reaches the client as `EREMOTEIO` (errno 121). Enumerating a directory while
anything writes to it is therefore unreliable -- which is precisely the
`individuals.py` pattern: write ~2500 per-individual files, then `tarfile.add`
the directory, whose first act is `os.listdir`.

Measured with the same reproducer against both classes:

| class | backend | readdir under concurrent writes |
|---|---|---|
| `nfs` | nfs-ganesha 4.0.8 | **0/60** |
| `nfs-xattr` | kernel nfsd | **60/60** |

and `tarfile.add` over a 2504-entry directory: 10/10 on `nfs-xattr`.

Two things about the failure were misleading while diagnosing it, and both fall
out of the cookie mechanism:

- **It looked bursty rather than deterministic.** Samples taken while nothing was
  writing came back 40/40 and 58/60 clean; a sample taken during a run came back
  3/5. The variable was never server health, it was whether a writer happened to
  be active during the enumeration.
- **It looked size-independent, which seemed to rule out a directory-size limit.**
  It is size-independent, because cookie validity has nothing to do with entry
  count. 100-entry and 2504-entry directories fail alike.

Only readdir is affected, because read, write and stat do not use cookies at all.
That is why file operations measured 150/150 clean throughout.

`nfs-xattr` also serves extended attributes (NFSv4.2 / RFC 8276), which Ganesha
did not.

Do not fall back to omitting `storageClassName`. That selects the host default
`cinder-perf`, which advertises RWX on the PV and does not deliver it: it is
block storage, so a second pod mounting it lands on another node with no shared
filesystem underneath.

## 1. There is no concurrency dial in the Kubernetes execution path

The laptop measurements were produced by an in-process semaphore. `redisCommand`
holds a counter and spins until a slot frees:

```
functions/redisCommand.js:15   const MAX_PARALLELISM = process.env.HF_VAR_REDIS_CMD_MAX_PARALLELISM || 10;
functions/redisCommand.js:96   while (numParallelJobs == MAX_PARALLELISM) {
```

`FORCE_MAX_PARALLELISM` in the harness sets that variable, which is why it varies
allocated capacity while leaving `ind_jobs` alone.

`k8sCommand` has no equivalent. It submits every ready task as a Job the moment
its inputs are satisfied; there is no counter anywhere in `k8sCommand.js` or
`kubernetes/k8sJobSubmit.js`. The only pacing mechanism is the optional admission
controller (`HF_VAR_ADMISSION_CONTROLLER=1`), and it caps *pending* pods to
protect the scheduler — it is a rate limiter with an adaptive window, not a
capacity allocation.

So the concurrency dial that produced every measurement in `RFC-006-REVIEW.md`
does not exist on the backend the plan wants to validate against.

## 2. The ResourceQuota does not bind pod-per-task workers

§4.2 assumes it does:

> the chart already enforces `resourceQuota.hard.requests.cpu` and worker pods
> already carry CPU requests, so the quota throttles concurrency exactly as an
> allocation would

Two things make that false as the charts stand.

**The quota is not created.** `charts/hyperflow-run/templates/resourcequota.yml:1`
is gated on `workerPools.enabled` as well as on `resourceQuota.enabled`. The
pod-per-task deployment sets the former false, so the template renders nothing.

**It would not match if it were.** The quota carries a `scopeSelector` limiting
it to pods with `priorityClassName: hyperflow-worker`
(`templates/resourcequota.yml:16-21`). That class is stamped on worker-pool pods
by the operator. The pod-per-task Job template
(`charts/hyperflow-run/values.yaml:207-293`) sets no `priorityClassName` at all,
so worker Jobs fall outside the scope and the quota constrains nothing.

Both are fixable, and `cluster/values-run-1000genome.yaml` in the deployment repo
fixes them: it adds `priorityClassName: hyperflow-worker` to the job template and
supplies the quota as a standalone manifest. But fixing them does not make the
quota a good instrument — see below.

## 3. A quota is an allocation, not a queue — but the scheduler is a queue

The distinction that matters is *where* a task waits when capacity is short.

Under a quota, it waits at **admission**. The API server rejects the pod, the
pod is never created, and the Job controller retries on an exponential backoff
measured in tens of seconds. A sweep point well below `C*` would leave most of
the stage's Jobs retrying on independent backoff timers, and the makespan would
substantially measure that backoff schedule rather than the workflow's response
to capacity.

Under insufficient node capacity, it waits at **scheduling**. The pod is created,
goes `Pending`, and the scheduler binds it as soon as a core frees — it
re-evaluates on pod-completion events, with no backoff. Nothing counts this as a
failure: the job template sets `restartPolicy: Never` and
`k8sJobSubmit.js:76-77` derives `backoffLimit: 0`, both of which concern pods
that *ran and failed*, not pods that have not started.

So the cluster already has a clean concurrency dial, and it is not the quota:

> **Vary the number of nodes labelled `hyperflow-wms/nodepool=hfworker`, holding
> `HF_VAR_CPU_REQUEST=1`.**

One core per task, one task per core, and the surplus queues in the scheduler.
On this cluster that gives 8, 16, 24, 32 and 40 concurrent slots from `avx2-0`
(8 cores), `workers-0` (16) and `workers-1` (16) in combination — which brackets
both workloads:

| workload | `C*` | `C*/2` → | `C*` → | `2·C*` → |
|---|---|---|---|---|
| Q1 (HLA+BRCA1) | 9 | 8 cores | 8 or 16 | 16 or 24 |
| Q3 (multi-region) | 14 | 8 | 16 | 24 or 32 |

This depends on `server-0` staying reserved as `hfmaster` and never carrying task
pods, because the NFS backing store is pinned to it by node affinity and the
shared filesystem must not move when capacity changes. That reservation is what
frees the other three nodes to be relabelled.

The granularity is coarse and node-sized, and `C* = 9` sits awkwardly between 8
and 16. Note also that `HF_VAR_CPU_REQUEST` is *not* a substitute dial: dropping
it to 0.5 doubles the slots but puts two tasks on a core, reintroducing exactly
the CPU contention these runs exist to eliminate.

## 4. What this implies for the sequence

The three open milestones do not depend on the dial equally.

**M2, re-fitting the coefficients, needs no dial at all.** It needs per-task
timings that are not contention-confounded, and the cluster gives that directly:
with `HF_VAR_CPU_REQUEST=1` against 40 worker cores, the scheduler will not place
two single-threaded tasks on one core. Both workloads fit at the top of the
ladder: Q3's widest stage asks for 15.8 slots and Q1's for 33.3, against 40
labelled cores. Anything that does not fit queues as a `Pending` pod rather than
sharing a core, so the per-task times stay clean either way — which is what the
calibration needs. This is the highest-value work and it is unblocked.

**M6 and the §4.1 capacity sweep do need one**, as does the question the cluster
was brought in to answer (§5). The node-count dial in §3 is enough to start, and
it needs no code change.

If finer granularity turns out to be necessary — the Q1 knee at 9 is the likely
reason — the option is to add a semaphore to `k8sCommand` mirroring
`redisCommand`'s. That would also make the two backends directly comparable: the
same mechanism and the same variable, so a cluster-versus-laptop difference is
attributable to the machine rather than to the instrument. It is worth doing only
if the coarse dial proves inadequate.

Either way, the two roles the plan currently gives the quota should be separated.
As the *production binding point* (D2) the quota is right: it is how an operator
caps a workflow's footprint, and wiring it to `plan.capacity.slots × cpuRequest`
remains worthwhile. As the *measurement instrument* it is not, and the claim to
validate under it is that the workflow degrades gracefully, not that
makespan-versus-`C` curves can be produced with it.

## 5. What the cluster does and does not separate

The brief's hypothesis was that six nodes remove the CPU component of the
observed per-task inflation while shared storage persists, separating the two.

The separation is not clean in the form stated. On the laptop the shared
directory was local disk with a warm page cache. On the cluster it becomes a
single NFS server pod reached over the network. That is not the same storage with
less CPU contention; it is slower storage *and* less CPU contention, moving both
variables at once. If per-task times inflate on the cluster, absolute NFS
slowness and neighbour-dependent contention are confounded.

The clean version of the experiment is available on the cluster, and it needs the
dial from §4: hold the storage fixed and vary only concurrency, with one core
guaranteed per task. If per-task time still inflates with concurrency while every
task has a core to itself, the coupling is storage. `individuals_merge` remains
the control — it runs alone, and it moved +2% on the laptop while its neighbours
moved +60% to +243%.

This is also the reason not to fit coefficients on the cluster and apply them to
the laptop, or the reverse. §7 of the plan already says the coefficients do not
transfer across storage architectures; the NFS backing makes that concrete rather
than hypothetical.

## 6. Deployment facts worth not rediscovering

- **The cluster has no storage class.** `kubectl get sc` is empty and there are no
  CSI drivers, so the NFS provisioner that supplies the `nfs` class has nowhere
  to put its own backing store. `cluster/00-prereqs.yaml` supplies a static
  hostPath PV for it, pinned by node affinity to the node that runs it.
- **The worker image the harness pins is not published.**
  `hyperflowwms/1000genome-worker:1.4-je1.4.2` exists only in the local Docker
  daemon. Tag 1.4 carries the RFC-005 fix (`1aade0a`, "Slice variants rather than
  file lines"), which lives on `restructure-engine-backends`; the publishing
  workflow triggers on pushes to `main`, so CI never built it. The newest tag on
  Docker Hub is `1.3-je1.4.2`, which still has the bug — and Q3 includes APOE,
  the region that exposed it. Nodes cannot use a local image, so this must be
  published before any cluster run is scientifically meaningful.
- **`HF_VAR_DEBUG` defaults to `1`, and at `1` the engine does not run the
  workflow** (`charts/hyperflow-run/values.yaml:143`); it parks in an idle loop.
  That is useful — it is what lets one deployment serve every sweep point — but a
  run that appears to do nothing is usually this.
- **The engine subchart keeps its own `workerPools.enabled`.** Setting only the
  top-level one leaves `RABBIT_HOSTNAME` pointing at a RabbitMQ that a
  pod-per-task deployment never installs.
- **The `worker-config` volume must be removed when worker pools are off.** It
  references a ConfigMap created only by `templates/workerpools-cm.yml`, which is
  gated on `workerPools.enabled`; left in place the engine pod stays in
  `CreateContainerConfigError`.
- **Per-task logs port unchanged.** The job executor writes them to
  `$HF_VAR_WORK_DIR/logs-hf` from inside each worker
  (`hyperflow-job-executor/handler.js:381`), which is the shared volume in both
  backends. The calibration reads the same format either way. They are still
  overwritten by each run, so `run-workflow.sh` copies them off before returning.
- **None of these charts supports tolerations.** The three `db-*` nodes are
  tainted `dedicated=database` and the `nfs`-role node is tainted too, so 24 of
  the cluster's 70 vCPUs are unreachable without editing the job template.
