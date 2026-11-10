# Project field grouping

Proposed tab / sub-category structure for `PROJECT_FORM` in `project_dialogs.py`.
Model paths are unchanged; only the UI placement moves.

| Tab | Sub-category | Fields (model path) |
|---|---|---|
| **1. Overview** | Identity | `name`, `description`, `status`, `date_created`, `notes` |
| | Classification | `project_type`, `deployment.stage` |
| **2. Source & Environment** | Local source | `source_location`, `machine` |
| | Paths | `paths.config`, `paths.data`, `paths.logs`, `paths.artifacts` |
| | Commands | `commands.start`, `commands.restart`, `commands.stop` |
| **3. Hosting & Deployment** | Deployment target | `deployment.environment`, `deployment.platform`, `deployment.runtime`, `deployment.service` |
| | Host / server | `deployment.provider_name`, `website.hosting.provider`, `website.hosting.provider_url`, `website.hosting.plan`, `website.hosting.ip_address`, `deployment.remote_path` |
| | Access | `deployment.credentials.*`, `deployment.ssh_key_path` |
| | Endpoints & health | `deployment.site_url`, `deployment.health_url` |
| | Billing / renewal | `website.hosting.purchased_on`, `renewal_fields(website.hosting)` |
| **4. Domain & DNS** | Domain | `website.domain.name`, `website.domain.registrar`, `website.domain.registrar_url`, `website.domain.registered_on` |
| | DNS | `website.dns.provider`, `website.dns.provider_url`, `website.dns.nameservers`, `website.dns.records` |
| | Billing / renewal | `renewal_fields(website.domain)` |
| **5. Cloud** | Account | `cloud.provider`, `cloud.account`, `cloud.project` |
| | Resources | `cloud.region`, `cloud.service` |
| | Access | `cloud.credential_ref` |
| **6. Data & Services** | Database: provider | `database.provider` |
| | Database: connection | `database.db_name`, `database.credentials.*` |
| | Payments | `payments.provider`, `payments.mode`, `payments.account`, `payments.webhook_url`, `payments.credential_ref` |
| | AI | `ai.provider`, `ai.model`, `ai.endpoint`, `ai.credential_ref` |

## Rules

- Each service section follows the same order: Identity, Location, Access (credentials), Lifecycle (billing / renewal).
- Credentials always live in an "Access" sub-block and are always vault keys.
- Hosting moves out of the website tab and sits with deployment, since both describe where the project runs.
- Domain and DNS stay together as registrar / zone concerns.
- Database, Payments and AI share a tab: each is a third-party service with a provider, account and credential.
