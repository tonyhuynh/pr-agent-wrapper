"""Pinned upstream functions with stubbed dependencies; no provider transport."""
import ast
import argparse
import asyncio
from contextvars import ContextVar
from contextlib import redirect_stderr
import enum
import json
import io
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import types
import unittest
from unittest.mock import Mock, patch

from dynaconf import Dynaconf
from tenacity import retry, retry_if_exception, stop_after_attempt

from support import chains, functions, inputs, review, upstream


class APIError(Exception): pass
class APITimeoutError(APIError): pass
class RateLimitError(APIError): pass
class BadRequestError(APIError): pass
class UnprocessableEntityError(APIError): pass
class AuthenticationError(APIError): pass
class PermissionDeniedError(APIError): pass
class EmptyTruncatedResponseError(APIError): pass
class FallbackEligibleError(Exception): pass

OPENAI = types.SimpleNamespace(**{c.__name__: c for c in [APIError, APITimeoutError, RateLimitError, BadRequestError,
                                                         UnprocessableEntityError, AuthenticationError, PermissionDeniedError]})


class ModelType(enum.Enum):
    REGULAR = 1
    WEAK = 2
    REASONING = 3


def config_scope(config, extra_env=None):
    """Use the pinned loader, file defaults, and Dynaconf env loader together."""
    class Scope:
        def __enter__(self):
            self.temp = tempfile.TemporaryDirectory()
            root = Path(self.temp.name)
            package = types.ModuleType("pr_agent")
            package.__path__ = []
            log = types.ModuleType("pr_agent.log")
            log.get_logger = lambda: Mock()
            loader = types.ModuleType("pr_agent.custom_merge_loader")
            context = types.ModuleType("starlette_context")
            context.context = {}
            self.modules = patch.dict(sys.modules, {"pr_agent": package, "pr_agent.log": log,
                                                   "pr_agent.custom_merge_loader": loader, "starlette_context": context})
            self.modules.start()
            exec(compile(upstream("pr_agent/custom_merge_loader.py"), "custom_merge_loader.py", "exec"), loader.__dict__)
            (root / "settings").mkdir()
            for name in ["configuration.toml", "ignore.toml"]:
                (root / "settings" / name).write_text(upstream("pr_agent/settings/" + name))
            self.env = patch.dict(os.environ, {**review.serialize(config), **(extra_env or {})}, clear=True)
            self.env.start()
            namespace = {"__file__": str(root / "config_loader.py")}
            exec(compile(upstream("pr_agent/config_loader.py"), "config_loader.py", "exec"), namespace)
            self.settings = namespace["get_settings"]()
            return self.settings
        def __exit__(self, *args):
            self.env.stop()
            self.modules.stop()
            self.temp.cleanup()
    return Scope()


class ConfigSemanticsTests(unittest.TestCase):
    def test_real_pinned_loader_replaces_primary_and_fallback_defaults(self):
        tuned = {"api_base": "https://llm.example.com/v1", "custom_model_max_tokens": 128000, "max_model_tokens": 64000,
                 "reasoning_effort": "high", "response_language": "pt-BR", "ai_timeout": 300}
        for fallbacks, extra in [([], {}), (["openai/a", "anthropic/b", "gemini/c"], {}), (["openai/a", "openrouter/x-ai/grok-4.6:free"], tuned)]:
            config = review.settings(inputs(fallback_models=json.dumps(fallbacks), extra_instructions='Check "package.json"\nthen docs; skip @Override and a@b.co.', **extra))
            self.assertEqual("openai.api_base" in config, bool(extra))
            with config_scope(config) as settings:
                for key, value in config.items():
                    self.assertEqual(settings.get(key), value, key)
                self.assertEqual(settings.config.fallback_models, fallbacks)
                self.assertEqual(settings.ignore.glob, ["vendor/**"])
                self.assertEqual(settings.config.model, "xai/grok-4.7")
                self.assertFalse(settings.config.get("use_repo_settings_file"))

    def test_settings_keep_their_types_when_dynaconf_skips_converters(self):
        # PR-Agent's logger reads settings mid-load; Dynaconf then skips "@" converters, as it does here.
        config = review.settings(inputs(fallback_models='["openai/a"]', extra_instructions='say "hi"\nplease'))
        with config_scope(config, {"AUTO_CAST_FOR_DYNACONF": "false"}) as settings:
            for key, value in config.items():
                self.assertEqual(settings.get(key), value, key)
                self.assertIsInstance(settings.get(key), type(value), key)
        keys = {"XAI_API_KEY": "test-only-xai", "OPENAI_API_KEY": "123"}
        with patch.dict(os.environ, {**review.provider_keys(config, keys), "AUTO_CAST_FOR_DYNACONF": "false"}, clear=True):
            settings = Dynaconf(envvar_prefix=False, load_dotenv=False, settings_files=[], merge_enabled=False)
            self.assertEqual((settings.get("XAI.KEY"), settings.get("OPENAI.KEY")), ("test-only-xai", "123"))

    def test_key_mapping_real_env_loader(self):
        keys = {public: "test-only-" + provider for provider, (public, _) in review.KEYS.items()}
        seen = set()
        for group, chain in chains():
            with patch.dict(os.environ, review.provider_keys(review.settings(chain), keys), clear=True):
                settings = Dynaconf(envvar_prefix=False, load_dotenv=False, settings_files=[], merge_enabled=False)
                for provider, (public, native) in review.KEYS.items():
                    self.assertEqual(settings.get(native.replace("__", ".")), keys[public] if provider in group else None)
                self.assertIsNone(settings.get("OPENAI.API_BASE"))
            seen.update(group)
        self.assertEqual(seen, set(review.KEYS))
        tree = ast.parse(upstream("pr_agent/algo/ai_handlers/litellm_ai_handler.py"))
        provider_paths = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == "PROVIDER_SETTING_PATHS" for t in n.targets))
        for provider in set(review.KEYS) - {"openai"}:
            self.assertEqual(provider_paths[provider]["api_key"], review.KEYS[provider][1].replace("__", "."))
        self.assertIn('configured_api_base = settings.get("OPENAI.API_BASE", None)', upstream("pr_agent/algo/ai_handlers/litellm_ai_handler.py"))
        self.assertIn('"api_key": settings.get("OPENAI.KEY", None)', upstream("pr_agent/algo/ai_handlers/litellm_ai_handler.py"))

    def test_wrapped_evaluators_are_rejected_before_dynaconf(self):
        # These expressions perform arithmetic only. They demonstrate why wrappers
        # reject directive forms even when callers request string or JSON casting.
        for payload in ["@jinja {{ 7 * 7 }}", "@str @jinja {{ 7 * 7 }}", "@json @jinja {{ 7 * 7 }}", "@format {env[PRW_TEST_VALUE]}"]:
            with patch.dict(os.environ, {"PRW_TEST_VALUE": "49", "TEXT": payload}, clear=True):
                settings = Dynaconf(envvar_prefix=False, load_dotenv=False, settings_files=[])
                self.assertEqual(str(settings.get("TEXT")), "49")
            with self.assertRaises(ValueError):
                review.settings(inputs(extra_instructions=payload))

    def test_failure_reasons_match_pinned_source(self):
        values = {}
        for node in ast.parse(upstream("pr_agent/tools/pr_reviewer.py")).body:
            if isinstance(node, ast.Assign) and node.targets[0].id in {"_REVIEW_FAILURE_REASONS", "_UNKNOWN_REVIEW_FAILURE_REASON"}:
                values[node.targets[0].id] = ast.literal_eval(node.value)
        reasons = {reason for _, reason in values["_REVIEW_FAILURE_REASONS"]} | {values["_UNKNOWN_REVIEW_FAILURE_REASON"]}
        self.assertEqual(set(review.FAILURE_REASONS), reasons)

    def test_all_fixed_keys_exist_in_pinned_source(self):
        defaults = tomllib.loads(upstream("pr_agent/settings/configuration.toml"))
        handler = upstream("pr_agent/algo/ai_handlers/litellm_ai_handler.py")
        for key in review.FIXED:
            section, field = key.split(".")
            if key == "config.num_retries":
                self.assertIn('get("num_retries", None)', handler)
            else:
                self.assertIn(field, defaults[section], key)
        tuned = review.settings(inputs(model="openai/a", api_base="https://llm.example.com/v1", custom_model_max_tokens=128000, reasoning_effort="low"))
        for key in set(tuned) - set(review.FIXED) - {"ignore.glob", "openai.api_base"}:
            section, field = key.split(".")
            self.assertIn(field, defaults[section], key)
        self.assertIn(defaults["config"]["reasoning_effort"], review.REASONING_EFFORTS)
        self.assertEqual((defaults["config"]["ai_timeout"], defaults["config"]["max_model_tokens"], defaults["config"]["response_language"]), (120, 32000, "en-US"))
        self.assertEqual(defaults["config"]["model"], "gpt-6.1-sol")
        self.assertEqual(defaults["config"]["fallback_models"], ["gpt-6-luna"])

    def test_pinned_entrypoint_flags_plain_diff_and_footer_contract(self):
        cli = upstream("pr_agent/cli.py")
        dockerfile = upstream("docker/Dockerfile")
        plain = upstream("pr_agent/git_providers/plain_diff_provider.py")
        self.assertIn('ENTRYPOINT ["python", "pr_agent/cli.py"]', dockerfile)
        self.assertIn("FROM base AS cli", dockerfile)
        for flag in ['"--diff-file"', '"--output"']:
            self.assertIn(flag, cli)
        self.assertIn('"plain-diff"', cli)
        self.assertIn("if there is no detectable .git root", plain.lower())
        calls = types.SimpleNamespace(model_used="openai/a", models_used=["openai/a"], fallback_used=True,
                                      has_token_usage=False, has_cache_usage=False, duration_seconds=1.0, num_ai_calls=1)
        namespace = functions("pr_agent/algo/run_output.py", ["show_run_details"],
                              {"get_run_details": lambda: calls,
                               "get_settings": lambda: types.SimpleNamespace(get=lambda key, default=False: default)})
        rendered = namespace["show_run_details"](False)
        config = review.settings(inputs(fallback_models='["openai/a"]'))
        self.assertEqual(review.classify(rendered.encode(), config), "success")
        self.assertIn("- Model: openai/a (fallback)", rendered)

    def test_actual_cli_parser_accepts_the_runtime_arguments(self):
        tree = ast.parse(upstream("pr_agent/cli.py"))
        names = {"_PLAIN_DIFF_MARKDOWN_COMMANDS", "_JSON_REVIEW_COMMANDS", "_JSON_IMPROVE_COMMANDS",
                 "_JSON_DESCRIBE_COMMANDS", "_OUTPUT_OPTIONS"}
        assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
        # set_parser builds help text for all three commands before parsing review.
        namespace = {"argparse": argparse, "commands": ["review"], "DOCTOR_COMMANDS": [],
                     "COMMAND_DESCRIPTIONS": {"review": "Review", "describe": "Describe", "improve": "Improve"},
                     "get_version": lambda: "0.49.0", "os": os}
        exec(compile(ast.Module(body=assignments, type_ignores=[]), "native-cli-constants", "exec"), namespace)
        functions("pr_agent/cli.py", ["set_parser", "_resolve_output_option", "_validate_output_options"], namespace)
        parser = namespace["set_parser"]()
        args = parser.parse_args(review.docker_command(Path("/tmp/example"), set())[-5:])
        namespace["_validate_output_options"](parser, args, True)
        self.assertEqual((args.diff_file, args.output, args.command, args.pr_url),
                         ("/input/review.diff", "/output/review.md", "review", None))
        wrong = parser.parse_args(["--diff-file", "/input/review.diff", "review", "--output", "/output/review.md"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as misplaced_output:
            namespace["_validate_output_options"](parser, wrong, True)
        self.assertEqual(misplaced_output.exception.code, 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as unknown_command:
            parser.parse_args(["--diff-file", "/input/review.diff", "unknown-command"])
        self.assertEqual(unknown_command.exception.code, 2)


class NativeFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope = config_scope(review.settings(inputs(fallback_models='["openai/a","anthropic/b","gemini/c"]')))
        self.settings = self.scope.__enter__()
        self.records = []
        self.context = ContextVar("test_chain", default=None)
        self.namespace = functions("pr_agent/algo/pr_processing.py",
                                   ["retry_with_fallback_models", "_get_all_models", "_get_all_deployments"],
                                   {"asyncio": asyncio, "openai": OPENAI, "FallbackEligibleError": FallbackEligibleError,
                                    "ModelType": ModelType, "get_settings": lambda: self.settings,
                                    "route_primary_model": lambda *args: None, "_effective_fallback_chain": self.context,
                                    "get_logger": lambda: Mock(), "record_model_used": lambda model, is_fallback: self.records.append((model, is_fallback))})
        self.handler = functions("pr_agent/algo/ai_handlers/litellm_ai_handler.py",
                                 ["_as_bool", "_configured_client_retries", "_should_retry_same_model"],
                                 {"openai": OPENAI, "EmptyTruncatedResponseError": EmptyTruncatedResponseError,
                                  "get_settings": lambda: self.settings, "get_logger": lambda: Mock()})

    async def asyncTearDown(self):
        self.scope.__exit__()

    async def test_ordered_cross_provider_fallback_uses_real_upstream_loop(self):
        attempted = []
        async def inference(model):
            attempted.append(model)
            if model != "gemini/c": raise APIError("stubbed failure")
            return "stubbed result"
        value = await self.namespace["retry_with_fallback_models"](inference)
        self.assertEqual(value, "stubbed result")
        self.assertEqual(attempted, ["xai/grok-4.7", "openai/a", "anthropic/b", "gemini/c"])
        self.assertEqual(self.records, [("gemini/c", True)])
        self.assertIsNone(self.context.get())
        self.assertIsNone(self.settings.get("openai.deployment_id"))

    async def test_primary_success_stops_the_chain(self):
        calls = []
        async def inference(model):
            calls.append(model)
            return "ok"
        await self.namespace["retry_with_fallback_models"](inference)
        self.assertEqual(calls, ["xai/grok-4.7"])
        self.assertEqual(self.records, [("xai/grok-4.7", False)])

    async def test_primary_only_native_loop_makes_one_inference_call(self):
        self.settings.set("config.fallback_models", [])
        calls = []
        async def inference(model):
            calls.append(model)
            return "primary result"
        result = await self.namespace["retry_with_fallback_models"](inference)
        self.assertEqual(result, "primary result")
        self.assertEqual(calls, ["xai/grok-4.7"])
        self.assertEqual(self.records, [("xai/grok-4.7", False)])

    async def test_only_native_eligible_exceptions_fall_back(self):
        for error in [APIError, asyncio.TimeoutError, FallbackEligibleError, ValueError, RuntimeError]:
            calls = []
            async def inference(model):
                calls.append(model)
                if len(calls) == 1: raise error("stubbed")
                return "ok"
            if error in [ValueError, RuntimeError]:
                with self.assertRaises(error): await self.namespace["retry_with_fallback_models"](inference)
                self.assertEqual(len(calls), 1)
            else:
                await self.namespace["retry_with_fallback_models"](inference)
                self.assertEqual(calls, ["xai/grok-4.7", "openai/a"])
            self.assertIsNone(self.context.get())

    async def test_exhausted_chain_restores_deployment_and_fails(self):
        self.settings.set("openai.deployment_id", "original")
        async def inference(model): raise APIError("stubbed")
        with self.assertRaisesRegex(Exception, "Failed to generate prediction with any model"):
            await self.namespace["retry_with_fallback_models"](inference)
        self.assertEqual(self.settings.get("openai.deployment_id"), "original")
        self.assertIsNone(self.context.get())

    async def test_native_retry_predicate_and_decorator_with_stubbed_body(self):
        self.assertEqual(self.handler["_configured_client_retries"](), 0)
        predicate = self.handler["_should_retry_same_model"]
        self.assertTrue(predicate(APIError()))
        for error in [APITimeoutError, RateLimitError, BadRequestError, UnprocessableEntityError,
                      AuthenticationError, PermissionDeniedError, EmptyTruncatedResponseError, ValueError]:
            self.assertFalse(predicate(error()), error)
        tree = ast.parse(upstream("pr_agent/algo/ai_handlers/litellm_ai_handler.py"))
        decorator = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_chat_completion_with_retry").decorator_list[0]
        constant = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MODEL_RETRIES" for t in n.targets))
        namespace = {"retry": retry, "retry_if_exception": retry_if_exception, "stop_after_attempt": stop_after_attempt,
                     "_should_retry_same_model": predicate}
        exec(compile(ast.Module(body=[constant], type_ignores=[]), "native-retry-constant", "exec"), namespace)
        self.assertEqual(namespace["MODEL_RETRIES"], 2)
        decorate = eval(compile(ast.Expression(decorator), "native-retry-decorator", "eval"), namespace)
        for error, expected_count in [(APIError, 2), (APITimeoutError, 1), (BadRequestError, 1)]:
            count = 0
            async def stub():
                nonlocal count
                count += 1
                raise error()
            with self.assertRaises(error): await decorate(stub)()
            self.assertEqual(count, expected_count)
