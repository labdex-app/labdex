# Kubernetes workloads & Flux

The **Kubernetes workloads & Flux** integration shows what runs in one cluster and whether
[Flux](https://fluxcd.io) keeps it in step with git. It is read-only: labdex only sends `GET`
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
