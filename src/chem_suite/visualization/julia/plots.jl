function draw_spec(spec)
    fig = Figure(size=(950, 540), fontsize=16, backgroundcolor=:white)
    ax = Axis(fig[1, 1]; title=spec.title, xlabel=spec.xlabel, ylabel=spec.ylabel,
              xscale=spec.xscale == "log" ? log10 : identity,
              yscale=spec.yscale == "log" ? log10 : identity)
    if get(spec, :equal_aspect, false)
        ax.aspect = DataAspect()
    end
    palette = [:steelblue, :darkorange, :seagreen, :purple, :firebrick]
    have_data = false
    for (n, curve) in enumerate(spec.series)
        x, y = Float64.(curve.x), Float64.(curve.y)
        isempty(x) && continue
        color = palette[mod1(n, length(palette))]
        if curve.kind == "scatter"
            scatter!(ax, x, y; label=curve.label, color=color, markersize=8)
        else
            lines!(ax, x, y; label=curve.label, color=color, linewidth=2)
        end
        have_data = true
    end
    have_data && axislegend(ax; position=:rt)
    return fig
end

