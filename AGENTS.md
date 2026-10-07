# Chem Suite development boundaries

- `core` must use only the standard library and never import desktop or domain modules.
- Register lightweight module declarations in `bootstrap.py`; load scientific handlers only in job processes.
- EIS and cycling must not import each other. Optional dependencies belong to the corresponding extra.
- Keep import, parsing, analysis and bulk export out of the Qt event loop. Use JobService or an isolated service.
- Cross process boundaries using versioned JSON contracts and artifact references, not QObject or optimizer objects.
- Preserve source data and provenance. Never publish a cancelled or partial job as a successful scientific result.
- Before changing execution, run failure/cancellation/timeout and Qt heartbeat acceptance tests.
- Before changing mathematics, validate physical units, conventions and representative numerical baselines.
- Runtime caches, virtual environments and experiment data are excluded from version control.

