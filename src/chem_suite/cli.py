import argparse
import json
import sys
import time
from pathlib import Path

from chem_suite.core.paths import default_workspace
from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService


def main(argv=None):
    parser = argparse.ArgumentParser(prog="chem-suite", description="Chem Suite — модульная электрохимия")
    commands = parser.add_subparsers(dest="command")
    gui = commands.add_parser("gui", help="Открыть приложение")
    gui.add_argument("--workspace", default=str(default_workspace()))
    commands.add_parser("modules", help="Показать модули без загрузки научных библиотек")
    run = commands.add_parser("run", help="Запустить анализ в изолированном процессе")
    run.add_argument("module", choices=[m.id for m in builtins().modules()])
    run.add_argument("action")
    run.add_argument("inputs", nargs="*")
    run.add_argument("--config", default="{}", help="JSON с настройками")
    run.add_argument("--workspace", default=str(default_workspace()))
    run.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    if args.command in {None, "gui"}:
        from chem_suite.desktop.app import launch

        return launch(Path(getattr(args, "workspace", default_workspace())))
    if args.command == "modules":
        for module in builtins().modules():
            print(f"{module.id}: {', '.join(a.id for a in module.actions)}")
        return 0
    try:
        request = JobRequest(
            args.module, args.action, tuple(args.inputs), json.loads(args.config), args.timeout
        )
        with JobService(builtins(), args.workspace) as jobs:
            record = jobs.submit(request)
            while not record.state.terminal:
                for event in jobs.poll():
                    print(f"{event.state}: {event.message}", file=sys.stderr)
                time.sleep(0.05)
            if record.state == JobState.SUCCEEDED:
                print(record.result_path)
                return 0
            print(record.error or record.message, file=sys.stderr)
            return 1
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
