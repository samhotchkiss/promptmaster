from pollypm.plugin_api.v1 import Capability, PollyPMPlugin


def _factory(**kwargs):
    from pollypm.recovery.default import DefaultRecoveryPolicy
    return DefaultRecoveryPolicy(**kwargs)


plugin = PollyPMPlugin(
    name="default_recovery_policy",
    capabilities=(Capability(kind="recovery_policy", name="default"),),
    recovery_policies={"default": _factory},
)
