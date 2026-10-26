import pytest

from kustomize_render import KustomizeRender


@pytest.fixture(scope="session")
def kustomize_documents(request):
    renderer = KustomizeRender()
    yield renderer
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"Kustomize session renders: {renderer.executed} executed, {renderer.reused} reused"
        )


@pytest.fixture(scope="session", autouse=True)
def validation_inputs(request):
    from operations.ci.input_snapshot import session_inputs

    with session_inputs() as snapshot:
        yield snapshot
        request.config._validation_input_statistics = {"parsed": snapshot.parsed, "reused": snapshot.reused}
        reporter = request.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                f"Input session parses: {snapshot.parsed} parsed, {snapshot.reused} reused"
            )
