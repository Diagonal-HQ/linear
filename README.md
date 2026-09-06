# linear

A small, standalone command-line client for Linear. It provides focused issue
commands plus a file-based GraphQL escape hatch for queries and mutations that
do not warrant application-specific workflows.

## Setup

`bin/linear` is the whole app. It needs Python 3.10 or newer, but has no
packages to install and does not need the rest of this repository.

```sh
curl -fsSL https://raw.githubusercontent.com/Diagonal-HQ/linear/main/install.sh | sh
~/.local/bin/linear --help
```

The installer writes to `~/.local/bin`. Set `LINEAR_INSTALL_DIR` on the shell
that runs the piped installer to choose a different directory:

```sh
curl -fsSL https://raw.githubusercontent.com/Diagonal-HQ/linear/main/install.sh | LINEAR_INSTALL_DIR="$HOME/bin" sh
```

## Configure

Provide a Linear OAuth application's client credentials in the environment:

```sh
export LINEAR_CLIENT_ID='your-client-id'
export LINEAR_CLIENT_SECRET='your-client-secret'
```

The default requested scopes are exactly:

```text
read,write,app:assignable,app:mentionable
```

Set `LINEAR_SCOPE` to override this list, preserving the scopes already used by
your app. Changing scopes revokes its existing app-actor tokens; see
[Linear's OAuth documentation](https://linear.app/developers/oauth-2-0-authentication#client-credentials-tokens).

Tokens are cached at
`${XDG_CACHE_HOME:-~/.cache}/linear/linear_token.json`. The cache directory and
file use modes `0700` and `0600`; writes are atomic. A cached token is accepted
only when its client ID, client secret, and scopes match the current settings.
The client re-mints when a cached token is within one minute of expiry, and
re-mints once after an HTTP 401.
It does not inspect, migrate, or remove caches belonging to other tools.

## Commands

| Command | Purpose |
| --- | --- |
| `linear token` | Print the current app-actor access token. |
| `linear issue <identifier>` | Print one issue as JSON. |
| `linear comments <identifier>` | Print an issue's comments as JSON. |
| `linear users` | Print workspace users as JSON. |
| `linear set-state <identifier> <state-name>` | Move an issue to a workflow state by name. |
| `linear set-description <identifier> [--file <path>]` | Replace an issue description from a file or standard input. |
| `linear comment <identifier> <body>` | Add a comment to an issue. |
| `linear graphql --file <path\|-> [--variables-file <path>]` | Run a GraphQL document and print its JSON data. |
| `linear --version` | Print the version. |

`set-description` reads standard input when `--file` is omitted. `token`
explicitly prints the current access token, which is a secret; do not paste its
output into logs or chat.

The GraphQL command reads a query from a file (or standard input with `-`) and
prints the response's JSON `data` object. Variables must be a JSON object:

```sh
printf '%s\n' 'query Issue($id: String!) { issue(id: $id) { id title } }' > issue.graphql
printf '%s\n' '{"id":"ENG-123"}' > variables.json
linear graphql --file issue.graphql --variables-file variables.json
```

Or pipe a query without variables:

```sh
printf '%s\n' '{ viewer { id name } }' | linear graphql --file -
```

## Using it from a Paseo prompt

The Paseo agent must inherit `LINEAR_CLIENT_ID` and `LINEAR_CLIENT_SECRET`
(plus `LINEAR_SCOPE`, if set). Invoke the installed app by its explicit path:

```sh
~/.local/bin/linear issue ENG-123
```

## Agent skill

[skills/diagonal-linear/SKILL.md](skills/diagonal-linear/SKILL.md) is a
self-contained example skill that clients can install to teach their agents how
to use this CLI. It covers credentials, issue commands, GraphQL, pagination, and
verification of writes.

Set `agent_skills_dir` to your agent's configured skill directory (shown here as
`~/.agents/skills`), then download the skill:

```sh
agent_skills_dir="$HOME/.agents/skills"
mkdir -p "$agent_skills_dir/diagonal-linear"
curl -fsSL https://raw.githubusercontent.com/Diagonal-HQ/linear/main/skills/diagonal-linear/SKILL.md \
  -o "$agent_skills_dir/diagonal-linear/SKILL.md"
```

Reload skills or start a new agent session as required by your client. Install
the CLI and configure its credentials using the instructions above; the skill
contains instructions and does not install the executable or supply credentials.

## Development

```sh
make check
```

The tests exercise a standalone copy of `bin/linear` and the piped installer.
They use synthetic credentials, mocked HTTP transport, a fake downloader, and
temporary cache/install directories. They do not contact Linear or GitHub.

This helper was extracted from Diagonal engineering automation code.
