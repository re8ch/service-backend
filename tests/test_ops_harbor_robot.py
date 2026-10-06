from unittest.mock import Mock

from test_ops_mcp_tool_profiles import load_profile


def test_existing_project_robot_does_not_create_or_rotate():
    app = load_profile("admin")
    app.harbor_get_project = Mock(return_value={"project_id": 31})
    robot = {"id": 2385, "name": "robot$re8ch+re8ch-admin-20260606"}
    app.paged_harbor = Mock(return_value=[robot])
    app.harbor_request = Mock(side_effect=AssertionError("unexpected mutation"))
    result = app.ensure_harbor_project_admin_robot("re8ch")
    assert result == {"action": "exists", "robotName": robot["name"], "tokenAvailable": False}
    app.paged_harbor.assert_called_once_with("/robots?q=Level%3Dproject%2CProjectID%3D31")
    app.harbor_request.assert_not_called()


def test_robot_lookup_does_not_match_another_project():
    app = load_profile("admin")
    app.harbor_get_project = Mock(return_value={"project_id": 31})
    app.paged_harbor = Mock(return_value=[{"name": "robot$other+re8ch-admin-20260606"}])
    assert app.find_harbor_robot("re8ch-admin-20260606", project="re8ch") is None
