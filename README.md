rdmo-plugins-gitlab
===================

This repo implements three plugins for [RDMO](https://github.com/rdmorganiser/rdmo):

* an [issue provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#issue-providers), which lets users push their tasks from RDMO to GitLab issues.
* a [project import plugins](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-import-plugins), which can be used to import projects from (public or private)repos.
* an export plugin, which can be used to export projects to (public or private) repos. For SMP projects, this plugin also provides other export choices that reuse project data (e.g. README, CITATION or LICENSE files).

The plugin uses [OAUTH 2.0](https://oauth.net/2/), so that users use their respective accounts in both systems.


Setup
-----

Install the plugin in your RDMO virtual environment using pip (directly from GitHub):

```bash
pip install git+https://github.com/rdmorganiser/rdmo-plugins-gitlab
```

An *App* has to be registered with the particular GitLab instance. For GitLab.com, go to https://gitlab.com/-/profile/applications and create an application with the callback URL `https://<rdmo_url>/services/oauth/gitlab/callback/` and the scope `api`.

The `client_id` and the `client_secret`, together with the `gitlab_url`, need to be configured in `config/settings/local.py`:

```python
GITLAB_PROVIDER = {
    'gitlab_url': 'https://gitlab.com',
    'client_id': '',
    'client_secret': ''
}
```

For the issue provider, add the plugin to `PROJECT_ISSUE_PROVIDERS` in `config/settings/local.py`:

```python
PROJECT_ISSUE_PROVIDERS += [
    ('gitlab', _('GitLab Provider'), 'rdmo_gitlab.providers.GitLabIssueProvider'),
]
```

For the import, add the plugin to `PROJECT_IMPORTS` and `PROJECT_IMPORTS_LIST` in `config/settings/local.py`:

```python
PROJECT_IMPORTS = [
    ('gitlab', _('Import from GitLab'), 'rdmo_gitlab.providers.GitLabImport'),
]

PROJECT_IMPORTS_LIST += ['gitlab']
```

For the export:

1. Add the plugin to `PROJECT_EXPORTS` in `config/settings/local.py`:

```python
PROJECT_EXPORTS += [
    ('gitlab', _('GitLab'), 'rdmo_github.providers.GitLabExportProvider'),
]
```

2. Install the helper plugin "MAUS" in your RDMO virtual environment using pip (directly from GitHub). MAUS provides the SMP specific export choices:

```bash
not working yet!!!!!!!!
pip install git+https://github.com/MPDL/rdmo-plugins-maus
```

Usage
-----

### Issue provider

After the setup, users can add a GitLab intergration to their projects. They need to provide the URL to their repository. Afterwards, project tasks can be pushed to the GitLab repository.

Additionally, a secret can be added to enable GitLab to communicate to RDMO when the status of a work package changed. For this, a webhook has to be added at `https://<repo_url>/-/hooks`. The webhook has to point to `https://<rdmo_url>/projects/<project_id>/integrations/<integration_id>/webhook/` and the secret token has to be exactly the secret entered in the integration.

### Project import

Users can import project import files directly from a public or private GitLab repository.

### Project export

Users can export project import files directly to a public or private GitLab repository. For SMP projects, they can also export custom files (README, CITATION, LICENSE) created with the SMP project's data. They can choose to export to an existing repository or to create a new one.
