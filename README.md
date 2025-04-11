rdmo-plugins-gitlab
===================

This repo implements two plugins for [RDMO](https://github.com/rdmorganiser/rdmo):

* an [issue provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#issue-providers), which lets users push their tasks from RDMO to GitLab issues.
* a [project import plugins](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-import-plugins), which can be used to import projects from (public or private)repos.

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


Usage
-----

### Issue provider

After the setup, users can add a GitLab intergration to their projects. They need to provide the URL to their repository. Afterwards, project tasks can be pushed to the GitLab repository.

Additionally, a secret can be added to enable GitLab to communicate to RDMO when the status of a work package changed. For this, a webhook has to be added at `https://<repo_url>/-/hooks`. The webhook has to point to `https://<rdmo_url>/projects/<project_id>/integrations/<integration_id>/webhook/` and the secret token has to be exactly the secret entered in the integration.

### Project import

Users can import project import files directly from a public or private GitLab repository.
