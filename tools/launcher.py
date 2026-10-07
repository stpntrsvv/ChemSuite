"""Frozen entry point: multiprocessing dispatch must precede application imports."""
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    import os
    import sys
    import time
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if len(sys.argv) == 2 and sys.argv[1] == "--acceptance-sleep":
        time.sleep(120)
        raise SystemExit(0)
    if "--self-test" in sys.argv:
        from chem_suite.distribution.acceptance import main
    else:
        from chem_suite.cli import main
    raise SystemExit(main())
