# Contributing

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
Contributions are accepted under the [Apache License 2.0](LICENSE).

The repo is in the design stage: the specs under `specs/` are the work. Edit
them by pull request. A change to a data contract another component depends on
(staged manifest shape, event payloads, policy schema) should say so in the PR
and link the consuming spec.

When workflows land, the rules that apply to every sibling repo apply here:
small changes, one assertion that fails when the logic breaks, comments that
say why. Workflows in this repo must never acquire a secret beyond the OIDC
token exchange; a PR that adds one will be declined on that ground alone.
