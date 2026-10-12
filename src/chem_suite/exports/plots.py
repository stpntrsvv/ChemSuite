"""Publication plots; optional Matplotlib stays in the export worker."""

import textwrap

from chem_suite.locale import text

PALETTE = ('#087f8c', '#d27b2c', '#42885b', '#87569c', '#b74e55')


def plot_figure(specs, language, context, *, legend='inside'):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    if legend not in {'outside', 'inside', 'hidden'}:
        raise ValueError('Unsupported export legend mode')
    figure = Figure(figsize=(8 if legend == 'outside' else 7, 4 * len(specs)), layout='constrained')
    canvas = FigureCanvasAgg(figure)
    renderer = canvas.get_renderer()
    heights = []
    for spec in specs:
        heights.extend([3.2, 0.5] if legend == 'outside' and spec['series'] else [3.2])
    grid = figure.add_gridspec(len(heights), 1, height_ratios=heights)
    row = 0
    extra_height = 0.0
    for spec in specs:
        context.check_cancelled()
        axis = figure.add_subplot(grid[row])
        row += 1
        for index, curve in enumerate(spec['series']):
            if index % 100 == 0:
                context.check_cancelled()
            color = PALETTE[index % len(PALETTE)]
            label = text(curve['label'], language)
            if curve['kind'] == 'scatter':
                axis.scatter(curve['x'], curve['y'], s=12, color=color, label=label)
            else:
                axis.plot(curve['x'], curve['y'], color=color, label=label)
        axis.set(title=text(spec['title'], language), xlabel=text(spec['xlabel'], language),
                 ylabel=text(spec['ylabel'], language), xscale=spec['xscale'], yscale=spec['yscale'])
        if spec.get('equal_aspect'):
            axis.set_aspect('equal', adjustable='box')
        axis.grid(True, alpha=.2)
        if legend == 'inside' and spec['series']:
            # An overlaid legend must not collapse the data axes when its list
            # is taller than the plot. savefig includes its full bounds below.
            axis.legend(fontsize=8).set_in_layout(False)
        elif legend == 'outside' and spec['series']:
            # A separate grid row reserves space without covering data or the
            # next plot. Measure actual font/label geometry rather than
            # squeezing the data axes to fit a long legend.
            legend_axis = figure.add_subplot(grid[row])
            legend_axis.set_axis_off()
            handles, labels = axis.get_legend_handles_labels()
            labels = ['\n'.join(textwrap.wrap(label, width=42)) for label in labels]
            for columns in range(min(3, max(1, len(labels))), 0, -1):
                artist = legend_axis.legend(handles, labels, loc='center', ncols=columns,
                                            fontsize=8, frameon=False, borderaxespad=0)
                extent = artist.get_window_extent(renderer)
                if extent.width / figure.dpi <= 6.5 or columns == 1:
                    break
                artist.remove()
            heights[row] = max(0.3, extent.height / figure.dpi + 0.15)
            extra_height += heights[row]
            row += 1
    grid.set_height_ratios(heights)
    if legend == 'outside':
        figure.set_size_inches(8, 4 * len(specs) + extra_height)
    return figure


def render_plots(folder, groups, formats, language, context, *, legend='inside'):
    for name, specs in groups.items():
        context.check_cancelled()
        figure = plot_figure(specs, language, context, legend=legend)
        try:
            for extension in formats:
                context.check_cancelled()
                if extension not in {'png', 'svg', 'pdf'}:
                    raise ValueError('Unsupported plot format')
                extra_artists = [axis.get_legend() for axis in figure.axes if axis.get_legend() is not None]
                figure.savefig(folder / f'{name}.{extension}', dpi=180, bbox_inches='tight',
                               bbox_extra_artists=extra_artists)
        finally:
            figure.clear()
