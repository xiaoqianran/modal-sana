from __future__ import annotations

import typer

from modal_sana import __version__
from modal_sana.cli.batch import batch
from modal_sana.cli.benchmark import benchmark
from modal_sana.cli.doctor import doctor
from modal_sana.cli.generate import generate
from modal_sana.cli.jobs import cancel, job, jobs, resume
from modal_sana.cli.models import gpus, models
from modal_sana.cli.prefetch import prefetch
from modal_sana.cli.trace import cost, trace
from modal_sana.cli.web import web

app = typer.Typer(
    name="modal-sana",
    help="Local prompt workbench. Modal GPUs run SANA. Images come back here.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

app.command("generate")(generate)
app.command("batch")(batch)
app.command("jobs")(jobs)
app.command("job")(job)
app.command("resume")(resume)
app.command("cancel")(cancel)
app.command("models")(models)
app.command("gpus")(gpus)
app.command("doctor")(doctor)
app.command("web")(web)
app.command("benchmark")(benchmark)
app.command("trace")(trace)
app.command("cost")(cost)
app.command("prefetch")(prefetch)


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", help="Show version and exit"),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


def main() -> None:
    app()
