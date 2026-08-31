# Disclaimer

OpenRBus is experimental interoperability software. It is not affiliated with,
authorized by, sponsored by, or endorsed by BDR Thermea or any of its brands.
Product names may be used only to describe compatibility targets.

The software and register dataset are provided without warranty. Heating and
hot-water systems can contain combustion equipment, high temperatures,
pressurized circuits, pumps, valves, and safety controls. Incorrect reads may
lead to bad automation decisions. Incorrect writes may cause equipment damage,
unsafe operation, loss of comfort, increased energy use, loss of warranty, or
regulatory non-compliance.

Do not rely on OpenRBus for a safety function. Keep all original safety devices
and control limits in service. Before testing on real equipment, ensure that a
qualified person can identify the affected circuit, observe the result, restore
the original value, and shut the system down safely if necessary.

All published writable definitions remain unverified. Explicit opt-ins,
validation, rate limiting, and read-back checks reduce some software mistakes;
they do not prove that a requested setting is safe for a particular device or
installation. You are responsible for authorization, compatibility, backups,
recovery, and all consequences of use.

Access levels 2 and 3 expose the same installer/professional capabilities that
are available at the physical control panel after entering the documented code
`0012`. Incorrect settings at these levels can increase energy consumption,
accelerate equipment wear, and reduce comfort. OpenRBus does not remove that
operational risk merely because the same access is exercised programmatically.
