using WGLMakie, Bonito, JSON3
include("plots.jl")
WGLMakie.activate!()
server = Bonito.Server(App(DOM.div("Chem Suite · Makie")), "127.0.0.1", parse(Int, ARGS[1]))
println(JSON3.write((type="ready", port=server.port)))
flush(stdout)
try
    while !eof(stdin)
        line = readline(stdin)
        isempty(line) && continue
        id = ""
        try
            request = JSON3.read(line)
            request.type == "shutdown" && break
            id = String(request.id)
            result = JSON3.read(read(String(request.result_path), String))
            specs = haskey(request, :display_plots) ? request.display_plots : result.payload.plots
            app = App() do
                figures = [draw_spec(spec) for spec in specs]
                DOM.div(figures...; style="display:flex;flex-direction:column;align-items:center;gap:24px;padding:24px;background:#f5f7fa")
            end
            route = "/plots/" * id
            route!(server, route => app)
            println(JSON3.write((type="plot", id=id, url="http://127.0.0.1:$(server.port)$route")))
            flush(stdout)
        catch error
            println(JSON3.write((type="error", id=id, message=sprint(showerror, error))))
            flush(stdout)
        end
        yield()
    end
finally
    close(server)
end

