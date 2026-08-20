# gitlab

Grafana dashboard for a **GitLab Omnibus** install, built on the exporters
GitLab bundles: node, Postgres, Redis, Gitaly, Puma, Workhorse, Sidekiq, and
the gitlab-exporter. Panels cover host resources, request/latency by
component, Postgres/Redis health, and background-job throughput.

## Enable metrics

GitLab Omnibus runs these exporters by default. If they were disabled,
re-enable in `/etc/gitlab/gitlab.rb`:

```ruby
prometheus_monitoring['enable'] = true
```

and allow your Prometheus host to reach the exporter ports:

```ruby
gitlab_rails['monitoring_whitelist'] = ['<prometheus-ip>/32']
```

Run `gitlab-ctl reconfigure` after editing.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml) — eight jobs on one
host. **Keep the job names** (`gitlab-node`, `gitlab-puma`, `gitlab-gitaly`,
…); the dashboard filters panels by them.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
