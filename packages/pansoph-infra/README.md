# pansoph-infra

The two infrastructure ports that the platform needs but that are not domain logic: blob storage and secret storage. Both are replaceable.

## ObjectStore (S3 API)

Stores parsed blobs and large payloads. Default adapter is `s3` (works against SeaweedFS, Garage, or any S3). A `memory` adapter exists for tests.

## SecretStore

Stores connector OAuth tokens and service credentials at rest. The default adapter is `envelope`: per-tenant data keys derived from a root key with HKDF, values sealed with AES-GCM, so a single box needs no external secret service. The `openbao` adapter is the opt-in for dynamic secrets, leasing, audit, and namespaces. Vault is not used, since it is now BUSL.

The root key must come from outside source control (an age key or a KMS). Never put it in a committed file.
