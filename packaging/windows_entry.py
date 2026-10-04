"""Entry point for the console-free Windows executable."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from openpowerstation.desktop import main
    raise SystemExit(main())

