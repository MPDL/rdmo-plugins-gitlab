rdmo-plugins-gitlab
===================

This repo implements three plugins for [RDMO](https://github.com/rdmorganiser/rdmo):

* an [issue provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#issue-providers), which lets users push their tasks from RDMO to GitLab issues.
* an [import provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-import-plugins), which can be used to import projects from (public or private) repos. For SMP projects, repository metadata (dependecy graph, languages, license, CITATION or CodeMeta) can also be imported.
* an [export provider](https://rdmo.readthedocs.io/en/latest/plugins/index.html#project-export-plugins), which can be used to export projects to (public or private) repos. For SMP projects, this plugin also provides other export choices that reuse project data (e.g. README, CITATION, CodeMeta or LICENSE files).

The plugins use [OAUTH 2.0](https://oauth.net/2/), so that users use their respective accounts in both systems.


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

If you want to support *multiple* GitLab providers, instead of defining a GITLAB_PROVIDER, define multiple GITLAB_PROVIDERS in `config/settings/local.py`:

```python
GITLAB_PROVIDERS = {
    _('Provider 1'): {
        'gitlab_url': 'https://gitlab.com',
        'client_id': '',
        'client_secret': ''
    }, 
    _('Provider 2'): {
        'gitlab_url': 'https://gitlab.my-institute.de',
        'client_id': '',
        'client_secret': ''
    },
    ...
}
```

Add the gitlab plugin app (`rdmo_gitlab`) to `INSTALLED_APPS` in `config/settings/local.py`:

```python
INSTALLED_APPS += ['rdmo_gitlab']
```

For the issue provider, add the plugin to `PROJECT_ISSUE_PROVIDERS` in `config/settings/local.py`:

```python
PROJECT_ISSUE_PROVIDERS += [
    ('gitlab', _('GitLab Provider'), 'rdmo_gitlab.providers.exports.GitLabIssueProvider'),
]
```

For the import provider, add the plugin to `PROJECT_IMPORTS` and its key to `PROJECT_IMPORTS_LIST` in `config/settings/local.py`:

```python
PROJECT_IMPORTS = [
    ('gitlab', _('GitLab'), 'rdmo_gitlab.providers.imports.GitLabImportProvider'),
]

PROJECT_IMPORTS_LIST += ['gitlab']
```

For the export provider, add the plugin to `PROJECT_EXPORTS` in `config/settings/local.py`:

```python
PROJECT_EXPORTS += [
    ('gitlab', _('GitLab'), 'rdmo_gitlab.providers.exports.GitLabExportProvider'), 
]
```

The export and import plugins use the plugin [rdmo-plugins-maus](https://github.com/MPDL/rdmo-plugins-maus). This plugin provides the SMP specific import and export choices as well as a custom field used in their form templates. `rdmo-plugins-maus` is installed as a dependency of `rdmo-plugins-gitlab`, but it must be also included in `INSTALLED_APPS` in `config/settings/local.py`:

```python
INSTALLED_APPS += ['rdmo_maus']
```


Usage
-----

### Issue provider

Users can add a GitLab intergration to their projects. They need to provide the URL to their repository. Afterward, project tasks can be pushed to the GitLab repository as repo issues.

Additionally, a secret can be added to enable GitLab to communicate to RDMO when the status of a work package changed. For this, a webhook has to be added at `https://<repo_url>/-/hooks`. The webhook has to point to `https://<rdmo_url>/projects/<project_id>/integrations/<integration_id>/webhook/` and the secret token has to be exactly the secret entered in the integration.

### Import provider

Users can import xml project files, and for SMP projects also repository metadata (dependency graph, languages, license, CITATION or CodeMeta files) directly from a public or private GitLab repository.

### Export provider

Users can export project files directly to a public or private GitLab repository. For SMP projects, they can also export metadata files (README, CITATION, CodeMeta, LICENSE) created with the SMP project's data. They can choose to export to an existing repository or to create a new public one.


Funding Acknowledgments
-----

The import and export provider plugins in this repository were created by [Max Planck Information and Technology](https://maxit.mpg.de) in the context of a project funded by the [Deutsche Forschungsgemeinschaft](https://www.dfg.de) (DFG, German Research Foundation) – Project number: [543616919](https://gepris.dfg.de/project/543616919).