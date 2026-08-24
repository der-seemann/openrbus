# Proprietary formats are deferred

The public project intentionally does not parse vendor applications, binaries,
encrypted resources, internal databases, or undocumented proprietary project
formats. Those inputs are neither runtime dependencies nor build inputs.

The registry exporter accepts only the normalized public JSON schema. Keeping
that boundary small makes releases reproducible and prevents an accidental
dependency on licensed files, secrets, private research paths, or installation
data.

A future importer belongs in the public repository only when all of the
following are true:

- the format can be documented from lawful, independently publishable facts;
- the implementation is original and carries no copied or mechanically
  translated proprietary code;
- tests use synthetic fixtures created for the project;
- no key, authentication constant, vendor file, or identifying runtime data is
  required;
- the output is reviewed against the public registry content policy;
- licensing and redistribution rights are explicit.

Until those conditions are met, conversion stays outside OpenRBus. Only the
resulting normalized public facts may be proposed through the documented
registry contribution process.
