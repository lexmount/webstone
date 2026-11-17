# PyPI publishing setup

This repository publishes from `.github/workflows/publish-pypi.yml` using GitHub Actions OIDC. Do not create or store a long-lived PyPI token for this workflow.

## Account and publisher

1. Sign in to [PyPI](https://pypi.org/account/login/). If needed, [register an account](https://pypi.org/account/register/), verify the email address and configure two-factor authentication.
2. If you own an existing `webstone` project, add the publisher under that project's Publishing settings. For a new project, open [account Publishing](https://pypi.org/manage/account/publishing/) and add a pending GitHub publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `webstone` |
| Owner | `lexmount` |
| Repository name | `webstone` |
| Workflow filename | `publish-pypi.yml` |
| Environment name | `pypi` |

A pending publisher does not reserve a package name. Check ownership/name availability before the first release. If the project is owned by another party, resolve its name before publishing; do not upload under an unrelated existing project.

Official instructions: [create a project with Trusted Publishing](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) and [add a publisher to an existing project](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## GitHub environment

Open [repository Environments](https://github.com/lexmount/webstone/settings/environments), create `pypi`, and configure a maintainer as required reviewer for publishing. Use the exact environment name in the table above. Review the environment's deployment restrictions so the approved release tags can deploy; do not select a branch-only rule that excludes release tags.

## First release

1. Merge the reviewed CLI and publication changes; wait for full CI on the exact main commit.
2. Select and approve the software package version under the repository's release policy. Result-edition versions do not authorize a software package release.
3. Verify project metadata, the root lock package and the release tag agree. The workflow rejects a tag not pointing at the checked-out commit or a commit outside main.
4. After the release PR is reviewed and merged and full CI succeeds on that exact main commit, create and push an annotated `vMAJOR.MINOR.PATCH` tag pointing to it. A tag push triggers [the PyPI publication workflow](https://github.com/lexmount/webstone/actions/workflows/publish-pypi.yml); creating a GitHub release alone does not trigger a second upload. The workflow rejects prerelease tags and version mismatches. Never move an existing release tag. Create the matching GitHub release/changelog for that same immutable tag.
5. Approve the `pypi` environment deployment, inspect the resulting wheel/sdist and attestation, and verify the installed `webstone results --help` command in a clean environment.

The workflow checks full successful main CI on the exact SHA, lock consistency, tag/version agreement, wheel/sdist metadata and artifact upload before publishing. A successful package build alone does not mean it has been uploaded to PyPI.

## Personal project ownership

The first release may be owned by the personal PyPI account `lanyue-llk`. Add the pending publisher under that account; the GitHub Owner field is still `lexmount`, not the PyPI login. Package author metadata remains Lexmount. No organization or billing is required for this route. Confirm project ownership, pending publisher fields and protected environment approval before uploading; do not use a long-lived token.

## Lexmount organization

A PyPI organization is separate from the package project and is not a prerequisite for a pending publisher under a user account. In PyPI's account menu, choose Your organizations and request `lexmount` with the company's details. Corporate organizations require billing details and PyPI administrator approval; an authorized company owner should complete those steps. See [organization actions](https://docs.pypi.org/organization-accounts/actions/org-actions/).
