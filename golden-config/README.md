# Golden Config repository (shared by every customer)

This repository is synced by Nautobot as the Git repository `golden-config` and provides two things:

* `templates/<network_driver>.j2`: Jinja templates that render each device's intended configuration.
* `golden_config/...`: compliance features, compliance rules, lines removed before comparison and
  secrets replaced before a backup is stored ("Golden Config properties").

Change a file, commit, then in Nautobot open Extensibility > Git Repositories > golden-config > Sync.

Customer-specific values do not belong in templates. Put platform standards in the config context
`msp-standards` and customer overrides in a config context scoped to that customer's tenant.
