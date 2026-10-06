"""Cross-platform command entry point, also usable from a source checkout."""
from .installer import main

raise SystemExit(main())
