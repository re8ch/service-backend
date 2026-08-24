"""Keyless Pipecat WebRTC smoke bot for the R640 voice lab."""

from importlib.metadata import version

from fastapi.responses import JSONResponse
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.runner.run import app
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner


TRANSPORT_PARAMS = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok", "service": "pipecat-echo", "pipecat": version("pipecat-ai")})


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    # AudioInputFrame objects pass directly back to the browser. This deliberately
    # uses no model credentials and verifies the complete WebRTC/Pipeline path.
    worker = PipelineWorker(
        Pipeline([transport.input(), transport.output()]),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, client):
        logger.info("Pipecat echo client connected: {}", client)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, client):
        logger.info("Pipecat echo client disconnected: {}", client)
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, TRANSPORT_PARAMS)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
