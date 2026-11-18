# Kubernetes workloads & Flux

The **Kubernetes workloads & Flux** integration shows what runs in one cluster and whether
[Flux](https://fluxcd.io) keeps it in step with git. Towards the cluster it is read-only: labdex only sends `GET`
requests and never changes the cluster. Add one integration per cluster, in the
[site](../guide/sites.md) the cluster belongs to. Like the Pulumi plugin it lives in
`plugins/kubernetes/` of the labdex repository for now.

It sits next to the **k3s / Kubernetes** plugin, which matches nodes to inventory hosts and
reports their readiness as host status. Use both on a cluster: k3s for the nodes, this one for
what runs on them.

## What it shows

On the integration's **Cluster** page:

- **Flux**: every Kustomization and HelmRelease, failing ones first, with its Ready state and
  message, the revision it applied (`main@sha1:…`, or the chart version), its source and path
  or chart. Suspended objects are marked.
- **Sources**: the GitRepositories, OCIRepositories and HelmRepositories, with URL, branch or
  tag and the revision they fetched.
- **Workloads**: Deployments, StatefulSets and DaemonSets with ready against wanted replicas and
  their images. *Leave out namespaces* hides system namespaces (`kube-system` by default).

A dashboard widget lists what isn't ready, or says that everything is.

*Read workloads and Flux* runs every 5 minutes by default. When a Flux object starts failing, or a
workload goes from ready to fewer ready replicas than wanted, it sends a notification:
`plugin.kubernetes.flux.failed` or `plugin.kubernetes.workload.degraded`. Pick them for a channel
in **Settings › Notifications**. The first read after adding the integration only learns the
current state.

A cluster without Flux still shows its workloads.

## A read-only token

Create a service account that may only read what the plugin asks for, and give labdex its token:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata: {name: labdex, namespace: kube-system}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: labdex-read}
rules:
  - apiGroups: [""]
    resources: [nodes, pods]
    verbs: [get, list]
  - apiGroups: [apps]
    resources: [deployments, statefulsets, daemonsets]
    verbs: [get, list]
  - apiGroups: [kustomize.toolkit.fluxcd.io, helm.toolkit.fluxcd.io, source.toolkit.fluxcd.io]
    resources: ["*"]
    verbs: [get, list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: labdex-read}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: labdex-read}
subjects: [{kind: ServiceAccount, name: labdex, namespace: kube-system}]
---
apiVersion: v1
kind: Secret
metadata:
  name: labdex-token
  namespace: kube-system
  annotations: {kubernetes.io/service-account.name: labdex}
type: kubernetes.io/service-account-token
```

`kubectl -n kube-system get secret labdex-token -o jsonpath='{.data.token}' | base64 -d` prints
the token. The same token works for the k3s plugin. If the token can't read Flux, the page says
so and shows the workloads.

| Setting | |
| --- | --- |
| API server | `https://<address>:6443` |
| Bearer token | the service account token |
| Cluster CA | `certificate-authority-data` from the kubeconfig, decoded |
| Cluster name | shown on the page and in notifications |
| Leave out namespaces | space separated, default `kube-system kube-public kube-node-lease` |

## Flux files as code

The same integration can keep a cluster's apps as Flux files in your Flux git repository. You
list the apps in labdex; labdex writes them as Flux manifests into one folder of the repository
and proposes the change as a merge request. It never pushes to the branch Flux reads: Flux applies
the apps when someone merges the request.

### The apps

*Settings › Flux files › Apps* is a YAML list. A Helm chart:

```yaml
- name: grafana
  namespace: monitoring          # where it's installed (default: the name)
  helm: {repo: https://grafana.github.io/helm-charts, chart: grafana, version: "8.x"}
  values: {adminUser: admin}
  depends_on: [infra]
```

or a folder of the Flux repository, applied as a Flux Kustomization:

```yaml
- name: infra
  path: ./apps/infra
  source: flux-system           # the GitRepository (default flux-system)
  prune: true
```

Both take `interval` (default `10m`) and `depends_on` (other apps). A chart can come from an
`oci://` registry too. *Show Flux files* prints what the apps become, without touching git.

Each app becomes one file in *Folder for labdex's files* (default `clusters/<cluster>/labdex`):
a HelmRepository and a HelmRelease, or a Kustomization, all in the Flux namespace. A
`kustomization.yaml` lists them, so the folder can be included from one of your Flux
Kustomizations (for example by adding it to `clusters/<cluster>/kustomization.yaml`). Every file
starts with `# Managed by labdex`. labdex only ever changes or removes files that start that way;
a file of the same name written by someone else stops the plan.

### Proposing the files

*Propose Flux files* is a plan:

1. **Plan**: labdex fetches the Flux branch, compares labdex's folder with the apps and lists
   every file it would create, change or remove, with the diff. Nothing is written.
2. **Approve**: every run waits for a person's approval, whatever the approval policy says.
3. **Apply**: labdex commits the files on top of the branch it planned against, pushes the commit
   to its own branch `labdex/flux-<cluster>` and opens a merge request (GitLab) or pull request
   (GitHub) into the Flux branch. If the Flux branch moved since the plan, it stops and asks
   for a new plan.

It needs the repository's https address (or a local path), the branch Flux reads, and a token
that may push branches and open merge requests (GitLab: a project access token with `api` and
`write_repository`; GitHub: a fine-grained token with *Contents* and *Pull requests*).
