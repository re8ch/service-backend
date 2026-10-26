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
