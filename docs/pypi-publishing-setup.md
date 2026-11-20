# PyPI publishing setup

This repository publishes from `.github/workflows/publish-pypi.yml` using GitHub Actions OIDC. Do not create or store a long-lived PyPI token for this workflow.

## Account and publisher

1. Sign in to [PyPI](https://pypi.org/account/login/). If needed, [register an account](https://pypi.org/account/register/), verify the email address and configure two-factor authentication.
2. Open Publishing for the [existing `webstone` project](https://pypi.org/manage/project/webstone/settings/publishing/). Use these values to verify the publisher or re-add it if needed:

| Field | Value |
| --- | --- |
| PyPI project name | `webstone` |
| Owner | `lexmount` |
| Repository name | `webstone` |
| Workflow filename | `publish-pypi.yml` |
| Environment name | `pypi` |

See [PyPI's instructions for existing projects](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## GitHub environment

Open [repository Environments](https://github.com/lexmount/webstone/settings/environments), create `pypi`, and configure a maintainer as required reviewer for publishing. Use the exact environment name in the table above. Review the environment's deployment restrictions so the approved release tags can deploy; do not select a branch-only rule that excludes release tags.

## Publish a release

1. Merge the reviewed release changes and wait for full CI on the exact main commit.
2. Select and approve the software package version under the repository's release policy. Result-edition versions do not authorize a software package release.
3. Verify project metadata, the root lock package and the release tag agree. The workflow rejects a tag not pointing at the checked-out commit or a commit outside main.
4. Push the approved annotated `vMAJOR.MINOR.PATCH` tag for the tested main commit. This starts the [PyPI publication workflow](https://github.com/lexmount/webstone/actions/workflows/publish-pypi.yml). Publish the changelog in GitHub Releases against the same immutable tag.
5. Approve the `pypi` environment deployment. Verify the wheel, source distribution and attestation on PyPI, then install the exact release in a clean environment and run `webstone tracks` and `webstone skills install --help`.

The workflow checks full successful main CI on the exact SHA, lock consistency, tag/version agreement, wheel/sdist metadata and artifact upload before publishing. A successful package build alone does not mean it has been uploaded to PyPI.

## Lexmount organization

A PyPI organization is separate from the package project. For organization administration, see [PyPI organization actions](https://docs.pypi.org/organization-accounts/actions/org-actions/).
