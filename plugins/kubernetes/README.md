# Kubernetes workloads & Flux

A read-only [labdex](https://github.com/labdex-app/labdex) plugin: what runs in one cluster
(Deployments, StatefulSets, DaemonSets with ready against wanted replicas) and how Flux is doing
(Kustomizations, HelmReleases and their Git, OCI and Helm sources, with Ready state and revision).
It notifies when a Flux object starts failing or a workload loses replicas. Add one per cluster.

Documentation: *Plugins › Kubernetes workloads & Flux* in the labdex docs.

Licence: AGPL-3.0 (see LICENSE and NOTICE).
