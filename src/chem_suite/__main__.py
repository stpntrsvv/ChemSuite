if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from chem_suite.cli import main
    raise SystemExit(main())
