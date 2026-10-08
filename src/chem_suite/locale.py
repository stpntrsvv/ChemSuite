"""Human-facing language only. Numerical fields and export column IDs stay stable."""
import re

_PAIRS = '''
Модуль|Module
Проект|Project
Нет доступных модулей|No modules available
Рабочая панель недоступна|Workspace panel unavailable
Ошибка отображения|Display failed
Результат сохранён; ошибка отображения|Result saved; display failed
Подготовка графиков Makie…|Preparing Makie plots…
Графики Makie готовы|Makie plots ready
Не удалось открыть Makie|Could not open Makie
Не удалось отобразить графики Makie. Результаты расчёта сохранены.|Could not display Makie plots. Analysis results are saved.
Makie остановлен.|Makie stopped.
Рабочий модуль|Workspace module
Циклирование|Cycling
Графики|Plots
Файл|File
Анализ|Analysis
Вид|View
Язык|Language
Открыть…|Open…
Папка…|Folder…
Рассчитать|Run analysis
Подбор схемы|Fit circuit
Автофит|Auto-fit
Рассчитать все|Run all
Экспорт…|Export…
Пакетный экспорт…|Batch export…
Отменить|Cancel
Журнал задач|Job history
Рабочая панель|Workspace
Исходные данные|Source data
Настройки анализа|Analysis settings
Выберите исходные данные|Select source data
Папка эксперимента|Experiment folder
Данные и расчёт|Data and analysis
Схема|Circuit
Канал|Channel
Канал импеданса (пусто — автоматически)|Impedance channel (automatic if empty)
Начальные приближения|Multistart count
Расширенные настройки|Advanced settings
Расширенный режим|Advanced mode
Журнал EIS|EIS log
Состояние|State
Статус|Status
Не рассчитан|Not fitted
Загружен|Loaded
Ошибка загрузки|Load failed
Точек|Points
Формат|Format
Ёмкость и эффективность|Capacity and efficiency
Номинальная ёмкость, mAh (необязательно)|Nominal capacity, mAh (optional)
Параметры|Parameters
Параметры схемы|Parameters
Параметр|Parameter
Значение|Value
Стандартная ошибка, 1σ|Standard error, 1σ
Относительная ошибка, %|Relative error, %
Ошибка, %|Relative error, %
Флаги|Flags
Сообщение|Message
Сводка|Summary
Диагностика|Diagnostics
Парсер|Parser
Прогресс|Progress
Боде|Bode
Модуль импеданса (АЧХ)|Bode magnitude
Фаза импеданса (ФЧХ)|Bode phase
Остатки подбора|Fit residuals
Относительные остатки|Relative residuals
График КК|KK Nyquist
Остатки КК|KK residuals
Найквист|Nyquist
Частота, Гц|Frequency, Hz
Фаза, °|Phase, °
Остаток, Ом|Residual, Ω
Остаток, %|Residual, %
Измерение|Measured
Модель|Model
Действительная часть|Real component
Мнимая часть|Imaginary component
Модуль импеданса|Magnitude
Готов к работе|Ready
Очистить|Clear
Убрать данные и результаты текущего модуля из окна. Файлы сохраняются.|Remove the current module's data and results from the window. Files are preserved.
В очереди|Queued
Расчёт|Running
Остановка|Stopping
Отмена расчёта|Cancelling calculation
Остановка по таймауту|Stopping after time limit
Не удалось запустить процесс|Could not start worker
Некорректный результат процесса|Invalid worker result
Ошибка публикации экспорта|Export publication failed
Не найдены поддерживаемые файлы спектров|No supported spectrum files found
Нет сошедшихся схем|No circuit converged
Нет готовых результатов для экспорта|No successful results to export
Julia не найдена. Укажите CHEM_SUITE_JULIA; установка описана в docs/makie.md|Julia was not found. Set CHEM_SUITE_JULIA; see docs/makie.md
Не удалось запустить Julia|Could not start Julia
Истёк лимит запуска Makie|Makie startup timed out
Процесс графиков Makie остановлен; расчёты модулей продолжаются|Makie stopped; module calculations continue
Готово|Completed
Ошибка|Failed
Отменено|Cancelled
Таймаут|Timed out
Отменено до запуска|Cancelled before start
Расчёт отменён|Calculation cancelled
Истёк лимит времени|Time limit exceeded
Запуск расчёта|Starting calculation
Ошибка расчёта|Calculation failed
Рабочий процесс аварийно завершился|Worker process crashed
Чтение спектра|Reading spectrum
Проверка Крамерса — Кронига|Checking Kramers–Kronig consistency
Сохранение результатов|Saving results
Чтение данных|Loading data
Откройте данные и запустите анализ.|Open data and run analysis.
Откройте спектр и запустите подбор схемы.|Open a spectrum and fit a circuit.
Откройте спектры через кнопку «Открыть…» в верхней панели.|Open spectra using the Open… button in the toolbar.
Автоматически|Automatic
Заряд / разряд|Charge / discharge
Ёмкость от начала шага, mAh|Capacity, mAh
Напряжение, V|Voltage, V
Эффективность|Efficiency
Цикл|Cycle
Эффективность, %|Efficiency, %
Ёмкость по циклам|Capacity by cycle
Ёмкость разряда, mAh|Discharge capacity, mAh
Разряд|Discharge
Циклы (первые 200)|Cycles (first 200)
Циклов|Cycles
Шагов|Steps
не задана|Not specified
Номинальная ёмкость, mAh|Nominal capacity, mAh
Состав экспорта|Export contents
Сводная таблица CSV|Summary CSV
Книга XLSX|XLSX workbook
Копии исходных файлов|Source file copies
Спектр и модель CSV|Spectrum and fitted model CSV
Параметры CSV|Parameters CSV
Кандидаты схем CSV|Model comparison CSV
Метаданные парсера CSV|Parser metadata CSV
Проверка КК CSV|KK results CSV
Результат JSON|Result JSON
Текстовый отчёт|Text report
Графики Найквиста|Nyquist plots
Графики Боде|Bode plots
Графики остатков|Residual plots
Графики КК|KK plots
Графики циклирования|Cycling plots
Метрики циклов CSV|Cycle metrics CSV
Метрики шагов CSV|Step metrics CSV
Паузы CSV|Rest metrics CSV
Форматы графиков|Plot formats
Результаты для экспорта|Results to export
Текущий результат|Current result
Все готовые результаты|All completed results
Папка для экспорта|Export folder
Выберите хотя бы один формат экспорта.|Select at least one export output.
Нет готовых результатов для экспорта.|No completed results to export.
Отчёт об анализе|Analysis report
Ошибки параметров — локальные стандартные ошибки (1σ), рассчитанные по ковариации подбора.|Parameter errors are local standard errors (1σ) from the fit covariance.
Стандартная ошибка оценки параметра (1σ) по ковариационной матрице подбора. Относительная ошибка = 100 × стандартная ошибка / модуль значения параметра.|Standard error of the parameter estimate (1σ), derived from the fit covariance. Relative error = 100 × standard error / absolute parameter value.
'''
RU, EN = {}, {}
for line in _PAIRS.strip().splitlines():
    ru, en = line.split('|', 1)
    RU[en], EN[ru] = ru, en
    RU[ru], EN[en] = ru, en
# Old payloads remain readable; machine keys are never changed by UI translation.
_ALIASES = {
    'ch': 'Charge', 'dch': 'Discharge',
    'Pro mode': 'Advanced mode', 'adaptive': 'Automatic', 'auto': 'Automatic',
    'Bode — модуль': 'Bode magnitude', 'Неопределённость': 'Standard error, 1σ',
    'circuit': 'Circuit', 'point_count': 'Points', 'mean_fit_error_percent': 'Fit error, %',
    'model_status': 'Model status', 'kk_status': 'KK status', 'selected_channel': 'Channel',
    'source_format': 'Format', 'cycle_count': 'Cycles', 'step_count': 'Steps',
    'capacity_mAh': 'Capacity, mAh', 'energy_mWh': 'Energy, mWh',
    'charge_mAh': 'Charge capacity, mAh', 'discharge_mAh': 'Discharge capacity, mAh',
    'coulombic_efficiency_percent': 'Coulombic efficiency, %',
    'voltage_efficiency_percent': 'Voltage efficiency, %', 'energy_efficiency_percent': 'Energy efficiency, %',
    'utilization_percent': 'Utilization, %', 'cycle': 'Cycle', 'step': 'Step',
    'charge_weighted_voltage_V': 'Charge-weighted voltage, V', 'source_index': 'Source step',
    'name': 'Parameter', 'value': 'Value', 'confidence': 'Standard error, 1σ',
    'relative_error_percent': 'Relative error, %', 'source_file': 'Source file',
}
for ru, en in [('Ошибка подбора, %', 'Fit error, %'), ('Статус модели', 'Model status'),
               ('Проверка КК', 'KK status'), ('Ёмкость, мА·ч', 'Capacity, mAh'),
               ('Энергия, мВт·ч', 'Energy, mWh'), ('Ёмкость заряда, мА·ч', 'Charge capacity, mAh'),
               ('Кулоновская эффективность, %', 'Coulombic efficiency, %'),
               ('Эффективность по напряжению, %', 'Voltage efficiency, %'),
               ('Энергетическая эффективность, %', 'Energy efficiency, %'), ('Использование ёмкости, %', 'Utilization, %'),
               ('Шаг', 'Step'), ('Среднее напряжение по заряду, В', 'Charge-weighted voltage, V'),
               ('Шаг исходных данных', 'Source step'), ('Исходный файл', 'Source file')]:
    RU[en], EN[ru] = ru, en

for ru, en in [("Поле", "Field"), ("Остатки", "Residuals"), ("Проверка КК", "KK results"),
               ("Показатель КК", "KK metric"), ("Максимальная ошибка, %", "Maximum error, %"),
               ("Число RC элементов", "RC elements"), ("Экспорт", "Export"),
               ("Спектры EIS", "EIS spectra"), ("Папка со спектрами EIS", "Open EIS folder"),
               ("Данные EIS (*.txt *.csv *.dat *.mpt *.mpr);;Все файлы (*)", "EIS data (*.txt *.csv *.dat *.mpt *.mpr);;All files (*)"),
               ("Исходные данные", "Source data"), ("Папка эксперимента", "Experiment folder"),
               ("Графики и параметры относятся к лучшей модели.", "Graphs and parameters show the best model.")]:
    RU[en], EN[ru] = ru, en


for ru, en in [("Заряд", "Charge"), ("Вписать график по данным", "Fit graph to data"),
               ("Перемещать график", "Pan graph"),
               ("Масштабировать прямоугольником", "Zoom by rectangle")]:
    RU[en], EN[ru] = ru, en

for ru, en in [
    ('Начальные значения и границы…', 'Parameter values and bounds…'),
    ('Начальные значения и границы', 'Parameter values and bounds'),
    ('Подготовить параметры', 'Load parameter defaults'), ('Единица', 'Unit'),
    ('Начальное значение', 'Initial value'), ('Нижняя граница', 'Lower bound'), ('Верхняя граница', 'Upper bound'),
    ('Применить', 'Apply'), ('Загрузите параметры выбранного спектра, затем измените значения.', 'Load defaults from the selected spectrum, then edit values.'),
    ('Изменения относятся к этой схеме. Настройки других схем сохраняются.', 'Changes apply to this circuit. Other circuits retain their settings.'),
    ('Настройки параметров сохранены', 'Parameter settings saved'),
    ('Значения параметров должны быть конечными', 'Parameter values must be finite'),
    ('Нижняя граница должна быть меньше верхней', 'Lower bound must be below upper bound'),
    ('Начальное значение должно находиться внутри границ', 'Initial value must be inside bounds'),
    ('Бюджет оптимизации', 'Optimization evaluations'), ('Seed генератора', 'Random seed'),
    ('Расчёт DRT', 'DRT analysis'), ('Распределение DRT', 'DRT distribution'), ('Постоянная времени, с', 'Time constant, s'),
    ('Пики DRT', 'DRT peaks'), ('Регуляризация DRT', 'DRT regularization'), ('Устойчивость пиков DRT', 'DRT peak stability'),
    ('Сетка регуляризации DRT', 'DRT regularization grid'), ('Число постоянных времени DRT', 'DRT time constants'),
    ('Число разбиений DRT', 'DRT validation folds'), ('Выборки устойчивости DRT (0 = выкл.)', 'DRT stability samples (0 = off)'),
    ('Выбор регуляризации DRT', 'Selecting DRT regularization'), ('Подбор DRT', 'Fitting DRT'),
    ('Проверка устойчивости пиков DRT', 'Checking DRT peak stability'), ('Пиков в диапазоне частот', 'drt_peak_count'),
    ('Ошибка DRT, %', 'drt_error_percent'), ('Регуляризация', 'regularization'), ('Анализ', 'analysis'),
    ('Пики DRT отражают релаксационные процессы и сами по себе не определяют химический механизм', 'DRT peaks are relaxation features, not automatically chemical mechanisms'),
    ('Регуляризация DRT достигла края сетки', 'DRT regularization reached the grid edge'),
    ('Постоянная времени, с', 'tau_seconds'), ('Частота, Гц', 'frequency_hz'), ('γ, Ом', 'gamma_ohm'),
    ('Выраженность пика, Ом', 'prominence_ohm'), ('В диапазоне измерений', 'inside_measured_frequency_band'),
    ('Ошибка на отложенных частотах', 'mean_heldout_relative_rmse'), ('Опорная частота, Гц', 'reference_frequency_hz'),
    ('Минимальная доля совпадений', 'worst_condition_match_fraction'), ('Медианная доля совпадений', 'median_condition_match_fraction'),
    ('Устойчивость ≥ 90%', 'stable_at_90_percent'), ('Да', 'True'), ('Нет', 'False'),
    ('Пакет SPICE…', 'SPICE package…'), ('Проверка пакета SPICE', 'Validating SPICE package'),
    ('Исполняемый файл ngspice (пусто — автоматически)', 'ngspice executable (empty = automatic)'),
    ('Проверенный пакет SPICE', 'Validated SPICE package'),
    ('Распределение DRT CSV', 'DRT distribution CSV'), ('Пики DRT CSV', 'DRT peaks CSV'),
    ('Регуляризация DRT CSV', 'DRT regularization CSV'), ('Устойчивость DRT CSV', 'DRT stability CSV'),
    ('График DRT', 'DRT plot'),
    ('Для SPICE нужен завершённый подбор схемы', 'SPICE requires a completed circuit fit'),
    ('Хеш сохранённого исходника не совпадает с его происхождением', 'Saved source hash does not match its provenance'),
    ('Сохранённые параметры не воспроизводят подобранный спектр', 'Saved circuit parameters do not reproduce the fitted spectrum'),
    ('Недопустимая сетка, число разбиений или выборок DRT', 'Invalid DRT grid, folds or sample count'),
    ('Регуляризация DRT должна быть конечной и неотрицательной', 'DRT regularization must be finite and non-negative'),
    ('Недостаточно точек спектра для проверки DRT', 'Not enough spectrum points for DRT validation'),
    ('Анализ не завершён успешно; SPICE-экспорт запрещён.', 'Analysis did not complete successfully; SPICE export refused.'),
]:
    RU[en], EN[ru] = ru, en

for key, label in {
    'analysis': 'Analysis', 'regularization': 'Regularization', 'drt_peak_count': 'Resolved peak count',
    'drt_error_percent': 'DRT error, %', 'tau_seconds': 'Time constant, s', 'frequency_hz': 'Frequency, Hz',
    'gamma_ohm': 'γ, Ω', 'prominence_ohm': 'Peak prominence, Ω',
    'inside_measured_frequency_band': 'Inside measured band', 'mean_heldout_relative_rmse': 'Held-out relative RMSE',
    'reference_frequency_hz': 'Reference frequency, Hz', 'worst_condition_match_fraction': 'Minimum match fraction',
    'median_condition_match_fraction': 'Median match fraction', 'stable_at_90_percent': 'Stability ≥ 90%',
}.items():
    ru = RU[key]
    _ALIASES[key] = label
    RU[label], EN[ru], EN[label] = ru, label, label
RU.update({'Ohm': 'Ω', 'sec': 'с', 'Ohm^-1 sec^a': 'Ом⁻¹·с^α'})
EN.update({'Ohm': 'Ω', 'sec': 's'})

_RESEARCH_PAIRS = '''
Недопустимое значение пресета|Invalid preset value
Недопустимое число выборок или точек сетки|Invalid diagnostic sample count or grid size
Недопустимый размах профиля|Invalid profile span
Недопустимый относительный уровень шума|Invalid relative noise fraction
Для импорта нужны спектр и полный JSON проверки|Import requires the spectrum and a full inference JSON
Импортированная проверка не содержит завершённых подборов|Imported inference does not contain completed fits
Победитель отсутствует среди кандидатов отчёта|Imported best circuit is missing from its candidates
Импортированные имена параметров не совпадают со схемой|Imported parameter names do not match their circuit
Пусто — адаптивный набор или выбранная схема|Empty = adaptive pool or selected circuit
Уровень доверия|Confidence level
Статусы выборок|Status counts
Взвешенная сумма квадратов остатков|Weighted RSS
Подбор сошёлся|Converged
В измеренном диапазоне|Supported
Минимальная измеренная частота, Гц|Minimum measured frequency, Hz
Максимальная измеренная частота, Гц|Maximum measured frequency, Hz
Проверка выбора схемы|Circuit reliability
Проверка параметров|Parameter diagnostics
Пользовательские пресеты…|User presets…
Пользовательские пресеты|User presets
Сохранённые пресеты|Saved presets
Название нового пресета|New preset name
Загрузить пресет|Load preset
Сохранить новый пресет|Save new preset
Обновить выбранный пресет|Update selected preset
Удалить пресет|Delete preset
Пресет загружен|Preset loaded
Список пресетов обновлён|Preset list updated
Пресет с таким названием уже существует|A preset with this name already exists
Название пресета должно содержать 1–128 символов|Preset name must contain 1–128 characters
Недопустимые настройки пресета|Invalid preset settings
Недопустимый формат пресета|Invalid preset schema
Некорректное хранилище пресетов|Invalid preset store
Слишком много пресетов|Too many presets
Выберите рассчитанную схему для просмотра её графиков и параметров.|Select a fitted circuit to inspect its graphs and parameters.
Схема для просмотра|Viewing circuit
Статистический победитель|Statistical winner
Экспорт использует победителя подбора.|Export uses the statistical winner.
Эта схема не сошлась; подобранная кривая отсутствует|This circuit did not converge; there is no fitted curve to display
Повторите подбор спектра для просмотра кандидатов|Refit this spectrum to enable candidate views
Схемы-кандидаты (через точку с запятой)|Candidate circuits (semicolon separated)
Число начальных приближений проверки выбора|Reliability multistart count
Выборки проверки выбора схемы|Circuit selection resamples
Выборки DRT для проверки выбора|Reliability DRT resamples
Импорт расширенной проверки…|Import reliable result…
Импорт расширенной проверки|Import reliable result
Метод повторной выборки параметров|Parameter bootstrap method
Повторные выборки параметров|Parameter bootstrap resamples
Относительный уровень шума|Relative noise fraction
Параметр профиля (пусто — выкл.)|Profile parameter (empty = off)
Точек сетки профиля|Profile grid points
Размах профиля, декады|Profile span, decades
Проверка частотного окна|Frequency window check
Выборка остатков|residual
Параметрическая выборка|parametric
Включена|on
Выключена|off
Проверка устойчивости выбора схемы|Checking circuit selection stability
Проверка интервалов параметров|Checking parameter intervals
Расчёт профиля правдоподобия|Calculating profile likelihood
Проверка устойчивости к частотному окну|Checking frequency window stability
Профиль правдоподобия|Profile likelihood
Профиль|Profile
Порог 95%|95% threshold
Интервалы параметров|Parameter intervals
Сводка повторных выборок|Bootstrap summary
Сводка профиля|Profile summary
Точки профиля|Profile points
Устойчивость к частотному окну|Window stability
Частотные окна|Frequency windows
Характерные частоты в диапазоне измерений|Characteristic frequency support
Устойчивость выбора схемы|Circuit selection stability
Устойчивость выбора семейства|Family stability
Вердикт|Verdict
Рекомендованное семейство|Recommended family
Рекомендованная топология|Recommended topology
Валидность данных|Data validity
Причина|Reason
Следующее действие|Next action
Проверка наличия диффузионного семейства|Positive diffusion gate
Проверено|Evaluated
Пройдено|Passed
Пороги|Thresholds
Эта проверка подтверждает только наличие семейства; она не доказывает отсутствие диффузии и не выбирает W/Wo/Ws.|This gate supports presence only; it does not prove absence of diffusion or select W/Wo/Ws.
Рекомендована схема|recommended
Схемы неразличимы|models_indistinguishable
Недостаточно информации|insufficient_information
Ошибка анализа|analysis_failed
Рекомендация неустойчива|unstable
Поддерживается данными|supported
Не проверено|not_evaluated
Идеальная RC-схема|ideal_rc
Межфазное семейство|interface
Диффузионное семейство|diffusion
Индуктивное семейство|inductive
Индуктивное семейство с диффузией|inductive_diffusion
Семейство не классифицировано|unclassified
Проверка Крамерса — Кронига не пройдена|Kramers-Kronig validation failed
Проверка устойчивости выбора схемы не запускалась|reliable topology diagnostics were not run
Запустите проверку выбора схемы|run reliable mode
Топология прошла проверку устойчивости повторными выборками|topology passed bootstrap stability gate
Топология устойчива, но ни один диапазон времени DRT не прошёл проверку устойчивости|topology is bootstrap-stable but no DRT time region passed the stability gate
Устойчивый победитель повторных выборок|stable bootstrap winner
Выбор топологии неустойчив|candidate topologies are not selection-stable
Для проверки выбора нужны хотя бы две схемы-кандидата|topology stability needs at least two candidate circuits
Проверка наличия диффузионного семейства не пройдена|calibrated positive diffusion gate not passed
Семейство ECM прошло откалиброванные проверки повторных выборок и BIC; граничное условие ещё не откалибровано|diffusion ECM family passed calibrated bootstrap and BIC gates; boundary condition remains uncalibrated
Семейство ECM устойчиво; выбор точной топологии неустойчив|ECM family is bootstrap-stable; exact topology is selection-unstable
Соберите независимые или повторные измерения|collect independent or repeated measurements
Проведите или расширьте проверку разрешимости|run or extend targeted resolution diagnostics
Проверьте стационарность измерения, подключение и артефакты|check measurement stationarity, wiring and artifacts
Интервалы условны относительно схемы и модели шума; они не доказывают физический механизм|Intervals are conditional on the circuit and noise model; they do not establish a physical mechanism
Менее 30 выборок: предварительная оценка устойчивости|Fewer than 30 resamples: exploratory stability estimate
Менее 30 выборок: предварительная оценка интервала|Fewer than 30 resamples: exploratory interval estimate
Интервал профиля достиг края сетки; границы интервала определены не полностью|Profile interval reached the grid edge; the interval is incomplete
Ни один подбор повторной выборки не принят; интервалы параметров недоступны|No bootstrap fits were accepted; parameter intervals are unavailable
Условная устойчивость относительно выбранной порождающей модели, а не вероятность истинности схемы|conditional stability around the selected generating model, not posterior model probability
Импортированные свидетельства: соответствие исходника и подбора проверено; диагностика не пересчитывалась|Imported evidence; source and fit consistency were checked, diagnostics were not recalculated
Нужен полный отчёт расширенной проверки|A full reliable inference report is required
Импортированный отчёт относится к другому спектру|Imported report belongs to another spectrum
Хеш исходника в отчёте не совпадает с этим спектром|Imported report source hash does not match this spectrum
Канал или число точек отчёта не совпадает с этим спектром|Imported report channel or point count does not match this spectrum
Статус КК отчёта не совпадает с этим спектром|Imported report KK status does not match this spectrum
Импортированные параметры не воспроизводят ошибку подбора из отчёта|Imported parameters do not reproduce the reported fit error
Расширенная проверка JSON|Reliability JSON
Таблицы проверки выбора|Circuit reliability tables
Таблицы интервалов и устойчивости|Parameter diagnostic tables
График профиля|Profile plot
Схема|Circuit
Семейство|Family
Побед|Wins
Доля принятых выборок|Fraction of accepted samples
Метод|Method
Запрошено выборок|Requested samples
Принято выборок|Accepted samples
Доля принятых|Acceptance fraction
Исходная оценка|Base estimate
Медиана|Median
Нижняя граница 95%|95% lower bound
Верхняя граница 95%|95% upper bound
Интервал достиг края сетки|Interval reached grid edge
Окно|Window
Оценка принята|Accepted
Устойчив|Stable
Максимальное изменение, раз|Maximum fold change
Принятых окон|Accepted windows
Запрошенных окон|Requested windows
'''
for _pair in _RESEARCH_PAIRS.strip().splitlines():
    _ru, _en = _pair.split('|', 1)
    RU[_en], EN[_ru] = _ru, _en

_ALIASES.update({'verdict': 'Verdict', 'recommended_topology': 'Recommended topology', 'recommended_family': 'Recommended family', 'family': 'Family', 'wins': 'Wins', 'fraction_of_accepted': 'Fraction of accepted samples', 'requested': 'Requested samples', 'accepted': 'Accepted samples', 'acceptance_fraction': 'Acceptance fraction', 'method': 'Method', 'base': 'Base estimate', 'median': 'Median', 'ci95_low': '95% lower bound', 'ci95_high': '95% upper bound', 'interval_hits_grid_edge': 'Interval reached grid edge', 'window': 'Window', 'stable': 'Stable', 'max_fold_change': 'Maximum fold change', 'accepted_windows': 'Accepted windows', 'requested_windows': 'Requested windows', 'bootstrap_method': 'Method', 'bootstrap_requested': 'Requested samples', 'bootstrap_accepted': 'Accepted samples', 'confidence_level': 'Confidence level', 'supported': 'Supported', 'parameters': 'Parameters', 'frequency': 'Frequency, Hz', 'measured_min': 'Minimum measured frequency, Hz', 'measured_max': 'Maximum measured frequency, Hz', 'status_counts': 'Status counts', 'weighted_rss': 'Weighted RSS', 'success': 'Converged', 'noise_fraction': 'Relative noise fraction', 'points': 'Points', 'value': 'Value', 'unit': 'Unit'})

for _code, _label in {
    'recommended': 'Circuit recommended', 'models_indistinguishable': 'Models indistinguishable',
    'insufficient_information': 'Insufficient information', 'analysis_failed': 'Analysis failed',
    'ideal_rc': 'Ideal RC', 'interface': 'Interface', 'diffusion': 'Diffusion', 'inductive': 'Inductive',
    'inductive_diffusion': 'Inductive diffusion', 'unclassified': 'Unclassified', 'unstable': 'Unstable',
    'not_evaluated': 'Not evaluated', 'residual': 'Residual resampling', 'parametric': 'Parametric resampling',
    'on': 'Enabled', 'off': 'Disabled',
}.items():
    _ru = RU[_code]
    _ALIASES[_code] = _label
    RU[_label], EN[_ru] = _ru, _label
for _code, _ru, _en in [
    ('centered_complex_residual_bootstrap', 'Повторные выборки центрированных комплексных остатков', 'Centered complex residual bootstrap'),
    ('relative_complex_parametric_bootstrap', 'Параметрические выборки относительного комплексного шума', 'Relative complex parametric bootstrap'),
    ('weighted_profile_likelihood', 'Взвешенный профиль правдоподобия', 'Weighted profile likelihood'),
    ('conditional_residual_topology_bootstrap', 'Условные повторные выборки выбора топологии', 'Conditional residual topology bootstrap'),
]:
    _ALIASES[_code] = _en
    RU[_en], EN[_ru] = _ru, _en


def text(value, language='ru'):
    value = str(value)
    if match := re.fullmatch(r'(incomplete_cycle|negative_voltage|electrode_potential|zero_capacity):(\d+)(?::.*)?', value):
        messages = {
            'incomplete_cycle': ('Цикл {n}: записан только заряд или разряд; CE/VE/EE не рассчитаны', 'Cycle {n}: only charge or discharge recorded; CE/VE/EE unavailable'),
            'negative_voltage': ('Цикл {n}: отрицательное напряжение; проверьте канал и полярность, VE/EE не рассчитаны', 'Cycle {n}: negative voltage; check channel and polarity, VE/EE unavailable'),
            'electrode_potential': ('Цикл {n}: выбран потенциал электрода; для VE/EE нужно напряжение ячейки', 'Cycle {n}: electrode potential selected; VE/EE require cell voltage'),
            'zero_capacity': ('Цикл {n}: нулевая измеренная ёмкость; проверьте длительность шага', 'Cycle {n}: zero measured capacity; check step duration'),
        }
        return messages[match[1]][language == 'en'].format(n=match[2])
    if match := re.fullmatch(r'unintegrated_boundary_s:(.*):([^:]+)', value):
        return (f'Между шагами перед {match[1]}: {match[2]} с без интегрирования; нет измеренных границ' if language == 'ru' else
                f'Before step {match[1]}: {match[2]} s excluded from integration; no recorded boundary samples')
    if value.startswith('cycle_counter_offset:'):
        return ('Сброс счётчика между файлами: номера циклов разделены' if language == 'ru' else
                'Cycle counter reset between files: cycle IDs kept separate')
    value = _ALIASES.get(value, value)
    lookup = EN if language == 'en' else RU
    if value in lookup:
        return lookup[value]
    if match := re.fullmatch(r'drop_(low|high)_([0-9.]+)', value):
        percent = float(match[2]) * 100
        edge = ('нижних' if match[1] == 'low' else 'верхних') if language == 'ru' else ('lowest' if match[1] == 'low' else 'highest')
        return f'Без {edge} {percent:g}% частот' if language == 'ru' else f'Drop {edge} {percent:g}% frequencies'
    if match := re.fullmatch(r'(?:Цикл|Cycle) (\d+) · (.*)', value):
        prefix = 'Цикл' if language == 'ru' else 'Cycle'
        return f'{prefix} {match[1]} · {text(match[2], language)}'
    if match := re.fullmatch(r'Batch (\d+)/(\d+): (.*)', value):
        prefix = 'Пакет' if language == 'ru' else 'Batch'
        return f'{prefix} {match[1]}/{match[2]}: {text(match[3], language)}'
    if ' · ' in value:
        return ' · '.join(text(part, language) for part in value.split(' · '))
    if '\n' in value:
        return '\n'.join(text(line, language) for line in value.split('\n'))
    if ': ' in value:
        key, rest = value.split(': ', 1)
        if re.fullmatch(r'[A-Za-z_]\w*(?:Error|Refused)', key):
            return key + ': ' + text(rest, language)
        if key in _ALIASES or key in lookup:
            return text(key, language) + ': ' + text(rest, language)
    for pattern, ru_prefix, en_prefix in [
        (r'^Ни один инженерный порядок не прошёл ворота: (.*)$', 'Ни один порядок инженерной модели не прошёл проверку: ', 'No engineering order passed validation: '),
        (r'^Проверка Крамерса — Кронига имеет статус (.*); экспорт запрещён\.$', 'Статус проверки Крамерса — Кронига: ', 'Kramers–Kronig status: '),
        (r'^Статус научной модели (.*) не допускает экспорт\.$', 'Экспорт запрещён для статуса модели ', 'Export refused for scientific model status '),
    ]:
        if match := re.fullmatch(pattern, value):
            return (en_prefix if language == 'en' else ru_prefix) + match[1]
    if language == 'ru':
        substitutions = [
            (r'^Noise (.*)$', r'Шум \1'),
            (r'^Resolution map (.*)$', r'Карта разрешимости \1'),
            (r'^Session saved to (.*)$', r'Сессия сохранена: \1'),
            (r'^Loading spectrum (.*)$', r'Загрузка спектра \1'),
            (r'^Exporting result (.*)$', r'Экспорт результата \1'),
            (r'^Exported to (.*)$', r'Экспорт завершён: \1'),
            (r'^Loaded (\d+) spectra; (\d+) failed$', r'Загружено спектров: \1; ошибок: \2'),
            (r'^Batch completed: (\d+) succeeded, (\d+) failed$', r'Пакет завершён: успешно \1, ошибок \2'),
            (r'^Batch (\d+)/(\d+): (.*)$', r'Пакет \1/\2: \3'),
        ]
    else:
        substitutions = [(r'^Чтение (.*)$', r'Reading \1'), (r'^Цикл (\d+) · (.*)$', r'Cycle \1 · \2'),
                         (r'^Циклирование: шаг (.*)$', r'Cycling: step \1')]
    for pattern, replacement in substitutions:
        if re.match(pattern, value):
            return re.sub(pattern, replacement, value)
    return value

_COMPLETION_PAIRS = """
Анализ серии|Series analysis
Совместный подбор по SOC|Joint SOC fit
Синтетическая карта разрешимости|Synthetic resolution map
Синтетическое исследование|Synthetic study
Манифест серии SOC (CSV)|SOC series manifest (CSV)
Выбрать манифест SOC…|Choose SOC manifest…
Сглаживание совместного подбора|Joint smoothness
Сетка проверки сглаживания (пусто — выключено)|Smoothness validation grid (empty = off)
Число проверочных окон SOC|SOC validation folds
Сетка нижних частот, Гц|Minimum frequency grid, Hz
Сетка относительного шума|Noise fraction grid
Верхняя частота исследования, Гц|Maximum study frequency, Hz
Повторов в каждой ячейке|Study replicates per cell
Точек в синтетическом спектре|Study spectrum points
Пакет C для контроллера…|Controller C package…
Проверенный пакет C для контроллера|Validated controller C package
Период дискретизации контроллера, с|Controller sample period, s
Полный диапазон тока контроллера, А|Controller current full scale, A
Верхняя частота контроллера, Гц (пусто — автоматически)|Controller maximum frequency, Hz (empty = automatic)
Проверка пакета для контроллера|Validating controller package
Сохранить сессию…|Save session…
Открыть сессию…|Open session…
Открыть сохранённый результат…|Open saved result…
Папка сессии|Session folder
Сохранение сессии|Saving session
Открытие сохранённого анализа|Opening saved analysis
Сессия восстановлена|Session restored
Сохранённый результат открыт|Saved result opened
Сохранение результатов сессии|Saving scientific result bundles
Проверка контрольных сумм сессии|Verifying session checksums
Сводка серии|Series summary
Параметры серии|Series parameters
Спектры серии|Series spectra
Независимые подборы|Independent fits
Проверка сглаживания|Smoothness validation
Ячейки карты разрешимости|Resolution cells
Рекомендации измерения|Measurement recommendations
Графики исследования|Study plots
Сглаженная траектория|Smoothed trajectory
Нижняя частота, Гц|Minimum frequency, Hz
Доля обнаружений медленного пика|Slow peak detection fraction
Чтение готовых подборов серии|Reading completed series fits
Выбор сглаживания по отложенным спектрам SOC|Selecting smoothness with held-out SOC spectra
Совместный подбор схемы по серии SOC|Fitting shared circuit across the SOC series
Укажите настройки исследования и запустите расчёт. Совместный подбор требует явного манифеста SOC.|Choose study settings and run the analysis. Joint fitting requires an explicit SOC manifest.
Совместное сглаживание не доказывает определимость параметров или надёжность схемы|Joint smoothing does not establish parameter identifiability or circuit reliability
Синтетический эталон с двумя RC-звеньями; эти рекомендации не являются калибровкой вашего образца|Synthetic two-RC reference; these recommendations are not a calibration of your sample
Метаданные серии получены из имён файлов; для своей схемы именования используйте явный манифест SOC|Series metadata inferred from filenames; use an explicit SOC manifest for your own naming scheme
"""
for _pair in _COMPLETION_PAIRS.strip().splitlines():
    _ru, _en = _pair.split('|', 1)
    RU[_en], EN[_ru] = _ru, _en
_ALIASES.update({'soc': 'SOC', 'soc_values': 'SOC', 'min_frequency_hz': 'Minimum frequency, Hz',
    'slow_peak_detection_fraction': 'Slow peak detection fraction', 'replicates': 'Study replicates per cell',
    'noise_fraction': 'Relative noise fraction', 'spectrum_count': 'Points', 'source_count': 'Source count',
    'cell_count': 'Resolution cells', 'smoothness': 'Joint smoothness', 'channel': 'Channel', 'file': 'File'})

RU.update({'Topology evidence': 'Свидетельства выбора схемы', 'Source count': 'Число источников', 'Spectrum count': 'Число спектров', 'Declared series': 'Заявленная серия'})
EN.update({value: key for key, value in [('Topology evidence', 'Свидетельства выбора схемы'), ('Source count', 'Число источников'), ('Spectrum count', 'Число спектров'), ('Declared series', 'Заявленная серия')]})
_ALIASES.update({'declared-series': 'Declared series', 'spectrum_count': 'Spectrum count'})

RU["Research result"] = "Результат исследования"
EN["Результат исследования"] = "Research result"

_CYCLING_PAIRS = """
Эксперименты|Experiments
Эксперимент|Experiment
Объединить файлы опыта…|Combine experiment files…
Убрать из списка|Remove from list
Канал напряжения BioLogic|BioLogic voltage channel
Канал напряжения|Voltage channel
Тип напряжения|Voltage type
Порог тока паузы, A|Rest current threshold, A
Определение циклов|Cycle definition
Счётчик между файлами|Counter across files
Опорный цикл (пусто — первая пара)|Reference cycle (empty = first pair)
Опорный цикл|Reference cycle
Циклы на графиках (например: 0, 1, 5-10)|Cycles to plot (e.g. 0, 1, 5-10)
Страница таблиц (200 строк)|Table page (200 rows)
Циклы для экспорта|Cycles to export
Показать выбранные циклы|Show selected cycles
Показаны циклы|Cycles shown
На графиках показано шагов|Steps shown in plots
Полные данные доступны в экспорте|Full data available in export
Страница|Page
Графики обновлены|Plots updated
Циклы|Cycles
Шаги|Steps
Паузы|Rests
Пауз|Rests
Пар заряд–разряд|Charge/discharge pairs
Определяется прибором или по переходам тока|Instrument counter or current transitions
Напряжение ячейки|Cell voltage
Потенциал электрода|Electrode potential
По направлению тока|By current direction
Разделять при сбросе счётчика|Separate reset counters
Продолжать одинаковые номера|Continue matching counters
Все циклы|All cycles
Выбранные циклы|Selected cycles
Не рассчитан|Not analyzed
Ёмкость от начала полупериода, mAh|Capacity from half-cycle start, mAh
Сохранение ёмкости|Capacity retention
Сохранение ёмкости, %|Capacity retention, %
Напряжение в конце паузы|Voltage at end of rest
Напряжение во времени|Voltage vs time
Ток во времени|Current vs time
Время записи, s|Recording time, s
Ток, mA|Current, mA
Пауза|Rest
Измерения и кривые CSV|Measurements and curves CSV
Время файла; разные файлы соединены по длительности, без неизвестных пауз|File time; files concatenated by duration, unknown gaps excluded
Шаги соединены по измеренной длительности, без неизвестных пауз|Steps concatenated by recorded duration, unknown gaps excluded
Данные (*.mpt *.mpr *.csv *.txt);;Все файлы (*)|Data (*.mpt *.mpr *.csv *.txt);;All files (*)
Ewe может быть потенциалом электрода. Для VE/EE выберите напряжение ячейки.|Ewe can be an electrode potential. Select cell voltage for VE/EE.
Объединять одинаковые номера можно только для продолжения одного опыта. Порядок файлов сохраняется.|Only join matching counters for a continuation of the same experiment. File order is preserved.
Пустой выбор: до 10 циклов на графике; экспорт — все циклы. Время разных файлов соединено по длительности записи.|Empty selection: up to 10 cycles plotted; all cycles exported. File timelines are concatenated by recorded duration.
Секунды|Seconds
Длительность, с|Duration, s
Начальное напряжение, V|Initial voltage, V
Изменение напряжения, V|Voltage change, V
Напряжение паузы, V|Rest voltage, V
Начало записи, с|Recording start, s
Конец записи, с|Recording end, s
Сохранение ёмкости, %|Capacity retention, %
Номер цикла прибора|Instrument cycle
Идентификатор шага|Step ID
Есть заряд и разряд|Charge and discharge recorded
Энергия заряда, мВт·ч|Charge energy, mWh
Энергия разряда, мВт·ч|Discharge energy, mWh
Длительность заряда, с|Charge duration, s
Длительность разряда, с|Discharge duration, s
Напряжение пригодно для VE/EE|Voltage valid for VE/EE
"""
for _pair in _CYCLING_PAIRS.strip().splitlines():
    _ru, _en = _pair.split('|', 1)
    RU[_en], EN[_ru] = _ru, _en
_ALIASES.update({'cell': 'Cell voltage', 'electrode': 'Electrode potential', 'direction': 'By current direction',
    'separate': 'Separate reset counters', 'continue': 'Continue matching counters', 'all': 'All cycles', 'selected': 'Selected cycles',
    'rest': 'Rest', 'Not analyzed': 'Not analyzed', 'duration_s': 'Duration, s', 'ocv_V': 'Rest voltage, V',
    'initial_voltage_V': 'Initial voltage, V', 'voltage_change_V': 'Voltage change, V', 'elapsed_start_s': 'Recording start, s',
    'elapsed_end_s': 'Recording end, s', 'instrument_cycle': 'Instrument cycle', 'step_id': 'Step ID', 'paired': 'Charge and discharge recorded',
    'charge_energy_mWh': 'Charge energy, mWh', 'discharge_energy_mWh': 'Discharge energy, mWh',
    'charge_duration_s': 'Charge duration, s', 'discharge_duration_s': 'Discharge duration, s', 'retention_percent': 'Capacity retention, %',
    'voltage_valid_for_efficiency': 'Voltage valid for VE/EE', 'voltage_channel': 'Voltage channel',
    'file_time_then_concatenated_files': 'File time; files concatenated by duration, unknown gaps excluded',
    'concatenated_step_durations': 'Steps concatenated by recorded duration, unknown gaps excluded'})
RU['Not analyzed'] = 'Не рассчитан'

RU["BioLogic current channel"] = "Канал тока BioLogic"
EN["Канал тока BioLogic"] = "BioLogic current channel"
RU["Current channel"] = "Канал тока"
_ALIASES["current_channel"] = "Current channel"

RU['View and export'] = 'Просмотр и экспорт'
EN['Просмотр и экспорт'] = 'View and export'
_CYCLING_ERRORS = """
Некоторые выбранные циклы отсутствуют в опыте|Some selected cycles are absent from this experiment
Неверный диапазон циклов|Invalid cycle range
Укажите циклы в формате 0, 1, 5-10|Choose cycles as 0, 1, 5-10
Для превью выберите не более 50 циклов; экспорт включает все циклы|Select up to 50 cycles for the interactive preview; export includes all cycles
Нет исходных файлов циклирования|No raw cycling files found
Нет опытов циклирования|No cycling experiments found
Неверная сигнатура MPT BioLogic|Invalid BioLogic MPT signature
Неверное число строк заголовка MPT BioLogic|Invalid BioLogic Nb header lines
Обрезанный заголовок MPT BioLogic|Truncated BioLogic MPT header
Повторяющиеся названия колонок MPT BioLogic|Duplicate BioLogic MPT column names
BioLogic: в записи нет измерений циклирования|BioLogic recording has no cycling measurements
BioLogic: это импеданс; откройте файл в EIS|BioLogic impedance data is not a cycling recording; open it in EIS
BioLogic: требуется колонка time/s|BioLogic cycling requires time/s
BioLogic: нечисловое или бесконечное измерение|BioLogic contains a nonfinite measurement
BioLogic: счётчик сброшен внутри файла; выберите циклы по направлению тока|BioLogic cycle number resets inside a file; choose current-direction cycle definition
Порог тока паузы должен быть конечным и неотрицательным|Rest threshold must be finite and nonnegative
Номинальная ёмкость должна быть конечной и положительной|Nominal capacity must be finite and positive
Опорный цикл должен быть целым неотрицательным числом|Reference cycle must be a nonnegative integer
У опорного цикла нет измеренной ёмкости разряда|Reference cycle has no measured discharge capacity
Концентрация, объём и число электронов должны быть конечными и положительными|Electrolyte concentration, volume and electron count must be finite and positive
Поддерживаются MPT, MPR, CSV и TXT YARST/Elins|Supported cycling files: MPT, MPR, CSV, YARST/Elins TXT
Для MPR нужен chem-suite[biologic]; можно экспортировать MPT из EC-Lab|BioLogic MPR requires chem-suite[biologic]; export MPT from EC-Lab as an alternative
"""
for _pair in _CYCLING_ERRORS.strip().splitlines():
    _ru, _en = _pair.rsplit('|', 1)
    RU[_en], EN[_ru] = _ru, _en

RU['BioLogic cycling requires signed I/A, <I>/A, I/mA or <I>/mA (not |I|)'] = 'BioLogic: нужен знаковый ток I/A, <I>/A, I/mA или <I>/mA; модуль |I| не подходит'
RU[
    'BioLogic cycling requires signed I/A, <I>/A, I/mA or <I>/mA (not |I|); '
    'if missing in MPR, export this recording as MPT from EC-Lab/BT-Lab'
] = (
    'BioLogic: нужен знаковый ток I/A, <I>/A, I/mA или <I>/mA; модуль |I| не подходит. '
    'Если ток отсутствует в MPR, экспортируйте запись в MPT из EC-Lab/BT-Lab'
)

RU['Rest before cycling'] = 'Пауза до циклирования'
EN['Пауза до циклирования'] = 'Rest before cycling'

RU['Yes'], RU['No'] = 'Да', 'Нет'
EN['Да'], EN['Нет'] = 'Yes', 'No'

_UPDATES = """
Обновление программы|Software update
Проверить обновления…|Check for updates…
Автоматически проверять обновления|Automatically check for updates
Доступна новая версия|New version available
Страница релиза|Release page
Открыть страницу релиза|Open release page
Позже|Later
Отменить скачивание|Cancel download
Скачать обновление|Download update
Установить обновление|Install update
Обновление скачивается…|Downloading update…
Проверяем скачанное обновление…|Verifying downloaded update…
Обновление готово. Завершите текущий расчёт перед установкой.|The update is ready. Finish the current analysis before installing.
Обновление готово. Сохраните сессию перед перезапуском.|The update is ready. Save your session before restarting.
Ваши данные и результаты анализа остаются в своих папках.|Your data and analysis results stay in their current folders.
Установите готовое приложение, чтобы получать обновления здесь.|Install the packaged application to receive updates here.
Релизы ещё не опубликованы.|No releases have been published yet.
У вас установлена последняя версия.|You are using the latest version.
Для этой платформы нет пакета обновления.|No update package is available for this platform.
Не удалось проверить обновления|Update check failed
Не удалось обновить программу|Update failed
Превышен лимит запросов GitHub. Попробуйте позже.|GitHub request limit reached
Сервер обновлений не ответил вовремя.|Update request timed out
Файл обновления не прошёл проверку целостности.|Update download failed integrity verification
Не удалось открыть установщик обновления.|Could not open the update installer
Не удалось подключиться к серверу обновлений|Update connection failed
Некорректный адрес обновления|Invalid update URL
Некорректное перенаправление загрузки|Invalid update redirect
Слишком большой ответ сервера обновлений|Update metadata is too large
Некорректная версия релиза|Invalid release version
Релиз не является стабильной опубликованной версией|Not a stable published release
Тег релиза должен начинаться с v|Release tags must start with v
Некорректный список файлов релиза|Invalid release assets
Файл релиза отсутствует или указан несколько раз|Release asset is missing or ambiguous
Некорректный размер или статус файла релиза|Invalid release asset size or state
Некорректный адрес файла релиза|Invalid release asset URL
Некорректная контрольная сумма файла релиза|Invalid release asset checksum
Некорректная контрольная сумма обновления|Invalid update checksum
Слишком большой манифест обновления|Update manifest is too large
Размер манифеста не совпадает с данными релиза|Update manifest size differs from release metadata
Контрольная сумма манифеста не совпадает с данными релиза|Update manifest checksum differs from release metadata
Некорректный манифест обновления|Invalid update manifest
Файл обновления не совпадает с данными релиза|Update artifact differs from release metadata
Контрольная сумма обновления не совпадает с данными релиза|Update checksum differs from release metadata
Платформа не поддерживается обновлением|Unsupported update platform
Скачанный файл превышает ожидаемый размер|Update download exceeded its expected size
Скачивание не выполняется|Download is not active
Скачивание отменено|Download was cancelled
Сейчас откроется установщик, а Chem Suite закроется. Сначала сохраните сессию.|The installer will open and Chem Suite will close. Save your session first.
Сейчас откроется образ диска, а Chem Suite закроется. Перетащите Chem Suite в «Программы» и подтвердите замену. Сначала сохраните сессию.|The disk image will open and Chem Suite will close. Drag Chem Suite to Applications and confirm replacement. Save your session first.
"""
for _pair in _UPDATES.strip().splitlines():
    _ru, _en = _pair.rsplit('|', 1)
    RU[_en], EN[_ru] = _ru, _en
