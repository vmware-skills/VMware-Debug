"""vmware-debug declares a constant environment, and must keep doing so.

Policy rules scope by environment. The baseline treats a target that declares
none as unknown: today a state-changing operation against it runs but logs a
warning (``require_declared_environment: warn``), and the next major release
refuses it outright (``true``).

Every skill with a config answers this per target. vmware-debug has none and no
connection to declare one about — its tools are pure correlation over event
dicts the calling agent has already fetched with other skills' read tools. No
network, no writes, and no operation above read risk. So it registers a
constant ``local`` resolver: the claim "nothing here touches a remote VMware
estate" is simply true.

Nothing this skill ships is gated under either setting today, so this file is
about keeping that honest as the skill changes: if debug ever grows a tool that
writes to a local store, the declaration is already in place and correct, and a
refactor that drops the registration fails here rather than at the moment
enforcement lands.
"""

from __future__ import annotations

import importlib

import pytest

import vmware_policy.environment as env_mod
from vmware_debug.mcp_server import server
from vmware_policy.environment import resolve_environment, set_environment_resolver


@pytest.fixture()
def _policy():
    """The policy engine, or a skip.

    ``vmware_policy.policy._load_rules`` imports PyYAML, but vmware-policy
    declares only typer + rich — so constructing a PolicyEngine raises
    ModuleNotFoundError in this skill's environment. Every other skill happens
    to pull PyYAML in for its own config; vmware-debug has no config and so
    does not. It never calls @vmware_tool either, so this is latent here rather
    than broken.

    Skipping (not stubbing) keeps that honest: these assertions start running
    for real the day vmware-policy declares the dependency.
    """
    pytest.importorskip(
        "yaml",
        reason="vmware-policy imports PyYAML without declaring it; "
        "vmware-debug has no YAML config so it is absent here",
    )
    from vmware_policy.policy import reset_policy_engine

    reset_policy_engine()
    yield
    reset_policy_engine()


@pytest.fixture()
def baseline(_policy):
    """The shipped policy baseline — currently the warn-only migration setting."""
    from vmware_policy.policy import get_policy_engine

    get_policy_engine()
    yield


@pytest.fixture()
def enforcing(_policy, tmp_path):
    """The same rules with the requirement switched on, as the next major
    release will ship it."""
    from vmware_policy.policy import get_policy_engine

    rules = tmp_path / "rules.yaml"
    rules.write_text("require_declared_environment: true\n")
    get_policy_engine(rules)
    yield


@pytest.fixture(autouse=True)
def _restore_resolver():
    """Tests here clear/reload the global resolver; put it back afterwards."""
    yield
    importlib.reload(server)


class TestConstantResolverIsRegistered:
    def test_importing_the_server_registers_a_resolver(self) -> None:
        set_environment_resolver(None)
        importlib.reload(server)

        assert env_mod._resolver is not None, (
            "vmware_debug.mcp_server.server must call set_environment_resolver() at import."
        )
        assert env_mod._resolver is server._environment_for

    def test_registration_is_at_module_level_not_inside_build_server(self) -> None:
        """Importing alone must be enough — no build_server() call required."""
        set_environment_resolver(None)
        importlib.reload(server)  # import only — build_server() not called

        assert env_mod._resolver is not None
        assert resolve_environment("lab") == server.LOCAL_ENVIRONMENT

    def test_resolver_reports_a_non_empty_environment(self) -> None:
        importlib.reload(server)

        # "" is the sentinel for *undeclared*. Anything else is a declaration.
        assert resolve_environment("") != ""
        assert resolve_environment("anything") == server.LOCAL_ENVIRONMENT

    def test_declaration_is_constant_across_targets(self) -> None:
        importlib.reload(server)

        for target in ("", "prod-vc01", "vcenter-lab", "nonsense"):
            assert resolve_environment(target) == server.LOCAL_ENVIRONMENT

    def test_declared_environment_is_not_a_production_label(self) -> None:
        """`local` must not collide with the environments real rules scope to."""
        assert server.LOCAL_ENVIRONMENT not in ("production", "prod", "staging", "")


class TestWriteGateInput:
    """Debug ships no write tools today. Pin that the value the gate keys on is
    a declaration, so a future local-store write is scoped right on day one
    rather than discovered at the enforcing release."""

    def test_the_gate_sees_a_declaration_not_a_blank(self) -> None:
        """``env`` is what PolicyEngine.check_allowed receives; "" is what it
        refuses. This holds regardless of whether PyYAML is installed."""
        importlib.reload(server)

        assert resolve_environment("") == server.LOCAL_ENVIRONMENT

    @pytest.mark.parametrize("mode", ["baseline", "enforcing"])
    @pytest.mark.parametrize("risk", ["medium", "high"])
    def test_a_hypothetical_write_is_allowed(self, mode, request, risk) -> None:
        from vmware_policy.policy import get_policy_engine

        request.getfixturevalue(mode)
        importlib.reload(server)

        result = get_policy_engine().check_allowed(
            "some_future_local_write", env=resolve_environment(""), risk_level=risk
        )
        assert result.allowed is True
        assert result.rule != "undeclared_environment_warning"

    def test_without_a_resolver_such_a_write_would_be_refused(self, enforcing) -> None:
        """Proves the pin above is load-bearing rather than decorative."""
        from vmware_policy.policy import get_policy_engine

        set_environment_resolver(None)

        result = get_policy_engine().check_allowed(
            "some_future_local_write", env=resolve_environment(""), risk_level="medium"
        )
        assert result.allowed is False
        assert result.rule == "undeclared_environment"


class TestReadsAreNeverGated:
    def test_product_read_tools_work_with_no_resolver_at_all(self) -> None:
        """Every tool this skill ships is read-only, so the whole surface must
        keep working regardless of any of this."""
        set_environment_resolver(None)

        from vmware_debug.mcp import tools as t

        assert t.list_symptom_categories()

    @pytest.mark.parametrize("mode", ["baseline", "enforcing"])
    def test_reads_allowed_by_policy_under_both_settings(self, mode, request) -> None:
        from vmware_policy.policy import get_policy_engine

        request.getfixturevalue(mode)
        set_environment_resolver(None)

        assert get_policy_engine().check_allowed(
            "incident_timeline", env="", risk_level="low"
        ).allowed
