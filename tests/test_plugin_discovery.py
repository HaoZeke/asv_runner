# External benchmark plugins (asv_bench_* distributions): name
# normalisation and reporting of plugins that fail to import.

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LIST_TYPES = (
    "import json, asv_runner.benchmarks as b; "
    "print(json.dumps([t.__name__ for t in b.benchmark_types]))"
)


def _make_plugin(site, dist_name, package, benchmark_source):
    dist_info = site / f"{package}-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: 1.0\n"
    )
    bench_dir = site / package / "benchmarks"
    bench_dir.mkdir(parents=True)
    (site / package / "__init__.py").write_text("")
    (bench_dir / "__init__.py").write_text("")
    (bench_dir / "plugin.py").write_text(textwrap.dedent(benchmark_source))


def _exported(class_name):
    return f"""
    class {class_name}:
        pass

    export_as_benchmark = [{class_name}]
    """


class TestPluginDiscovery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.site = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _list_types(self, **env_vars):
        env = dict(os.environ)
        env.pop("ASV_RUNNER_STRICT_PLUGINS", None)
        env.update(env_vars)
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(self.site)])
        return subprocess.run(
            [sys.executable, "-c", LIST_TYPES],
            cwd=str(self.site),
            env=env,
            capture_output=True,
            text=True,
        )

    def test_non_normalized_dist_names_are_loaded(self):
        _make_plugin(self.site, "asv-bench-dash", "asv_bench_dash", _exported("Dash"))
        _make_plugin(
            self.site, "Asv.Bench_Dotted", "asv_bench_dotted", _exported("Dotted")
        )

        proc = self._list_types()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        types = json.loads(proc.stdout)
        self.assertIn("TimeBenchmark", types)
        self.assertIn("Dash", types)
        self.assertIn("Dotted", types)
        self.assertEqual(proc.stderr, "")

    def test_import_error_is_reported(self):
        _make_plugin(
            self.site,
            "asv_bench_broken",
            "asv_bench_broken",
            "import does_not_exist_xyz\n",
        )
        _make_plugin(self.site, "asv_bench_ok", "asv_bench_ok", _exported("Ok"))

        proc = self._list_types()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        types = json.loads(proc.stdout)
        self.assertIn("TimeBenchmark", types)
        self.assertIn("Ok", types)
        self.assertIn("asv_bench_broken", proc.stderr)
        self.assertIn("does_not_exist_xyz", proc.stderr)

    def test_import_error_raises_when_strict(self):
        _make_plugin(
            self.site,
            "asv_bench_broken",
            "asv_bench_broken",
            "import does_not_exist_xyz\n",
        )

        proc = self._list_types(ASV_RUNNER_STRICT_PLUGINS="1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ModuleNotFoundError", proc.stderr)
        self.assertIn("does_not_exist_xyz", proc.stderr)

    def test_not_required_is_silent(self):
        _make_plugin(
            self.site,
            "asv_bench_optional",
            "asv_bench_optional",
            """
            from asv_runner.benchmarks._exceptions import NotRequired

            raise NotRequired("optional dependency missing")
            """,
        )

        for strict in ("", "1"):
            proc = self._list_types(ASV_RUNNER_STRICT_PLUGINS=strict)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("TimeBenchmark", json.loads(proc.stdout))
            self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
