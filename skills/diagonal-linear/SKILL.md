---
name: diagonal-linear
description: Use the Diagonal-HQ/linear CLI to read and update Linear issues, post comments, and run GraphQL with OAuth app credentials. Apply when a task uses this standalone CLI, including automation without a Linear MCP connection.
---

# Diagonal Linear CLI

Use the standalone CLI from [Diagonal-HQ/linear](https://github.com/Diagonal-HQ/linear).
It authenticates as a Linear OAuth app and supports issue commands and arbitrary
GraphQL queries and mutations.

## Locate and configure

Use the executable path configured by the client. The default installation is
`~/.local/bin/linear`; `LINEAR_INSTALL_DIR` can select another directory. Check
the executable's help before using it, since another CLI may also be named
`linear`:

```sh
linear_cli="${LINEAR_INSTALL_DIR:-$HOME/.local/bin}/linear"
"$linear_cli" --version
"$linear_cli" --help
"$linear_cli" graphql --help
```

The expected help describes a standalone Linear GraphQL client and includes
`graphql --file`. Keep using that executable path for subsequent commands. See
the [README setup instructions](https://github.com/Diagonal-HQ/linear#setup)
if it is missing. Python 3.10 or newer is required; no Python packages are needed.

The CLI requires `LINEAR_CLIENT_ID` and `LINEAR_CLIENT_SECRET` in its process
environment. Report missing variable names without displaying their values.
Scheduled agents must inherit these variables from their launcher; exporting
them in an unrelated shell does not configure an already-running process.

`LINEAR_SCOPE` optionally overrides `read,write,app:assignable,app:mentionable`.
Preserve the app's configured scopes: [changing scopes revokes its other
app-actor tokens](https://linear.app/developers/oauth-2-0-authentication#client-credentials-tokens).
API commands mint and cache tokens automatically; `token`
prints the access token, so avoid it for connectivity checks and keep its output
out of agent logs. The cache is
`${XDG_CACHE_HOME:-~/.cache}/linear/linear_token.json` and refreshes near expiry.

## Choose a command

Replace the example identifiers and arguments with the user's intended values.
Read commands return JSON; the write helpers return confirmation text.

| Command, after `"$linear_cli"` | Behavior |
| --- | --- |
| `issue ENG-123` | Fetch one issue, including its state, description, and URL. |
| `comments ENG-123` | Fetch the issue's comments. |
| `users` | List the first 100 workspace users. |
| `set-state ENG-123 "In Progress"` | Set a workflow state by its name in the issue's team. |
| `set-description ENG-123 --file description.md` | Replace the entire description; without `--file`, read standard input. |
| `comment ENG-123 "Message text"` | Post a comment; the body is a positional argument. |
| `graphql --file query.graphql --variables-file variables.json` | Run a query or mutation and print its JSON `data` object. |

Read the existing issue before editing its description when the request is to
amend rather than replace it. Resolve ambiguous targets and team/user IDs through
read-only lookups. Apply writes within the user's requested scope.

## GraphQL

Use GraphQL for searches, issue creation, fields, or operations without a built-in
command. Consult the current [Linear API documentation](https://linear.app/developers/graphql)
or schema for unfamiliar fields and input types. Put dynamic values in a JSON
object passed through `--variables-file`, rather than interpolating user text
into the query. For complete lists, request `pageInfo` and use `endCursor` as
`after` while `hasNextPage` is true; see [pagination](https://linear.app/developers/pagination).
The convenience commands do not paginate all results.

This read-only example uses temporary files so it does not overwrite project
files. Serialize actual user values as JSON when adapting the variables:

```sh
request_dir=$(mktemp -d "${TMPDIR:-/tmp}/diagonal-linear.XXXXXX")
cat > "$request_dir/query.graphql" <<'GRAPHQL'
query Issue($id: String!) {
  issue(id: $id) { id identifier title url }
}
GRAPHQL
printf '%s\n' '{"id":"ENG-123"}' > "$request_dir/variables.json"
"$linear_cli" graphql --file "$request_dir/query.graphql" \
  --variables-file "$request_dir/variables.json"
```

`--file -` reads the query from standard input. The variables argument must name
a file containing a JSON object; it does not accept inline JSON or standard input.
The CLI prints `data` directly, without an outer `data` property.

## Check the result

Check the exit status and returned data. Raw GraphQL mutations may return
`success: false` inside `data`; successful command execution alone does not prove
the requested change happened. Read back the affected issue or comments to verify
a write. If a create/comment request times out, look for the intended result
before retrying to avoid duplicates.

The CLI retries an API HTTP 401 once with a freshly minted token. Report remaining
authentication, permission, or network errors without exposing credentials.
Missing MCP access does not prevent this CLI from working when its own
credentials are configured.
