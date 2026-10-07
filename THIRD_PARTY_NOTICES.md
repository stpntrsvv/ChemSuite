# Источники и зависимости

`src/chem_suite/modules/eis/legacy` содержит вычислительную часть EIS Solver 1.0.0
из исходного проекта Stepan Tarasov. Сохраняется GPL-3.0-or-later; текст в LICENSE.
Изменения этого baseline: относительные импорты и исправления выбора канала и
знака отрицательной мнимой части в заголовке CSV. Научные процедуры fitting/KK/
маршрутизации и диагностические пороги сохранены. Дополнительно перенесены
eis_drt.py, eis_rational.py, eis_spice.py и eis_spice_export.py: относительные
импорты и проверка отмены между итерациями DRT; численные процедуры и проверки
SPICE сохранены. Исходные тесты этих четырёх файлов и генератор синтетических
спектров перенесены в tests как проверки baseline и fixtures.

Модуль циклирования написан для новых контрактов с использованием идей и описания
форматов Flow Battery Report Maker (FlowBat team, Skoltech): Andrey Novikov,
Nikita Buriak, Ilia Khristoforov. Источник: предоставленная папка reportmaker.
Их GUI, файловые побочные эффекты и код обработки датчиков не перенесены.
Изменения методов перечислены в README. Дополнительно перенесены eis_inference,
eis_uncertainty, eis_identifiability и eis_gui_decision вместе с соответствующими
тестами. Добавлены относительные импорты, отмена между выборками и в профиле,
передача ручных границ; профиль однопараметрической схемы вычисляется без
оптимизации пустого вектора. Значения по умолчанию многопараметрических методов
и пороги решений сохранены.

Основные зависимости: NumPy/SciPy (BSD), impedance (MIT), pandas (BSD),
openpyxl (MIT), Matplotlib (лицензия Matplotlib), galvani (GPL-3.0-or-later), PySide6
(LGPL/GPL или коммерческая лицензия Qt), pyqtgraph (MIT). Julia и её пакеты остаются отдельной
опциональной графической подсистемой; их лицензии поставляются в локальном depot.


Перенесены eis_controller, eis_joint, eis_series, eis_resolution, eis_synthetic,
eis_inference_batch и исходные benchmark/корпусные процедуры interval/family/window/
replication/grid-density/diffusion/wo-guardband/SPICE/gate-regression с baseline-
тестами, замороженными SPICE-манифестами и JSON-схемой. Дополнительные изменения:
относительные импорты, необязательная проверка отмены в совместном подборе и карте
разрешимости, необязательные явно заданные метаданные серии. Значения по умолчанию
и численные пороги исходных процедур сохранены. Адаптеры платформы добавляют
снимки входов, проверку контрактов, единицы, атомарную публикацию, ограниченные
GUI-превью и переносимые сессии; исходные проекты не модифицируются.


Cycling: MPT-адаптер реализован для контрактов Chem Suite с учётом открытого
описания заголовков в galvani/BioLogic.py и документации BioLogic. Бинарный
MPR разбирает необязательная библиотека galvani (Christopher Kerr, bcolsen;
GPL-3.0-or-later); её код не копируется в Cycling. Фикстуры BioLogic в examples
и tests синтетические и не содержат лабораторных данных. Из FlowBat используются
соглашения форматов YARST/ES8 и config.json; датчики/камера не включены.

Публичные лабораторные данные скачиваются отдельно в исключённый из Git кэш;
в репозитории хранятся только manifest и проверки. Galvani test data:
Christopher Kerr (2010–2014), CC BY 4.0; SINTEF/DigiBatt:
Simon Clark, Julian Tolchard, Dennis Kopljar, Christina Schmitt, Elias Barbers,
CC BY 4.0, DOI 10.5281/zenodo.18986774.
Версии, ссылки на источники/декларации лицензий и контрольные суммы указаны в
tests/public_data/biologic.json и docs/biologic-test-data.md.
