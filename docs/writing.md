# Write policy

Write safety and device access level are independent dimensions:

- `validated` / `unverified` / `read_only` describes confidence in writing the
  object. `allow_unsafe=True` is required only for `unverified` definitions.
- `required_access_level` describes the device role required by static family
  evidence. It does not make an otherwise unsafe write safe.
- `AccessPolicy.max_access_level` is the caller-selected ceiling for both reads
  and writes. It does not authorize the device or weaken write safety.

The access policy defaults to level 1 and writes default to disabled. See
[`access-policy.md`](access-policy.md) for the full model and configuration
sources.

No published register is currently classified as hardware-validated for safe
writing. Definitions with declared write access are therefore still marked
`unverified`; all others are read-only. The `validated` classification exists
so a future, independently reviewed user-level register can use the normal
`enable_writes=True` path without an unrelated unsafe opt-in.

## Device access levels

The confirmed application-side role model is:

| Level | Role | Library treatment |
| ---: | --- | --- |
| 1 | User | Ordinary setpoints and low-risk user functions |
| 2 | Installer | Higher-risk configuration; session level is checked |
| 3 | Professional | Deeper configuration; session level is checked |

Static device definitions also contain numeric levels `0`, `4`, `5`, and `15`.
OpenRBus preserves these values but gives them no speculative role names.
Anything at level 2 or above is exposed as higher-risk access metadata.

Access-level coverage is device-specific, not universal. The current registry
has 1,451 device-evidence rows covering 1,189 concrete object addresses. Every
one of those rows has one exact read and write level. Only 692 of the 2,832
globally declared-writable canonical definitions have any device evidence;
the remaining 2,140 therefore have an unknown access requirement. Twenty-eight
concrete addresses have different levels across families, while no
family/address pair is internally ambiguous. Such cross-family cases require
`device_family` instead of choosing the least restrictive level.

`RegisterDefinition.access_requirement()` returns the static read or write
requirement for a concrete object. `WritePlan.required_access_level` and
`WritePlan.higher_risk_access` expose the resolved result.

## Live session level

`OpenRBusClient.refresh_session_access(node)` reads, but never writes:

- `4002:00`: authoritative effective access level.

The returned `SessionAccess` is cached per node and available through
`session_access(node)` and `session_access_levels`. Before an active write with
a known requirement, the client refreshes `4002:00` and requires
`effective_level >= required`. A mismatch raises
`InsufficientAccessLevelError` before the raw write primitive is called.
Invalid access-level payloads and ambiguous family evidence have separate
errors, so they are not confused with value, safety, or CANopen abort failures.
Before this live check, the local access policy must permit the operation.

This model follows live SCB-10 evidence and the official authorization flow.
With `4002:00 = 1` and `4003:00 = 3`, all tested level-1 objects were readable
while all tested level-2/3 objects returned `unsupported object access`.
`4003:00` is a challenge-response object and is not used as a session grant.

The same SCB-10 exposes a 102-entry `5826` internal-configuration directory,
but that directory contains neither the rejected level-3 object nor several
successfully readable level-1 controls. It is a dynamic subset, not a complete
object-access whitelist, so absence from `5826` is not used as an access gate.

OpenRBus never writes `4002:00`; it is verification-only. A dry run remains
I/O-free: it reports the static required level and checks an already cached
session sample, but does not connect merely to discover the current level.

## Explicit node authorization

`NodeAuthorizer` implements the official sequence without including the
manufacturer-owned TEA key component:

1. request a free node channel at `4004:00` and explicitly select it;
2. read the node serial at `2001:0a` and dynamic token at `4001:00`;
3. calculate the TEA response from target level, serial, token, and an external
   four-byte key component;
4. write `4003:03`, then `4003:01`, then `4003:02`;
5. accept success only when `4002:00` equals the requested level.

The key component can be passed as four big-endian runtime bytes, through an
explicit file path, or through a caller-selected environment variable
containing that path. Text files use eight conventional hexadecimal digits.
No default location or environment-variable name exists. The component is
excluded from object representations, return values, logs, and error messages;
it is never cached by OpenRBus. The package neither supplies the component nor
suggests where to obtain it.

Authorization is opt-in and per node. It is not attempted automatically by
reads or writes. It also requires an adapter implementing
`AsyncNodeAuthorizationAccess`; ordinary generic-purpose CAN-IP object access
cannot express the allocated-channel transition and is rejected before I/O.
Without external key material, ordinary level-1 operation remains unchanged.
Level-2/3 operations first require an explicit local policy and then continue
to fail at the effective-level gate until the node is actually authorized.

The active BLE transport uses a second, capture-validated form of the official
flow. `CanIpGatewayAuthorizer` sends CAN-IP authorization-purpose identification
and challenge-response messages with the same externally supplied four-byte
component. CAN-IP carries serial, token, and response words in network byte
order. A positive gateway response is only transport authorization; callers
must immediately verify the target node through `4002:00`. This path was
hardware-validated on SCB-10 with level 3, including a reversible CP733 write
and confirmed restoration of the original value.

Neither authorizer runs implicitly. Both accept direct runtime bytes, an
explicit file path, or an explicitly named environment variable containing a
file path. Neither logs or returns challenge material. The library still
performs no password guessing, key discovery, automatic level selection, or
fallback to a bundled/default secret.

## Required gates

`OpenRBusClient.write()` requires all applicable checks to pass:

1. The client's access policy permits the register's required level.
2. The client was constructed with `enable_writes=True`.
3. The register is globally declared writable.
4. `allow_unsafe=True` is provided if the definition is `unverified`.
5. No unresolved wire-type conflict exists.
6. Supplied device-family evidence does not mark the register non-writable.
7. The required write level is unambiguous for the supplied family.
8. For an active write with a known requirement, the refreshed live session
   level satisfies it.
9. A conflicting range has exactly one matching device-family variant.
10. The value matches the semantic type, storage width, gain, range, precision,
   enumeration, and supported structure policy.

`dry_run=True` performs all static checks and returns the encoded bytes without
I/O. It still requires the write and applicable unsafe opt-ins so the same call
cannot become active merely because configuration changes elsewhere.

## Active writes

Active writes are serialized and subject to `minimum_write_interval`. The raw
client uses a confirmed-write request and validates node/address correlation in
the response. Read-back verification is enabled by default and compares exact
bytes. The client does not automatically retry an interrupted write.

Writes are deliberately single-object and sequential. No group-write, commit,
or rollback operation was found in captures or static analysis, and none is
validated on the supported protocol path. OpenRBus therefore does not offer a
transactional `write_many()` facade. Applications coordinating several writes
must treat every confirmed result independently and allow for partial
completion. The not-found result is not proof that no proprietary
implementation can exist.

Read-back proves only that the returned bytes match the request. It does not
prove that the setting is meaningful, persistent, reversible, or physically
safe. A controller may normalize a value, delay its effect, reject it later, or
apply side effects elsewhere.

## Hardware-validation requirements

Before any definition can receive a stronger safety classification, testing
must be explicit, reversible, and independently reviewed. At minimum record:

- exact object address, wire type, starting value, and intended test value;
- device family and relevant firmware without publishing unique identifiers;
- an observation plan and a tested recovery path;
- confirmed response, read-back, physical effect, persistence, and restoration;
- failure behavior for out-of-range and interrupted requests.

Tests must start with a dry run and use the smallest reversible change. No live
write should be performed solely to improve test coverage.
