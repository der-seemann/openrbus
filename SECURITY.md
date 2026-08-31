# Security policy

OpenRBus interacts with heating equipment and must treat protocol mistakes,
unsafe writes, credential disclosure, and privacy leaks as security-relevant.

## Reporting a vulnerability

Use the repository's **Security** tab to submit a private vulnerability report.
Do not open a public issue for a suspected credential, authentication, write-
safety, or device-privacy problem. Include the affected revision, the smallest
safe reproducer, the expected impact, and whether real equipment was involved.

Never attach credentials, pairing material, device identifiers, serial numbers,
captures from an occupied installation, or proprietary vendor files. Replace
them with synthetic data before submission.

The project is currently alpha and does not promise a response or disclosure
deadline. Maintainers will acknowledge valid reports before coordinating a fix
and public advisory when practical.

## Security boundaries

- Pairing and device authorization are owner-controlled. OpenRBus does not ship
  a shared PIN, unlock algorithm, manufacturer authentication constant, or
  service credential.
- Level-2/3 TEA authorization is functionally equivalent to entering the
  documented `0012` installer/professional code at the physical control panel.
  In both paths, preventing unauthorized access depends on restricting physical
  access to the heating room and appliance. Without physical access, the
  installation-specific BLE pairing PIN cannot be obtained and OpenRBus cannot
  establish remote BLE access.
- `authorizer` is an application boundary, not a credential store. Applications
  must retrieve sensitive values outside this package and must not log them.
- Write gates reduce accidental use; they are not an access-control sandbox.
  Python callers can deliberately reach private methods. Protect the host and
  restrict who can run code against real equipment.
- Log redaction covers common textual identifiers and assignments, but callers
  must still avoid passing secrets or raw installation data to logs.
- Registry declarations and read-back verification do not establish that a
  write is physically safe.

Level-2/3 operation does not add a distinct access-control risk beyond the
physical service interface, but it does carry substantial operational risk.
Incorrect settings can increase energy consumption, accelerate equipment wear,
and reduce comfort.

## Supported versions

Only the latest revision of the `main` branch is considered during the alpha
phase. No released version currently carries a long-term support commitment.
