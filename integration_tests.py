#!/usr/bin/env python3
"""
Usage:
    ./integration_tests.py
    ./integration_tests.py <patterns>...

Options:
    -h, --help               Print this help information.


Because a large part of this crate's functionality depends on generated code,
it's easier to test functionality from the point-of-view of an end user.
Therefore, a large proportion of the crate's tests are orchestrated by a
Python script.

For each `*.rs` file in the `integration_tests/` directory, this Python script
will:

- Create a new `--bin` crate in a temporary directory
- Copy the `*.rs` file into this new crate and rename it to `main.rs`.
- Scan the `*.rs` file for a **special** pattern indicating which asset
  directory will be included (relative to this crate's root directory). If the
  pattern isn't found, use this crate's directory (ignoring ".git" and "target").
- Generate a `build.rs` file which will compile in the specified file tree.
- Compile and run the new binary test crate.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import logging
import shutil
import re
import time
from concurrent.futures import ThreadPoolExecutor

from docopt import docopt

DEBUG = False

project_root = Path(os.path.abspath(__file__)).parent

logging.basicConfig(format='%(asctime)s %(levelname)6s: %(message)s', 
                    datefmt='%m/%d/%Y %I:%M:%S %p',
                    level=logging.DEBUG if DEBUG else logging.INFO)

BUILD_RS_TEMPLATE = """
extern crate include_dir;

use std::env;
use std::path::Path;
use include_dir::include_dir;

fn main() {{
    let outdir = env::var("OUT_DIR").unwrap();
    let dest_path = Path::new(&outdir).join("assets.rs");

    include_dir("{}")
        .as_variable("ASSETS")
        .ignore(".git")
        .ignore("target")
        .to_file(dest_path)
        .unwrap();
    }}
"""

CARGO_TOML_TEMPLATE = """
[package]
authors = ["Michael-F-Bryan <michaelfbryan@gmail.com>"]
name = "{}"
version = "0.1.0"

[build-dependencies]
include_dir = {{path = "{}"}}
"""

def pretty_print_output(name, output):
    logging.warning("return code: %d", output.returncode)
    if output.stdout:
        logging.warn("stdout:")
        for line in output.stdout.decode().split("\n"):
            logging.warning("(%s) %s", name, line)

    if output.stderr:
        logging.warning("stderr:")
        for line in output.stderr.decode().split("\n"):
            logging.warning("(%s) %s", name, line)


class IntegrationTest:
    """
    A runner for an integration test.
    """
    def __init__(self, filename):
        self.script = filename.relative_to(project_root)
        self.name = self.script.stem
        self.temp_dir = tempfile.TemporaryDirectory(prefix="include_dir_test-")
        self.crate = None

    def initialize(self):
        """
        Do all the work necessary to create a new project, copy across the test
        file, add a build script, then adjust Cargo.toml accordingly.
        """
        logging.debug("(%s) Initializing test crate in %s", self.name, self.temp_dir.name)
        crate_name = self.name

        cmd = ["cargo", "new", "--bin", crate_name]
        if DEBUG:
            cmd.append("--verbose")

        output = subprocess.run(cmd,
                                cwd=self.temp_dir.name,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

        if output.returncode != 0:
            logging.error("Unable to create a new crate")
            pretty_print_output(self.name, output)
            return

        self.crate = Path(self.temp_dir.name) / crate_name

        shutil.copy(self.script, self.crate / "src" / "main.rs")

        self._generate_build_rs()
        self._update_cargo_toml()
        self._copy_across_cache()

    def run(self):
        """
        Run the test, checking whether it passes or fails (non-zero exit code).
        """
        logging.info('Running test "%s"', self.name)

        cmd = ["cargo", "run"]

        if DEBUG:
            cmd.append("--verbose")

        output = subprocess.run(cmd,
                                cwd=self.crate,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

        if output.returncode != 0:
            logging.error('%-20s\t✘', self.name)
            pretty_print_output(self.name, output)
        else:
            logging.info('%-20s\t✔', self.name)


    def _generate_build_rs(self):
        asset_dir = self._assets_to_embed()
        logging.debug("(%s) Generating build.rs (assets: %s)", self.name, asset_dir)

        build_rs = self.crate / "build.rs"

        with open(build_rs, "w") as f:
            f.write(BUILD_RS_TEMPLATE.format(asset_dir))

    def _update_cargo_toml(self):
        logging.debug("(%s) Updating Cargo.toml", self.name)
        cargo_toml = self.crate / "Cargo.toml"

        with open(cargo_toml, "w") as f:
            f.write(CARGO_TOML_TEMPLATE.format(self.name, project_root))

    def _assets_to_embed(self):
        # Search for the "special" pattern -> include_dir!("path/to/assets")
        pattern = re.compile(r'include_dir!\("([\w\d./]+)"\)')

        with open(self.script) as f:
            got = pattern.search(f.read())
            if got is None:
                return project_root  / "src"
            else:
                return Path(os.path.abspath(got.groups(1)))

    def _copy_across_cache(self):
        logging.debug('(%s) Copying across "target/" dir', self.name)
        try:
            # os.symlink(project_root / "target", self.crate / "target")
            shutil.copytree(project_root / "target", self.crate / "target")
        except:
            pass

    def __repr__(self):
        return '<{}: filename="{}">'.format(
            self.__class__.__name__,
            self.name)


def discover_integration_tests(patterns):
    # Use a reasonable default if no patterns provided
    if len(patterns) == 0:
        patterns = ['*.rs']

    test_dir = project_root / "integration_tests"
    filenames = set()

    for pattern in patterns:
        matches = test_dir.glob(pattern)
        for match in matches:
            filenames.add(match)

    return [IntegrationTest(name) for name in filenames]


def run_test(test):
    test.initialize()
    test.run()

def main(args):
    patterns = args["<patterns>"]
    tests = list(discover_integration_tests(patterns))

    if len(tests) == 0:
        logging.warning("No tests match the provided pattern")

    # for test in tests:
    #     run_test(test)
    with ThreadPoolExecutor() as pool:
        pool.map(run_test, tests)

if __name__ == "__main__":
    options = docopt(__doc__)
    main(options)

