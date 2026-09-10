from forge.msdk.msdk_build.spinup import spinup_manifest_repo
from forge.msdk.msdk_build.env_init import init_environment

repo = spinup_manifest_repo("/Users/mehdi/Sandbox/my-manifest-repo")
init_environment(str(repo))
