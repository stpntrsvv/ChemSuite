"""Export layout regressions using synthetic curves, never private measurements."""

import csv
import json
from pathlib import Path

import pytest

from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from chem_suite.core.plots import plot, series
from chem_suite.desktop.app import create_application
from chem_suite.desktop.app import MainWindow
from chem_suite.desktop.export_dialog import ExportDialog
from chem_suite.exports.plots import plot_figure, render_plots
from tests.test_isolation import wait
from tests.test_science import Context


def cycling_specs(count=40):
    return [
        plot('Заряд / разряд', 'Ёмкость, mAh', 'Напряжение, V', [
            series(f'Cycle {i // 2 + 1} · {"ch" if i % 2 == 0 else "dch"} · Step: 1:{i + 1}',
                   [0, 1, 2], [1 + i / 100, 2, 3 - i / 100])
            for i in range(count)
        ]),
        plot('Эффективность', 'Цикл', 'Эффективность, %', [series('CE', [1, 2], [80, 85])]),
        plot('Ёмкость по циклам', 'Цикл', 'Ёмкость, mAh', [series('Заряд', [1, 2], [10, 9])]),
    ]


@pytest.mark.parametrize('count', [40, 200])
@pytest.mark.parametrize('language', ['ru', 'en'])
def test_external_legend_never_overlaps_any_data_axis_or_gets_clipped(count, language):
    specs = cycling_specs(count)
    figure = plot_figure(specs, language, Context(), legend='outside')
    try:
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        data = [a for a in figure.axes if a.axison]
        legends = [a.get_legend() for a in figure.axes if not a.axison]
        assert [len(a.lines) for a in data] == [count, 1, 1]
        assert [len(artist.get_texts()) for artist in legends] == [count, 1, 1]
        for legend in legends:
            box = legend.get_window_extent(renderer)
            assert figure.bbox.contains(box.x0, box.y0)
            assert figure.bbox.contains(box.x1, box.y1)
            assert all(not box.overlaps(axis.get_tightbbox(renderer)) for axis in data)
    finally:
        figure.clear()


@pytest.mark.parametrize('mode', ['inside', 'hidden'])
def test_hiding_legend_preserves_every_curve_and_coordinate(mode):
    specs = cycling_specs()
    figure = plot_figure(specs, 'en', Context(), legend=mode)
    try:
        assert len(figure.axes) == 3
        assert len(figure.axes[0].lines) == 40
        for line, curve in zip(figure.axes[0].lines, specs[0]['series'], strict=True):
            assert list(line.get_xdata()) == curve['x']
            assert list(line.get_ydata()) == curve['y']
        assert all((axis.get_legend() is None) == (mode == 'hidden') for axis in figure.axes)
    finally:
        figure.clear()


@pytest.mark.filterwarnings('error:constrained_layout not applied')
@pytest.mark.parametrize('mode', ['outside', 'inside', 'hidden'])
def test_all_plot_formats_render_with_selected_legend(tmp_path, mode):
    render_plots(tmp_path, {'cycling': cycling_specs()}, ['png', 'svg', 'pdf'], 'ru', Context(), legend=mode)
    for suffix in ['png', 'svg', 'pdf']:
        assert (tmp_path / f'cycling.{suffix}').stat().st_size > 1000


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_dialog_defaults_and_legend_choice_use_stable_tokens(language):
    create_application()
    cycling = ExportDialog(None, language, 'cycling', 1)
    eis = ExportDialog(None, language, 'eis', 1)
    try:
        assert cycling.plot_legend() == 'outside'
        assert eis.plot_legend() == 'inside'
        cycling.legend_mode.setCurrentIndex(cycling.legend_mode.findData('hidden'))
        assert cycling.plot_legend() == 'hidden'
        assert cycling.legend_mode.currentText() == ('Без легенды' if language == 'ru' else 'Hide legend')
    finally:
        cycling.close()
        eis.close()


def test_batch_export_carries_legend_choice_without_filtering_measurements(tmp_path):
    source = Path(__file__).parents[1] / 'examples/cycling.csv'
    with source.open() as f:
        expected = list(csv.DictReader(f))
    with JobService(builtins(), tmp_path / 'workspace') as jobs:
        analysis = jobs.submit(JobRequest('cycling', 'analyze', (str(source),)))
        wait(jobs, lambda: analysis.state.terminal, 30)
        assert analysis.state == JobState.SUCCEEDED, analysis.error
        for mode in ['auto', 'hidden']:
            destination = tmp_path / mode
            record = jobs.submit(JobRequest('exports', 'export', (analysis.result_path, analysis.result_path), {
                'destination': str(destination), 'outputs': {'excel': False},
                'formats': ['svg'], 'plot_legend': mode,
            }))
            wait(jobs, lambda: record.state.terminal, 30)
            assert record.state == JobState.SUCCEEDED, record.error
            manifest = json.loads((destination / 'manifest.json').read_text())
            assert manifest['plot_legend'] == mode
            assert len(manifest['experiments']) == 2
            for experiment in manifest['experiments']:
                assert experiment['plot_legend'] == ('outside' if mode == 'auto' else mode)
                with (destination / experiment['directory'] / 'waveforms.csv').open(encoding='utf-8-sig') as f:
                    actual = list(csv.DictReader(f))
                assert len(actual) == len(expected)
                assert [[float(r[k]) for k in ('time_s', 'voltage_V', 'current_A')] for r in actual] == [
                    [float(r[k]) for k in ('time_s', 'voltage_V', 'current_A')] for r in expected
                ]


def test_gui_forwards_and_remembers_export_legend_selection(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    create_application()
    window = MainWindow(tmp_path / 'workspace')
    panel = window.panels[1]
    panel.result_path = str(tmp_path / 'completed.json')
    calls, initial_choices = [], []

    def accept(dialog):
        initial_choices.append(dialog.plot_legend())
        dialog.legend_mode.setCurrentIndex(dialog.legend_mode.findData('hidden'))
        return 1

    monkeypatch.setattr(ExportDialog, 'exec', accept)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(panel, 'start_export', lambda *a, **kw: calls.append((a, kw)))
    try:
        panel.export_results()
        panel.export_results()
        assert initial_choices == ['outside', 'hidden']
        assert all(kwargs['plot_legend'] == 'hidden' for _, kwargs in calls)
    finally:
        window.close()
