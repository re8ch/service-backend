# Credential contract

GitHub Environments hold credential references. Protected workflows exchange
their OIDC identity for the exact target-specific API identity. The bounded
Cloud SSA executor never returns credential material to callers.

