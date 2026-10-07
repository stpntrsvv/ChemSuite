from chem_suite.core.contracts import ActionSpec, ModuleSpec, OptionSpec

SPEC = ModuleSpec(
    "cycling",
    "Циклирование",
    "0.2.0",
    (
        ActionSpec("analyze", "Ёмкость и эффективность", "chem_suite.modules.cycling.actions:analyze_file"),
        ActionSpec(
            "discover", "Поиск экспериментов", "chem_suite.modules.cycling.actions:discover", visible=False
        ),
        ActionSpec("view", "Выбор циклов", "chem_suite.modules.cycling.actions:view_file", visible=False),
    ),
    "cycling",
    options=(
        OptionSpec(
            "format", "Формат", "choice", "auto", ("auto", "biologic", "mpt", "mpr", "csv", "yarst", "elins")
        ),
        OptionSpec("nominal_capacity_mAh", "Номинальная ёмкость, mAh (необязательно)", "number"),
        OptionSpec(
            "voltage_channel",
            "Канал напряжения BioLogic",
            "choice",
            "auto",
            ("auto", "Ecell/V", "Ewe-Ece/V", "Ewe/V", "<Ewe>/V", "<Ewe/V>", "Ece/V"),
        ),
        OptionSpec(
            "current_channel",
            "Канал тока BioLogic",
            "choice",
            "auto",
            ("auto", "<I>/A", "<I>/mA", "I/A", "I/mA"),
        ),
        OptionSpec("voltage_kind", "Тип напряжения", "choice", "auto", ("auto", "cell", "electrode")),
        OptionSpec("rest_threshold_A", "Порог тока паузы, A", "number", "1e-9"),
        OptionSpec("cycle_policy", "Определение циклов", "choice", "auto", ("auto", "direction")),
        OptionSpec(
            "file_cycle_policy", "Счётчик между файлами", "choice", "separate", ("separate", "continue")
        ),
        OptionSpec("reference_cycle", "Опорный цикл (пусто — первая пара)", "number"),
        OptionSpec("selected_cycles", "Циклы на графиках (например: 0, 1, 5-10)", "text"),
        OptionSpec("table_page", "Страница таблиц (200 строк)", "integer", 1, minimum=1, maximum=100000),
        OptionSpec("export_scope", "Циклы для экспорта", "choice", "all", ("all", "selected")),
    ),
    supports_directory=True,
)
