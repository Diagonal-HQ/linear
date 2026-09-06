import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "linear"
INSTALLER = ROOT / "install.sh"
DOWNLOAD_URL = "https://raw.githubusercontent.com/Diagonal-HQ/linear/main/bin/linear"
LOADER = importlib.machinery.SourceFileLoader("linear_cli", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
linear = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(linear)


class Response:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


def token_response(token="minted-token", expires_in=3600):
    return Response({"access_token": token, "expires_in": expires_in})


def http_error(url, code, body):
    return urllib.error.HTTPError(url, code, "failure", {}, io.BytesIO(body.encode()))


def write_fake_curl(directory, body):
    directory.mkdir(parents=True, exist_ok=True)
    curl = directory / "curl"
    curl.write_text("#!/bin/sh\nset -eu\n" + body)
    curl.chmod(0o755)
    return curl


def successful_curl_body():
    return r'''output=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) shift; output=$1 ;;
    -*) ;;
    *) url=$1 ;;
  esac
  shift
done
[ "$url" = "$FAKE_CURL_EXPECTED_URL" ]
cp "$FAKE_CURL_SOURCE" "$output"
'''


class LinearTest(unittest.TestCase):
    def env(self, cache_root, **overrides):
        values = {
            "XDG_CACHE_HOME": str(cache_root),
            "LINEAR_CLIENT_ID": "client-id",
            "LINEAR_CLIENT_SECRET": "client-secret",
        }
        values.update(overrides)
        return mock.patch.dict(os.environ, values, clear=True)

    def run_main(self, argv, stdin=""):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            result = linear.main(argv)
        return result, stdout.getvalue(), stderr.getvalue()

    # The nine original authentication/cache tests, migrated to bin/linear.

    def test_mint_uses_environment_credentials_and_default_or_custom_scope(self):
        cases = (
            (None, "read,write,app:assignable,app:mentionable"),
            ("read,app:assignable", "read,app:assignable"),
        )
        for configured_scope, expected_scope in cases:
            with self.subTest(scope=configured_scope), tempfile.TemporaryDirectory() as cache_root:
                overrides = {}
                if configured_scope is not None:
                    overrides["LINEAR_SCOPE"] = configured_scope
                with self.env(cache_root, **overrides), mock.patch.object(
                    linear.urllib.request, "urlopen", return_value=token_response()
                ) as urlopen:
                    self.assertEqual(linear.get_linear_token(), "minted-token")

                request = urlopen.call_args.args[0]
                expected_auth = base64.b64encode(b"client-id:client-secret").decode()
                self.assertEqual(request.full_url, linear.LINEAR_TOKEN_URL)
                self.assertEqual(request.headers["Authorization"], f"Basic {expected_auth}")
                form = urllib.parse.parse_qs(request.data.decode())
                self.assertEqual(form["grant_type"], ["client_credentials"])
                self.assertEqual(form["scope"], [expected_scope])

    def test_missing_and_blank_credentials_are_reported_without_values(self):
        with mock.patch.dict(
            os.environ,
            {"LINEAR_CLIENT_ID": "  ", "LINEAR_CLIENT_SECRET": "top-secret"},
            clear=True,
        ), mock.patch.object(linear.urllib.request, "urlopen") as urlopen:
            result, stdout, stderr = self.run_main(["users"])

        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertIn("LINEAR_CLIENT_ID", stderr)
        self.assertNotIn("top-secret", stderr)
        urlopen.assert_not_called()

        with mock.patch.dict(os.environ, {}, clear=True):
            result, _, stderr = self.run_main(["users"])
        self.assertEqual(result, 1)
        self.assertIn("LINEAR_CLIENT_ID", stderr)
        self.assertIn("LINEAR_CLIENT_SECRET", stderr)

    def test_cache_is_reused_until_its_early_expiry(self):
        with tempfile.TemporaryDirectory() as cache_root, self.env(cache_root), mock.patch.object(
            linear.time, "time", return_value=1000
        ) as clock, mock.patch.object(
            linear.urllib.request,
            "urlopen",
            side_effect=[token_response("first", 120), token_response("second", 120)],
        ) as urlopen:
            self.assertEqual(linear.get_linear_token(), "first")
            self.assertEqual(linear.get_linear_token(), "first")
            self.assertEqual(urlopen.call_count, 1)

            clock.return_value = 1061
            self.assertEqual(linear.get_linear_token(), "second")
            self.assertEqual(urlopen.call_count, 2)

    def test_changed_client_id_secret_or_scope_invalidates_cache(self):
        changes = {
            "client ID": {"LINEAR_CLIENT_ID": "other-client"},
            "client secret": {"LINEAR_CLIENT_SECRET": "other-secret"},
            "scope": {"LINEAR_SCOPE": "read"},
        }
        for label, change in changes.items():
            with self.subTest(change=label), tempfile.TemporaryDirectory() as cache_root:
                with self.env(cache_root), mock.patch.object(
                    linear.urllib.request, "urlopen", return_value=token_response("original")
                ):
                    self.assertEqual(linear.get_linear_token(), "original")
                with self.env(cache_root, **change), mock.patch.object(
                    linear.urllib.request,
                    "urlopen",
                    return_value=token_response("replacement"),
                ) as urlopen:
                    self.assertEqual(linear.get_linear_token(), "replacement")
                    urlopen.assert_called_once()

    def test_cache_is_created_privately_in_linear_directory(self):
        with tempfile.TemporaryDirectory() as cache_root, self.env(cache_root), mock.patch.object(
            linear.urllib.request, "urlopen", return_value=token_response()
        ):
            linear.get_linear_token()
            cache = Path(cache_root) / "linear" / "linear_token.json"
            self.assertTrue(cache.is_file())
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(cache.parent.stat().st_mode), 0o700)

    def test_cache_write_failure_still_returns_minted_token(self):
        with tempfile.TemporaryDirectory() as cache_root, self.env(cache_root), mock.patch.object(
            linear.urllib.request, "urlopen", return_value=token_response()
        ), mock.patch.object(linear.tempfile, "mkstemp", side_effect=PermissionError):
            self.assertEqual(linear.get_linear_token(), "minted-token")

    def test_home_cache_fallback_does_not_touch_another_tools_cache(self):
        with tempfile.TemporaryDirectory() as home:
            old_cache = Path(home) / ".cache" / "probe-profile" / "linear_token.json"
            old_cache.parent.mkdir(parents=True)
            old_cache.write_text('{"token":"leave-me-alone"}')
            environment = {
                "HOME": home,
                "LINEAR_CLIENT_ID": "client-id",
                "LINEAR_CLIENT_SECRET": "client-secret",
            }
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
                linear.urllib.request, "urlopen", return_value=token_response("new-token")
            ):
                self.assertEqual(linear.get_linear_token(), "new-token")

            self.assertEqual(old_cache.read_text(), '{"token":"leave-me-alone"}')
            self.assertTrue(
                (Path(home) / ".cache" / "linear" / "linear_token.json").is_file()
            )

    def test_graphql_401_forces_exactly_one_remint_and_retry(self):
        token_requests = []
        graphql_tokens = []

        def urlopen(request, timeout):
            if request.full_url == linear.LINEAR_TOKEN_URL:
                token = f"token-{len(token_requests) + 1}"
                token_requests.append(request)
                return token_response(token)
            graphql_tokens.append(request.headers["Authorization"])
            if len(graphql_tokens) == 1:
                raise http_error(linear.LINEAR_API, 401, "unauthorized")
            return Response({"data": {"users": {"nodes": []}}})

        with tempfile.TemporaryDirectory() as cache_root, self.env(cache_root), mock.patch.object(
            linear.urllib.request, "urlopen", side_effect=urlopen
        ):
            result = linear.gql(linear.USERS_QUERY)

        self.assertEqual(result, {"users": {"nodes": []}})
        self.assertEqual(len(token_requests), 2)
        self.assertEqual(graphql_tokens, ["Bearer token-1", "Bearer token-2"])

    def test_token_endpoint_failure_is_a_sanitized_nonzero_cli_error(self):
        secret = "client-secret-never-print"
        leaked_body = f'{{"access_token":"response-token-never-print","detail":"{secret}"}}'
        with tempfile.TemporaryDirectory() as cache_root, self.env(
            cache_root, LINEAR_CLIENT_SECRET=secret
        ), mock.patch.object(
            linear.urllib.request,
            "urlopen",
            side_effect=http_error(linear.LINEAR_TOKEN_URL, 500, leaked_body),
        ):
            result, stdout, stderr = self.run_main(["users"])

        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Linear token endpoint returned HTTP 500", stderr)
        self.assertNotIn(secret, stderr)
        self.assertNotIn("response-token-never-print", stderr)
        self.assertNotIn(leaked_body, stderr)

    def test_invalid_token_response_is_sanitized(self):
        secret = "secret-in-malformed-body"
        with tempfile.TemporaryDirectory() as cache_root, self.env(
            cache_root, LINEAR_CLIENT_SECRET=secret
        ), mock.patch.object(
            linear.urllib.request, "urlopen", return_value=Response(secret.encode())
        ):
            result, _, stderr = self.run_main(["users"])

        self.assertEqual(result, 1)
        self.assertIn("invalid JSON", stderr)
        self.assertNotIn(secret, stderr)

    def test_repeated_graphql_401_stops_after_one_retry(self):
        token_requests = 0
        graphql_requests = 0

        def urlopen(request, timeout):
            nonlocal token_requests, graphql_requests
            if request.full_url == linear.LINEAR_TOKEN_URL:
                token_requests += 1
                return token_response(f"token-{token_requests}")
            graphql_requests += 1
            raise http_error(linear.LINEAR_API, 401, "secret response body")

        with tempfile.TemporaryDirectory() as cache_root, self.env(cache_root), mock.patch.object(
            linear.urllib.request, "urlopen", side_effect=urlopen
        ):
            with self.assertRaisesRegex(linear.LinearError, "Linear HTTP 401"):
                linear.gql("query Viewer { viewer { id } }")

        self.assertEqual(token_requests, 2)
        self.assertEqual(graphql_requests, 2)

    def test_token_command_explicitly_prints_the_token(self):
        with mock.patch.object(linear, "get_linear_token", return_value="secret-token"):
            result, stdout, stderr = self.run_main(["token"])

        self.assertEqual(result, 0)
        self.assertEqual(stdout, "secret-token\n")
        self.assertEqual(stderr, "")

    def test_graphql_file_and_variables_use_bearer_authenticated_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            query_path = Path(temporary) / "query.graphql"
            variables_path = Path(temporary) / "variables.json"
            query_path.write_text("query Team($id: String!) { team(id: $id) { name } }")
            variables_path.write_text(json.dumps({"id": "synthetic-team"}))

            with self.env(temporary), mock.patch.object(
                linear.urllib.request,
                "urlopen",
                side_effect=[
                    token_response("synthetic-token"),
                    Response({"data": {"team": {"name": "QA"}}}),
                ],
            ) as urlopen:
                result, stdout, stderr = self.run_main(
                    [
                        "graphql",
                        "--file",
                        str(query_path),
                        "--variables-file",
                        str(variables_path),
                    ]
                )

            expected_query = query_path.read_text()

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), {"team": {"name": "QA"}})
        request = urlopen.call_args_list[1].args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["query"], expected_query)
        self.assertEqual(payload["variables"], {"id": "synthetic-team"})
        self.assertEqual(request.headers["Authorization"], "Bearer synthetic-token")

    def test_graphql_reads_query_from_stdin(self):
        query = "# comment\n{ viewer { id } }\n"
        with mock.patch.object(
            linear, "gql", return_value={"viewer": {"id": "viewer-id"}}
        ) as gql:
            result, stdout, stderr = self.run_main(
                ["graphql", "--file", "-"], stdin=query
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), {"viewer": {"id": "viewer-id"}})
        gql.assert_called_once_with(query, {})

    def test_invalid_graphql_inputs_fail_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty_query = root / "empty.graphql"
            malformed_query = root / "malformed.graphql"
            good_query = root / "good.graphql"
            bad_json = root / "bad.json"
            array_json = root / "array.json"
            empty_query.write_text("  \n")
            malformed_query.write_text("this is not GraphQL")
            good_query.write_text("{ viewer { id } }")
            bad_json.write_text("{")
            array_json.write_text("[]")
            cases = (
                (["graphql", "--file", str(root / "missing.graphql")], "cannot read"),
                (["graphql", "--file", str(empty_query)], "empty"),
                (["graphql", "--file", str(malformed_query)], "must start"),
                (
                    [
                        "graphql",
                        "--file",
                        str(good_query),
                        "--variables-file",
                        str(root / "missing.json"),
                    ],
                    "cannot read",
                ),
                (
                    [
                        "graphql",
                        "--file",
                        str(good_query),
                        "--variables-file",
                        str(bad_json),
                    ],
                    "not valid JSON",
                ),
                (
                    [
                        "graphql",
                        "--file",
                        str(good_query),
                        "--variables-file",
                        str(array_json),
                    ],
                    "JSON object",
                ),
            )
            for argv, expected in cases:
                with self.subTest(argv=argv), mock.patch.object(
                    linear.urllib.request, "urlopen"
                ) as urlopen:
                    result, stdout, stderr = self.run_main(argv)
                self.assertEqual(result, 1)
                self.assertEqual(stdout, "")
                self.assertIn(expected, stderr)
                urlopen.assert_not_called()

    def test_version_help_and_copied_executable_are_standalone(self):
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary) / "directory with spaces"
            isolated.mkdir()
            copied = isolated / "standalone linear"
            shutil.copy2(SCRIPT, copied)
            copied.chmod(0o755)
            version = subprocess.run(
                [sys.executable, str(copied), "--version"],
                cwd=isolated,
                text=True,
                capture_output=True,
                check=False,
            )
            help_result = subprocess.run(
                [str(copied), "--help"],
                cwd=isolated,
                text=True,
                capture_output=True,
                check=False,
            )

            variables = isolated / "invalid variables with spaces.json"
            variables.write_text("[]")
            stdin_result = subprocess.run(
                [
                    str(copied),
                    "graphql",
                    "--file",
                    "-",
                    "--variables-file",
                    str(variables),
                ],
                cwd=isolated,
                input="{ viewer { id } }\n",
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout, "linear 0.1.1\n")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in (
            "token",
            "issue",
            "comments",
            "users",
            "set-state",
            "set-description",
            "comment",
            "graphql",
        ):
            self.assertIn(command, help_result.stdout)
        self.assertNotIn("Hermes", help_result.stdout)
        self.assertNotIn("Probe", help_result.stdout)
        self.assertEqual(stdin_result.returncode, 1)
        self.assertEqual(stdin_result.stdout, "")
        self.assertIn("GraphQL variables must be a JSON object", stdin_result.stderr)
        self.assertIn("standalone Linear", linear.__doc__)

    def test_piped_installer_downloads_to_default_home_and_installed_cli_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "fake bin"
            write_fake_curl(fake_bin, successful_curl_body())
            home = root / "home"
            environment = os.environ.copy()
            environment.pop("LINEAR_INSTALL_DIR", None)
            environment.update(
                {
                    "HOME": str(home),
                    "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
                    "FAKE_CURL_SOURCE": str(SCRIPT),
                    "FAKE_CURL_EXPECTED_URL": DOWNLOAD_URL,
                }
            )
            result = subprocess.run(
                ["sh"],
                cwd=root,
                env=environment,
                input=INSTALLER.read_text(),
                text=True,
                capture_output=True,
                check=False,
            )
            target = home / ".local" / "bin" / "linear"

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.is_file())
            self.assertTrue(target.stat().st_mode & stat.S_IXUSR)
            self.assertEqual(target.read_bytes(), SCRIPT.read_bytes())
            version = subprocess.run(
                [str(target), "--version"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout, "linear 0.1.1\n")

    def test_installer_override_handles_spaces_and_download_failure_is_atomic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "fake bin"
            write_fake_curl(fake_bin, successful_curl_body())
            install_dir = root / "installed tools with spaces"
            environment = os.environ.copy()
            environment.update(
                {
                    "LINEAR_INSTALL_DIR": str(install_dir),
                    "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
                    "FAKE_CURL_SOURCE": str(SCRIPT),
                    "FAKE_CURL_EXPECTED_URL": DOWNLOAD_URL,
                }
            )
            result = subprocess.run(
                ["sh"],
                cwd=root,
                env=environment,
                input=INSTALLER.read_text(),
                text=True,
                capture_output=True,
                check=False,
            )
            target = install_dir / "linear"

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_bytes(), SCRIPT.read_bytes())
            self.assertTrue(target.stat().st_mode & stat.S_IXUSR)

            target.write_text("existing installation")
            write_fake_curl(
                fake_bin,
                r'''output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then shift; output=$1; fi
  shift
done
printf '%s' 'partial download' > "$output"
exit 22
''',
            )
            failed = subprocess.run(
                ["sh", str(INSTALLER)],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(target.read_text(), "existing installation")
            self.assertEqual(list(install_dir.glob(".linear.*")), [])

    def test_installer_rejects_directory_target_and_reports_missing_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            no_tools = root / "no tools"
            no_tools.mkdir()
            environment = os.environ.copy()
            environment["PATH"] = str(no_tools)
            missing_curl = subprocess.run(
                ["/bin/sh", str(INSTALLER)],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(missing_curl.returncode, 0)
            self.assertIn("curl is required", missing_curl.stderr)

            fake_bin = root / "old python bin"
            write_fake_curl(fake_bin, "exit 99\n")
            python3 = fake_bin / "python3"
            python3.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = \"--version\" ]; then echo 'Python 3.9.18'; fi\n"
                "exit 1\n"
            )
            python3.chmod(0o755)
            environment["PATH"] = str(fake_bin)
            old_python = subprocess.run(
                ["/bin/sh", str(INSTALLER)],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(old_python.returncode, 0)
            self.assertIn("Python 3.10 or newer is required", old_python.stderr)
            self.assertIn("Python 3.9.18", old_python.stderr)

            install_dir = root / "directory target install"
            target = install_dir / "linear"
            target.mkdir(parents=True)
            current_python_bin = root / "current python bin"
            write_fake_curl(current_python_bin, successful_curl_body())
            environment.update(
                {
                    "LINEAR_INSTALL_DIR": str(install_dir),
                    "PATH": str(current_python_bin)
                    + os.pathsep
                    + os.environ["PATH"],
                    "FAKE_CURL_SOURCE": str(SCRIPT),
                    "FAKE_CURL_EXPECTED_URL": DOWNLOAD_URL,
                }
            )
            directory_target = subprocess.run(
                ["sh", str(INSTALLER)],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(directory_target.returncode, 0)
            self.assertIn("install target is a directory", directory_target.stderr)
            self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
