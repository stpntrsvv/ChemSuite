using CairoMakie, JSON3
include("plots.jl")
result = JSON3.read(read(ARGS[1], String))
mkpath(ARGS[2])
for (n, spec) in enumerate(result.payload.plots)
    fig = draw_spec(spec)
    save(joinpath(ARGS[2], "plot-$n.svg"), fig)
    save(joinpath(ARGS[2], "plot-$n.pdf"), fig)
    save(joinpath(ARGS[2], "plot-$n.png"), fig; px_per_unit=1.5)
end

