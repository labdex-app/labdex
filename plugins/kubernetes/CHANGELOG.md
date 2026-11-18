# Kubernetes workloads & Flux

## 0.2
- Flux files as code: apps listed in labdex become Flux manifests in one folder of the Flux
  repository, proposed after an approval on a `labdex/flux-<cluster>` branch with a merge request.

## 0.1
- First version, read-only: Deployments, StatefulSets and DaemonSets with ready against wanted
  replicas, Flux Kustomizations, HelmReleases and their sources with Ready state and revision,
  a Cluster page and dashboard widget, and notifications when a Flux object fails
  (`plugin.kubernetes.flux.failed`) or a workload loses replicas (`plugin.kubernetes.workload.degraded`).
