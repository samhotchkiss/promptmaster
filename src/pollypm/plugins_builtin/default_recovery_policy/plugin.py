from pollypm.plugin_api.v1 import PollyPMPlugin


def _factory(**kwargs):
    from pollypm.recovery.default import DefaultRecoveryPolicy
    return DefaultRecoveryPolicy(**kwargs)


plugin = PollyPMPlugin(
    name="default_recovery_policy",
    capabilities=("recovery_policy",),
    recovery_policies={"default": _factory},
)
