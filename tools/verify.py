#!/usr/bin/env python3
"""Complete Windows x64 Performance API verification using Python's standard library."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
    import winreg


class VerificationError(RuntimeError):
    """A failed prerequisite, command, or verification assertion."""


@dataclass
class CaptureRecord:
    label: str
    trace: Path
    session: Path
    pid: int
    main_thread: int
    worker_thread: int


@dataclass
class PhaseResult:
    label: str
    status: str
    detail: str = ""


@dataclass
class Config:
    level: str
    repo: Path
    odin: Path
    odinfmt: Path
    sdk: Path | None
    profiler: Path | None
    sudo: Path | None
    developer_setup: Path | None
    self: Path
    run: Path
    command_count: int = 0
    captures: list[CaptureRecord] = field(default_factory=list)
    capture_checker: Path | None = None
    results: list[PhaseResult] = field(default_factory=list)


def write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def copy_file(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)


def new_cell(c: Config, name: str) -> Path:
    cell = c.run / name
    cell.mkdir()
    return cell


def get_sdk_binary(c: Config, architecture: str, mode: str) -> Path:
    if c.sdk is None:
        raise VerificationError("This verification phase requires the Performance API SDK.")
    directory = "dll" if mode == "dynamic" else "lib"
    name = "PerformanceAPI.dll" if mode == "dynamic" else "PerformanceAPI_MT.lib"
    path = c.sdk / directory / architecture / name
    if not path.is_file():
        raise VerificationError(f"Missing SDK binary: {path}; check --sdk-root / SUPERLUMINAL_SDK.")
    return path.resolve()


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        absolute = path.resolve()
        unique.setdefault(os.path.normcase(str(absolute)), absolute)
    return list(unique.values())


def resolve_executable(value: str | None, environment: str, basename: str,
                       option: str, repo: Path, *, siblings: bool = False,
                       adjacent: Sequence[Path] = ()) -> Path:
    supplied = value or os.environ.get(environment)
    if supplied:
        found = shutil.which(supplied)
        if not found:
            raise VerificationError(f"Invalid {option} / {environment}: {supplied}")
        return Path(found).resolve()
    found = shutil.which(basename)
    if found:
        return Path(found).resolve()
    candidates = [path for path in adjacent if path.is_file()]
    if siblings:
        candidates.extend(directory / basename for directory in repo.parent.iterdir()
                          if directory.is_dir() and (directory / basename).is_file())
    candidates = _unique_paths(candidates)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise VerificationError(f"Ambiguous {option}; supply an explicit path: "
                                + "; ".join(map(str, candidates)))
    raise VerificationError(f"Missing {basename}; supply {option} or {environment}.")


def _as_sdk_root(candidate: str | Path | None) -> Path | None:
    if not candidate:
        return None
    for suffix in ("", "API", "Performance/API", "Superluminal/Performance/API"):
        root = Path(candidate) / suffix
        if (root / "include/Superluminal/PerformanceAPI_capi.h").is_file():
            return root.resolve()
    return None


def _installed_sdk_roots() -> Iterator[Path]:
    locations = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, name in locations:
        try:
            parent = winreg.OpenKey(hive, name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            continue
        with parent:
            for index in range(winreg.QueryInfoKey(parent)[0]):
                try:
                    child_name = winreg.EnumKey(parent, index)
                    with winreg.OpenKey(parent, child_name) as child:
                        display_name = winreg.QueryValueEx(child, "DisplayName")[0]
                        if not isinstance(display_name, str) or "superluminal" not in display_name.casefold():
                            continue
                        location = winreg.QueryValueEx(child, "InstallLocation")[0]
                        if isinstance(location, str):
                            root = _as_sdk_root(location)
                            if root:
                                yield root
                except OSError:
                    continue
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(variable)
        if value:
            root = _as_sdk_root(Path(value) / "Superluminal/Performance/API")
            if root:
                yield root


def resolve_sdk(value: str | None) -> Path:
    supplied = value or os.environ.get("SUPERLUMINAL_SDK")
    if supplied:
        root = _as_sdk_root(supplied)
        if root is None:
            raise VerificationError("Invalid --sdk-root / SUPERLUMINAL_SDK: expected a Performance API installation.")
        return root
    candidates = _unique_paths(_installed_sdk_roots())
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise VerificationError("Multiple SDKs found; supply --sdk-root: " + "; ".join(map(str, candidates)))
    raise VerificationError("Missing Performance API SDK; supply --sdk-root or SUPERLUMINAL_SDK.")


def resolve_developer_setup(value: str | None) -> Path:
    if value:
        path = Path(value)
        if not path.is_file():
            raise VerificationError(f"Invalid --vcvarsall: {value}")
        return path.resolve()
    compiler, dumpbin = shutil.which("cl.exe"), shutil.which("dumpbin.exe")
    if not compiler or not dumpbin:
        raise VerificationError("Runtime/Capture requires x64 MSVC tools and Windows SDK. "
                                "Run in a developer shell or supply --vcvarsall.")
    for directory in Path(compiler).resolve().parents:
        candidate = directory / "Auxiliary/Build/vcvarsall.bat"
        if candidate.is_file():
            return candidate
    raise VerificationError("Cannot locate vcvarsall.bat from cl.exe; supply --vcvarsall.")


def configure(args: argparse.Namespace) -> Config:
    script = Path(__file__).resolve()
    repo = script.parent.parent
    c = Config(
        level=args.level, repo=repo,
        odin=resolve_executable(args.odin, "ODIN", "odin.exe", "--odin", repo, siblings=True),
        odinfmt=resolve_executable(args.odinfmt, "ODINFMT", "odinfmt.exe", "--odinfmt", repo, siblings=True),
        sdk=None, profiler=None, sudo=None, developer_setup=None, self=script, run=Path(),
    )
    if args.level != "Contracts":
        c.sdk = resolve_sdk(args.sdk_root)
        get_sdk_binary(c, "x64", "dynamic")
        get_sdk_binary(c, "x64", "static")
        # This binary is only rejected by an x64 process, never executed.
        get_sdk_binary(c, "x86", "dynamic")
        c.developer_setup = resolve_developer_setup(args.vcvarsall)
    if args.level == "Capture":
        assert c.sdk is not None
        c.profiler = resolve_executable(
            args.superluminal_cmd, "SUPERLUMINAL_CMD", "SuperluminalCmd.exe",
            "--superluminal-cmd", repo,
            adjacent=(c.sdk.parent / "SuperluminalCmd.exe", c.sdk.parent.parent / "SuperluminalCmd.exe"),
        )
        sudo = shutil.which("sudo.exe")
        if not sudo:
            raise VerificationError("Capture requires Windows sudo.exe; enable Windows sudo.")
        c.sudo = Path(sudo).resolve()
    return c


def phase(c: Config, label: str, action: Callable[[], object]) -> bool:
    print(f"\n=== {label} ===", flush=True)
    try:
        action()
    except ElevationCancelled:
        raise
    except (VerificationError, OSError) as error:
        c.results.append(PhaseResult(label, "FAIL", str(error)))
        print(f"FAIL: {label}: {error}", flush=True)
        return False
    c.results.append(PhaseResult(label, "PASS"))
    print(f"PASS: {label}", flush=True)
    return True


def skip(c: Config, label: str, reason: str) -> None:
    c.results.append(PhaseResult(label, "SKIP", reason))
    print(f"SKIP: {label}: {reason}", flush=True)


def _build_and_run(c: Config, cell: Path, source_repo: Path, mode: str,
                   module: str, profile: Sequence[str], *, disabled: bool = False,
                   dependencies: bool = False) -> Path:
    executable = build_smoke(c, source_repo, cell, mode, module, profile, disabled=disabled)
    dll = get_sdk_binary(c, "x64", "dynamic") if not disabled and mode == "dynamic" else None
    run_smoke(c, cell, executable, mode, dll)
    if dependencies:
        assert_dependencies(c, cell, executable)
    return executable


def _inspect_archive(c: Config, cell: Path) -> None:
    archive = get_sdk_binary(c, "x64", "static")
    output = invoke_msvc(c, cell, f'dumpbin.exe /nologo /directives "{archive}"')
    if not re.search(r'DEFAULTLIB:[" ]*LIBCMT(?:\.lib)?(?:["\s]|$)', output, re.IGNORECASE):
        raise VerificationError("Expected LIBCMT archive directive missing for MT.")
    dll = get_sdk_binary(c, "x64", "dynamic")
    invoke_msvc(c, cell, f'dumpbin.exe /nologo /dependents "{dll}"')


def _copy_published_sources(c: Config, destination: Path) -> None:
    destination.mkdir()
    for directory, subdirectories, files in os.walk(c.repo):
        base = Path(directory)
        subdirectories[:] = [name for name in subdirectories
                            if name not in {".git", ".venv", "__pycache__"} and (base / name).resolve() != c.run]
        for name in files:
            source = base / name
            if source.suffix.lower() not in {".odin", ".py", ".cpp", ".md"} and name != "LICENSE":
                continue
            target = destination / source.relative_to(c.repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_file(source, target)


def _review_captures(c: Config) -> None:
    if {capture.label for capture in c.captures} != {"x64/static", "x64/dynamic"}:
        raise VerificationError("Both x64 capture configurations are required for inspection.")
    cell = new_cell(c, "capture-inspection")
    manifest = cell / "captures.json"
    write_text(manifest, json.dumps([
        {"label":capture.label, "session":str(capture.session), "pid":capture.pid,
         "main_thread":capture.main_thread, "worker_thread":capture.worker_thread}
        for capture in c.captures
    ], ensure_ascii=False))
    # The CLI daemon must survive individual queries. One worker/job owns the
    # entire sequential inspection, rather than killing the daemon per command.
    run_checked(c, cell, [sys.executable, "-X", "utf8", c.self,
                         "--internal-inspect-captures", c.profiler, manifest],
                expected="All resolved captures passed CLI inspection.")


class _CaptureCLI:
    def __init__(self, executable: Path, cell: Path) -> None:
        self.executable = executable
        self.cell = cell
        self.command_count = 0

    def call(self, *arguments: object) -> dict:
        self.command_count += 1
        argv = [str(self.executable), "llm", *map(str, arguments)]
        log = self.cell / f"inspection-{self.command_count}.log"
        print(subprocess.list2cmdline(argv), flush=True)
        # Inherit the inspection worker's job; never create a per-client job.
        with log.open("wb") as output:
            try:
                result = subprocess.run(argv, cwd=self.cell, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=subprocess.STDOUT, timeout=900)
            except subprocess.TimeoutExpired as error:
                raise VerificationError(f"CLI inspection timed out; log: {log}") from error
        text = _process_read_log(log)
        if result.returncode:
            raise VerificationError(f"CLI inspection exit={result.returncode}; {text}; log: {log}")
        try:
            response = json.loads(text)
        except ValueError as error:
            raise VerificationError(f"CLI inspection returned invalid JSON; log: {log}") from error
        if response.get("Success") is not True or not isinstance(response.get("Result"), dict):
            raise VerificationError(f"CLI inspection failed: {text}; log: {log}")
        return response["Result"]

    def rows(self, category: str, command: str, field: str, *arguments: object) -> list[dict]:
        rows: list[dict] = []
        while True:
            response = self.call(category, command, *arguments, "--limit", 100, "--offset", len(rows))
            page = response[field]
            rows.extend(page)
            if not response["Truncated"]:
                return rows
            if not page:
                raise VerificationError(f"CLI inspection pagination stopped making progress: {command}")


def _inspect_resolved_capture(cli: _CaptureCLI, capture: dict) -> None:
    opened = cli.call("session", "open", "--path", capture["session"])
    if opened["Result"] != "Success":
        raise VerificationError(f"Could not open resolved capture {capture['label']}: {opened}")
    session = opened["SuccessData"]["SessionKey"]
    errors: list[str] = []
    try:
        processes = cli.rows("general", "get-processes", "Processes", "--session-key", session)
        if {process["ProcessID"] for process in processes} != {capture["pid"]}:
            raise VerificationError("Resolved session process selection differs from the raw ETW capture.")
        threads = cli.rows("general", "get-threads", "Threads", "--session-key", session)
        expected_threads = {"Odin Main":capture["main_thread"], "Odin Worker":capture["worker_thread"]}
        thread_keys: dict[str, int] = {}
        for name, identifier in expected_threads.items():
            matches = [thread for thread in threads if thread["Name"] == name]
            if len(matches) != 1 or matches[0]["ThreadID"] != identifier:
                raise VerificationError(f"Resolved thread attribution is incorrect: {name}")
            thread_keys[name] = matches[0]["ThreadKey"]
        expected_counts = {
            "Odin Frame":3, "Odin Child":3, "Odin UTF8 café":1, "Odin Wide":1,
            "Odin Worker Event":1, "Odin Fiber Event":1,
            "Raw UTF8 Z":1, "Raw UTF8 N":1, "Raw UTF16 Z":1, "Raw UTF16 N":1,
        }
        opened_functions = cli.call(
            "functionlist", "open", "--session-key", session, "--function-name-reg-ex",
            "Odin (Frame|Child|UTF8 café|Wide|Worker Event|Fiber Event)|Raw UTF(8|16) [ZN]",
        )
        functions_key = opened_functions["FunctionListKey"]
        functions = cli.rows("functionlist", "get-functions", "Functions",
                             "--function-list-key", functions_key)
        events: dict[str, list[tuple[float, float]]] = {name:[] for name in expected_counts}
        for function in functions:
            name = function["Name"]
            if name not in events or function["Module"].casefold() != "smoke.exe":
                raise VerificationError(f"Unexpected resolved instrumentation function: {function}")
            opened_instances = cli.call("functionlist", "open-instances", "--function-list-key",
                                        functions_key, "--function-key", function["FunctionKey"])
            instances = cli.call("functionlist", "get-instances",
                                 "--instances-key", opened_instances["InstancesKey"], "--limit", 100,
                                 "--sort-key", "StartTime", "--sort-order", "Ascending")
            rows = instances["InstancesPlainText"].splitlines()
            if (instances["Truncated"] or instances["TotalInstanceCount"] != len(rows)
                    or opened_instances["TotalCallCount"] != len(rows)):
                raise VerificationError(f"Incomplete resolved event instances: {name}")
            thread_name = "Odin Worker" if name in {"Odin Worker Event", "Odin Fiber Event"} else "Odin Main"
            for row in rows:
                values = dict(zip(instances["Columns"].split(";"), row.split(";"), strict=True))
                start, duration = float(values["StartTimeMs"]), float(values["DurationMs"])
                if int(values["ThreadKey"]) != thread_keys[thread_name]:
                    raise VerificationError(f"Resolved event is on the wrong thread: {name}")
                if not math.isfinite(start) or not math.isfinite(duration) or duration <= 0:
                    raise VerificationError(f"Invalid resolved event interval: {name}")
                events[name].append((start, start + duration))
        counts = {name:len(instances) for name, instances in events.items()}
        if counts != expected_counts:
            raise VerificationError(f"Resolved event counts differ from the fourteen-scope contract: {counts}")
        frames, children = sorted(events["Odin Frame"]), sorted(events["Odin Child"])
        # CLI table timestamps are rounded to 0.001 ms. Do not assert wall-clock
        # duration ratios: scheduler delays can legitimately lengthen early return.
        for (begin, end), (child_begin, child_end) in zip(frames, children, strict=True):
            if child_begin < begin - 0.002 or child_end > end + 0.002:
                raise VerificationError("A resolved child interval escapes its matching frame.")
        for previous, following in zip(frames, frames[1:]):
            if previous[1] > following[0] + 0.002:
                raise VerificationError("Resolved frames unexpectedly overlap.")
        all_intervals = [interval for intervals in events.values() for interval in intervals]
        graph = cli.call("callgraph", "open", "--session-key", session,
                         "--start-time-ms", min(start for start, _ in all_intervals) - 0.002,
                         "--end-time-ms", max(end for _, end in all_intervals) + 0.002)
        tree = cli.call("callgraph", "traverse", "--call-graph-key", graph["CallGraphKey"],
                        "--max-node-depth", 64, "--min-inclusive-pct-of-parent", 0,
                        "--min-inclusive-pct-of-call-graph", 0)
        nodes = [dict(zip(tree["Columns"], row.split(";"), strict=True)) for row in tree["Rows"]]
        by_key = {node["NodeKey"]:node for node in nodes}

        def has_ancestor(node: dict, function: str) -> bool:
            seen: set[str] = set()
            key = node["ParentNodeKey"]
            while key in by_key and key not in seen:
                seen.add(key)
                parent = by_key[key]
                if parent["Function"] == function:
                    return True
                key = parent["ParentNodeKey"]
            return False

        child_nodes = [node for node in nodes if node["Function"] == "Odin Child"]
        fiber_nodes = [node for node in nodes if node["Function"] == "Odin Fiber Event"]
        if not child_nodes or not all(has_ancestor(node, "Odin Frame") for node in child_nodes):
            raise VerificationError("Resolved call graph does not nest every child under its frame.")
        if not fiber_nodes or not all(has_ancestor(node, "main::fiber_entry") for node in fiber_nodes):
            raise VerificationError("Resolved fiber events lack their symbolized fiber-entry ancestry.")
        print(f"CLI capture inspection passed: {capture['label']}; pid={capture['pid']}; "
              "14 events, 3 nested frame/child pairs, Unicode names, worker/fiber attribution.", flush=True)
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        errors.append(str(error))
    finally:
        try:
            closed = cli.call("session", "close", "--session-key", session)
            if session not in closed["ClosedSessions"]:
                raise VerificationError(f"CLI did not close owned inspection session {session}.")
        except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"Inspection session cleanup failed: {error}")
    if errors:
        raise VerificationError(f"{capture['label']}: " + "; ".join(errors))


def internal_inspection_main(arguments: Sequence[str]) -> int | None:
    if not arguments or arguments[0] != "--internal-inspect-captures":
        return None
    try:
        if len(arguments) != 3:
            raise VerificationError("Invalid internal capture-inspection invocation.")
        executable, manifest = Path(arguments[1]), Path(arguments[2])
        captures = json.loads(manifest.read_text(encoding="utf-8"))
        cli = _CaptureCLI(executable, manifest.parent)
        errors: list[str] = []
        for capture in captures:
            try:
                _inspect_resolved_capture(cli, capture)
            except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
                print(f"CLI capture inspection failed: {error}", file=sys.stderr, flush=True)
        if errors:
            return 1
        if {capture["label"] for capture in captures} != {"x64/static", "x64/dynamic"}:
            raise VerificationError("CLI inspection requires both x64 capture configurations.")
        print("All resolved captures passed CLI inspection.", flush=True)
        return 0
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"CLI capture inspection failed: {error}", file=sys.stderr, flush=True)
        return 1


def verify(c: Config) -> None:
    tool_cell = new_cell(c, "tools")
    phase(c, "Odin toolchain", lambda: run_checked(c, tool_cell, [c.odin, "version"]))
    phase(c, "formatter availability", lambda: run_checked(c, tool_cell, [c.odinfmt, "-help"]))
    contract_cell = new_cell(c, "contracts-sdk-free-windows_amd64")
    test_variables = ("SUPERLUMINAL_TEST_DLL", "SUPERLUMINAL_TEST_OPPOSITE_DLL",
                      "SUPERLUMINAL_TEST_VERSION_DLL", "SUPERLUMINAL_TEST_TABLE_DLL")
    phase(c, "SDK-free x64 contracts", lambda: run_checked(
        c, contract_cell, [c.odin, "test", c.repo / "tests/contracts", "-target:windows_amd64", "-o:speed",
                           f"-out:{contract_cell / 'contracts.exe'}"],
        environment={name: None for name in test_variables},
    ))
    disabled_cell = new_cell(c, "disabled-windows")
    phase(c, "disabled Windows execution", lambda: _build_and_run(
        c, disabled_cell, c.repo, "dynamic", "-use-single-module", ["-o:speed"], disabled=True,
    ))
    linux_cell = new_cell(c, "disabled-linux")
    phase(c, "disabled Linux checking", lambda: run_checked(
        c, linux_cell, [c.odin, "check", c.repo / "examples/smoke", "-target:linux_amd64",
                        "-define:SUPERLUMINAL_ENABLED=false"],
    ))
    if c.level == "Contracts":
        return

    dll = get_sdk_binary(c, "x64", "dynamic")
    contract_cell = new_cell(c, "contracts-x64")
    probes_ok = phase(c, "x64 C++ ABI and error fixtures", lambda: write_probes(c, contract_cell))
    if probes_ok:
        real_environment = dict(zip(test_variables, map(str, (
            dll, get_sdk_binary(c, "x86", "dynamic"),
            contract_cell / "version.dll", contract_cell / "table.dll",
        ))))
        phase(c, "real-DLL x64 contracts", lambda: run_checked(
            c, contract_cell, [c.odin, "test", c.repo / "tests/contracts", "-target:windows_amd64", "-o:speed",
                               f"-out:{contract_cell / 'contracts.exe'}"], environment=real_environment,
        ))
        phase(c, "process-isolated DLL discovery", lambda: test_dll_discovery(
            c, contract_cell, dll, contract_cell / "version.dll", contract_cell / "table.dll",
        ))
    else:
        skip(c, "real-DLL x64 contracts", "ABI/error-fixture preparation failed")
        skip(c, "process-isolated DLL discovery", "error-fixture preparation failed")
    archive_cell = new_cell(c, "sdk-dependencies-x64")
    phase(c, "MT archive and SDK dependencies", lambda: _inspect_archive(c, archive_cell))

    matrix_passed = 0
    for mode in ("static", "dynamic"):
        for module in ("-use-single-module", "-use-separate-modules"):
            label = f"x64/{mode}/speed/{module.lstrip('-')}"
            cell = new_cell(c, label.replace("/", "-"))
            matrix_passed += phase(c, label, lambda: _build_and_run(
                c, cell, c.repo, mode, module, ["-o:speed"], dependencies=True,
            ))
    print(f"Enabled x64 build/run matrix: {matrix_passed}/4 passed", flush=True)

    for mode in ("static", "dynamic"):
        cell = new_cell(c, f"codegen-x64-{mode}-speed")
        phase(c, f"codegen/x64/{mode}/speed", lambda: test_codegen(c, cell, mode))
        cell = new_cell(c, f"optimized-symbols-x64-{mode}")
        phase(c, f"optimized-symbols/x64/{mode}", lambda: _build_and_run(
            c, cell, c.repo, mode, "-use-single-module", ["-debug", "-o:speed"],
        ))

    negative_cell = new_cell(c, "negative-x64")
    negative_executable = negative_cell / "smoke.exe"
    negative_ok = phase(c, "assertion-disabled negative-test build", lambda: build_smoke(
        c, c.repo, negative_cell, "dynamic", "-use-single-module", ["-o:speed"], disable_assert=True,
    ))
    if negative_ok:
        for encoding in ("utf8", "utf16"):
            phase(c, f"maximum-length/{encoding}", lambda: run_smoke(
                c, negative_cell, negative_executable, "dynamic", dll, [f"--max-{encoding}"],
            ))
            phase(c, f"overflow/{encoding}", lambda: run_checked(
                c, negative_cell, [negative_executable, "--dll", dll, f"--overflow-{encoding}"],
                expected="Superluminal string length exceeds 65535", expect_failure=True,
            ))
        phase(c, "uninitialized event call", lambda: run_checked(
            c, negative_cell, [negative_executable, "--uninitialized"],
            expected="Superluminal is not initialized", expect_failure=True,
        ))
    else:
        skip(c, "boundary and uninitialized processes", "assertion-disabled build failed")

    relocated = c.run / "source relocation café"
    if phase(c, "published source relocation", lambda: _copy_published_sources(c, relocated)):
        for mode in ("dynamic", "static"):
            cell = new_cell(c, f"relocation-{mode}")
            phase(c, f"relocation/{mode}", lambda: _build_and_run(
                c, cell, relocated, mode, "-use-single-module", ["-o:speed"],
            ))
    else:
        skip(c, "relocated builds", "source relocation failed")

    if c.level == "Capture":
        if phase(c, "capture prerequisites and raw ETW checker", lambda: initialize_capture(c)):
            for mode in ("static", "dynamic"):
                cell = new_cell(c, f"capture-x64-{mode}")
                def capture_action() -> None:
                    executable = build_smoke(c, c.repo, cell, mode, "-use-single-module", ["-debug", "-o:speed"])
                    start_capture(c, cell, executable, mode, dll, f"x64/{mode}")
                try:
                    phase(c, f"capture/x64/{mode}", capture_action)
                except ElevationCancelled as error:
                    c.results.append(PhaseResult(f"capture/x64/{mode}", "FAIL", str(error)))
                    print(f"FAIL: capture/x64/{mode}: {error}", flush=True)
                    if mode == "static":
                        skip(c, "capture/x64/dynamic", "elevation was cancelled; not prompting again")
                    break
            phase(c, "capture inspection", lambda: _review_captures(c))
        else:
            skip(c, "two captures and inspection", "capture prerequisites failed")


def parse_args(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Complete x64 Performance API verification; standard-library Python, no PowerShell.")
    parser.add_argument("--level", choices=("Contracts", "Runtime", "Capture"), default="Runtime")
    parser.add_argument("--odin", help="Odin executable; otherwise ODIN, PATH, or a unique sibling checkout")
    parser.add_argument("--odinfmt", help="Formatter executable; otherwise ODINFMT, PATH, or a unique sibling checkout")
    parser.add_argument("--sdk-root", help="Installed Performance API SDK; otherwise SUPERLUMINAL_SDK or installation discovery")
    parser.add_argument("--superluminal-cmd", help="Profiler CLI; otherwise SUPERLUMINAL_CMD, PATH, or adjacent to the SDK")
    parser.add_argument("--vcvarsall", help="MSVC vcvarsall.bat; otherwise discovered from developer-shell cl.exe")
    return parser.parse_args(arguments)


def main() -> int:
    inspection_result = internal_inspection_main(sys.argv[1:])
    if inspection_result is not None:
        return inspection_result
    internal_result = internal_capture_main(sys.argv[1:])
    if internal_result is not None:
        return internal_result
    args = parse_args(sys.argv[1:])
    if sys.platform != "win32":
        print("This verification driver requires Windows.", file=sys.stderr)
        return 2
    try:
        c = configure(args)
    except (VerificationError, OSError) as error:
        print(f"Prerequisite failure: {error}", file=sys.stderr)
        return 2
    c.run = Path(tempfile.mkdtemp(prefix="superluminal-verify-")).resolve()
    print(f"Verification scratch: {c.run}", flush=True)
    status = 1
    try:
        verify(c)
        status = 0 if all(result.status == "PASS" for result in c.results) else 1
    except KeyboardInterrupt:
        print("Verification cancelled; cleaning owned processes and files.", file=sys.stderr, flush=True)
        status = 130
    except (VerificationError, OSError) as error:
        c.results.append(PhaseResult("verification infrastructure", "FAIL", str(error)))
        print(f"Verification infrastructure failed: {error}", file=sys.stderr, flush=True)
    finally:
        try:
            shutil.rmtree(c.run)
            if c.run.exists():
                raise VerificationError(f"Owned verification directory still exists: {c.run}")
            print(f"Removed verification scratch: {c.run}", flush=True)
        except (OSError, VerificationError) as error:
            print(f"Cleanup failed: {error}", file=sys.stderr, flush=True)
            status = 1
    print("\nVerification results:", flush=True)
    for result in c.results:
        detail = f": {result.detail}" if result.detail else ""
        print(f"{result.status}: {result.label}{detail}", flush=True)
    passed = sum(result.status == "PASS" for result in c.results)
    failed = sum(result.status == "FAIL" for result in c.results)
    skipped = sum(result.status == "SKIP" for result in c.results)
    print(f"Verification level {c.level}: {passed} passed, {failed} failed, {skipped} skipped; exit {status}.", flush=True)
    return status


def build_smoke(
    c: Config,
    source_repo: Path,
    cell: Path,
    mode: str,
    module: str,
    profile: Sequence[str],
    *,
    disabled: bool = False,
    disable_assert: bool = False,
) -> Path:
    executable = cell / "smoke.exe"
    arguments: list[str | Path] = [
        c.odin,
        "build",
        source_repo / "examples" / "smoke",
        "-target:windows_amd64",
        module,
        f"-out:{executable}",
        f"-pdb-name:{cell / 'smoke.pdb'}",
        f"-define:SUPERLUMINAL_LINK_MODE={mode}",
        *profile,
    ]
    if disabled:
        arguments.append("-define:SUPERLUMINAL_ENABLED=false")
    elif mode == "static":
        if c.sdk is None:
            raise VerificationError("Static smoke requires the Performance API SDK.")
        arguments.append(f"-collection:superluminal_sdk={c.sdk}")
    if disable_assert:
        arguments.append("-disable-assert")
    run_checked(c, cell, arguments, working_dir=cell)
    return executable


def run_smoke(
    c: Config,
    cell: Path,
    executable: Path,
    mode: str,
    dll: Path | None,
    extra: Sequence[str] = (),
    *,
    environment: Mapping[str, str | None] | None = None,
    working_dir: Path | None = None,
) -> None:
    arguments: list[str | Path] = [executable, *extra]
    if mode == "dynamic" and dll is not None:
        arguments.extend(["--dll", dll])
    run_checked(
        c,
        cell,
        arguments,
        expected="Superluminal smoke passed",
        environment=environment,
        working_dir=working_dir if working_dir is not None else cell,
    )


def assert_dependencies(c: Config, cell: Path, executable: Path) -> None:
    text = invoke_msvc(c, cell, f'dumpbin.exe /nologo /dependents "{executable}"')
    dynamic_crt = r"^\s*(?:MSVC[PR]\w*|VCRUNTIME\w*|UCRTBASE\w*|api-ms-win-crt-\S*)\.dll\s*$"
    if re.search(dynamic_crt, text, re.IGNORECASE | re.MULTILINE):
        raise VerificationError(f"Static CRT host acquired a dynamic CRT: {executable}")


def write_probes(c: Config, cell: Path) -> None:
    if c.sdk is None:
        raise VerificationError("ABI probes require the Performance API SDK.")
    fields = (
        "SetCurrentThreadName",
        "SetCurrentThreadNameN",
        "BeginEvent",
        "BeginEventN",
        "BeginEventWide",
        "BeginEventWideN",
        "EndEvent",
        "RegisterFiber",
        "UnregisterFiber",
        "BeginFiberSwitch",
        "EndFiberSwitch",
    )
    probe = '''#include <Superluminal/PerformanceAPI_capi.h>
#include <stddef.h>
#include <type_traits>
static_assert(PERFORMANCEAPI_VERSION == 0x30000, "version");
static_assert(sizeof(int) == 4 && sizeof(wchar_t) == 2, "scalar ABI");
static_assert(sizeof(void*) == 8, "target architecture");
static_assert(sizeof(PerformanceAPI_SuppressTailCallOptimization) == 24, "return size");
static_assert(alignof(PerformanceAPI_SuppressTailCallOptimization) == 8, "return alignment");
static_assert(sizeof(PerformanceAPI_Functions) == 11 * sizeof(void*), "table size");
static_assert(alignof(PerformanceAPI_Functions) == alignof(void*), "table alignment");
static_assert(std::is_same<decltype(PerformanceAPI_EndEvent()), PerformanceAPI_SuppressTailCallOptimization>::value, "EndEvent return");
'''
    probe += "\n".join(
        f'static_assert(offsetof(PerformanceAPI_Functions, {name}) == {index} * sizeof(void*), "field offset");'
        for index, name in enumerate(fields)
    ) + "\n"
    write_text(cell / "abi.cpp", probe)
    include = c.sdk / "include"
    invoke_msvc(
        c,
        cell,
        f'cl.exe /nologo /c /TP /I"{include}" /Fo"{cell / "abi.obj"}" "{cell / "abi.cpp"}"',
    )
    for fixture, result in (("version", 0), ("table", 1)):
        source = f'''#include <Superluminal/PerformanceAPI_capi.h>
extern "C" int __cdecl PerformanceAPI_GetAPI(int version, PerformanceAPI_Functions* functions) {{
    (void)version;
    *functions = {{}};
    return {result};
}}
'''
        write_text(cell / f"{fixture}.cpp", source)
        # Explicit export spelling keeps both error fixtures faithful to the SDK.
        write_text(cell / f"{fixture}.def", "EXPORTS\r\nPerformanceAPI_GetAPI\r\n")
        invoke_msvc(
            c,
            cell,
            f'cl.exe /nologo /LD /MT /I"{include}" '
            f'/Fo"{cell / (fixture + ".obj")}" "{cell / (fixture + ".cpp")}" '
            f'/link /DEF:"{cell / (fixture + ".def")}" '
            f'/OUT:"{cell / (fixture + ".dll")}" '
            f'/IMPLIB:"{cell / (fixture + ".lib")}" '
            f'/PDB:"{cell / (fixture + ".pdb")}"',
        )


def test_dll_discovery(
    c: Config, cell: Path, dll: Path, version_dll: Path, table_dll: Path
) -> None:
    # Every launch is a new native process. Only its environment is overridden.
    # Cleanup errors propagate instead of hiding residual owned files.
    with tempfile.TemporaryDirectory(prefix="discovery-", dir=cell) as directory:
        root = Path(directory)
        app = root / "application café"
        cwd = root / "different working directory"
        environment_directory = root / "environment 日本"
        path_directory = root / "path only"
        empty_directory = root / "empty directory"
        for path in (app, cwd, environment_directory, path_directory, empty_directory):
            path.mkdir()
        executable = build_smoke(
            c, c.repo, app, "dynamic", "-use-single-module", ["-o:speed"]
        )
        app_dll = app / "PerformanceAPI.dll"
        copy_file(dll, app_dll)
        run_smoke(
            c, cell, executable, "dynamic", None,
            environment={"SUPERLUMINAL_DLL_DIR": None}, working_dir=cwd,
        )
        # A genuinely present zero-length value must be distinct from removal.
        run_smoke(
            c, cell, executable, "dynamic", None,
            environment={"SUPERLUMINAL_DLL_DIR": ""}, working_dir=cwd,
        )
        # A valid Unicode environment directory wins over a broken app-local DLL.
        copy_file(dll, environment_directory / "PerformanceAPI.dll")
        copy_file(version_dll, app_dll)
        for value in (str(environment_directory), str(environment_directory) + "\\"):
            run_smoke(
                c, cell, executable, "dynamic", None,
                environment={"SUPERLUMINAL_DLL_DIR": value}, working_dir=cwd,
            )
        copy_file(dll, app_dll)
        failures = [
            ("relative directory", "Invalid_Path"),
            ("\\root-relative", "Invalid_Path"),
            ("C:drive-relative", "Invalid_Path"),
            ("C:\\" + "a" * 32757, "Invalid_Path"),
            (str(root / "missing directory"), "Load_Failed"),
            (str(empty_directory), "Load_Failed"),
            (str(app_dll), "Load_Failed"),
        ]
        for name, fixture, error in (
            ("version", version_dll, "Version_Mismatch"),
            ("table", table_dll, "Invalid_Function_Table"),
        ):
            fixture_directory = root / name
            fixture_directory.mkdir()
            copy_file(fixture, fixture_directory / "PerformanceAPI.dll")
            failures.append((str(fixture_directory), error))
        for value, error in failures:
            environment = {"SUPERLUMINAL_DLL_DIR": value}
            run_checked(
                c, cell, [executable],
                expected=f"Superluminal initialization failed: {error}",
                expect_failure=True, environment=environment, working_dir=cwd,
            )
            # Explicit nonempty paths win even over malformed discovery overrides.
            run_smoke(
                c, cell, executable, "dynamic", dll,
                environment=environment, working_dir=cwd,
            )
        # Neither the current directory nor PATH is an automatic DLL location.
        app_dll.unlink()
        cwd_dll = cwd / "PerformanceAPI.dll"
        copy_file(dll, cwd_dll)
        run_checked(
            c, cell, [executable],
            expected="Superluminal initialization failed: Load_Failed",
            expect_failure=True,
            environment={"SUPERLUMINAL_DLL_DIR": None}, working_dir=cwd,
        )
        cwd_dll.unlink()
        copy_file(dll, path_directory / "PerformanceAPI.dll")
        run_checked(
            c, cell, [executable],
            expected="Superluminal initialization failed: Load_Failed",
            expect_failure=True,
            environment={
                "SUPERLUMINAL_DLL_DIR": None,
                "PATH": f"{path_directory};{os.environ.get('PATH', '')}",
            },
            working_dir=cwd,
        )
    print("Process-isolated DLL discovery passed: windows_amd64")


def test_codegen(c: Config, cell: Path, mode: str) -> None:
    source = '''package probe
import perf "binding:."
import "base:runtime"
@(export)
probe :: proc "c" () {
    context = runtime.default_context()
    perf.begin_event("Codegen")
    perf.end_event()
}
@(export)
initialize :: proc "c" (path: cstring) -> i32 {
    context = runtime.default_context()
    return i32(perf.init(string(path)))
}
'''
    probe_path = cell / "probe.odin"
    write_text(probe_path, source)
    ir_directory = cell / "ir"
    ir_directory.mkdir()
    arguments: list[str | Path] = [
        c.odin, "build", probe_path, "-file", "-no-entry-point",
        "-use-single-module", "-target:windows_amd64",
        f"-collection:binding={c.repo}",
        f"-define:SUPERLUMINAL_LINK_MODE={mode}", "-o:speed",
    ]
    if mode == "static":
        if c.sdk is None:
            raise VerificationError("Static code generation requires the Performance API SDK.")
        arguments.append(f"-collection:superluminal_sdk={c.sdk}")
    run_checked(
        c, cell, [*arguments, "-build-mode:llvm-ir", f"-out:{ir_directory}"],
        working_dir=cell,
    )
    ir_files = [path for path in ir_directory.glob("*.ll") if path.is_file()]
    if len(ir_files) != 1:
        raise VerificationError("Expected one LLVM IR module from the single-module probe.")
    ir = ir_files[0].read_text(encoding="utf-8")
    # Odin emits IR before optimization: check ABI here, inlining in final assembly.
    end_match = re.search(
        r'^define\b[^\r\n]*@"superluminal_perf::end_event"\([^\r\n]*\).*?^\}',
        ir, re.MULTILINE | re.DOTALL,
    )
    if end_match is None:
        end_match = re.search(
            r"^define\b[^\r\n]*@probe\([^\r\n]*\).*?^\}",
            ir, re.MULTILINE | re.DOTALL,
        )
    end_body = end_match.group(0) if end_match is not None else ""
    if (
        'alloca %"raw::Suppress_Tail_Call_Optimization"' not in end_body
        or re.search(r"call\s+void\s+[^\r\n]*sret\(", end_body, re.MULTILINE) is None
    ):
        raise VerificationError(
            f"EndEvent is missing its C ABI structure-return storage/call in windows_amd64 {mode}."
        )
    assembly_path = cell / "probe.S"
    run_checked(
        c, cell, [*arguments, "-build-mode:asm", f"-out:{assembly_path}"],
        working_dir=cell,
    )
    assembly = assembly_path.read_text(encoding="utf-8")
    caller_match = re.search(
        r"^_?probe:\r?\n.*?(?=^\s*\.def\s|\Z)",
        assembly, re.MULTILINE | re.DOTALL,
    )
    if caller_match is None:
        raise VerificationError("Exported code-generation probe assembly is missing.")
    caller = caller_match.group(0)
    if re.search(r"superluminal_perf::(?:begin_event|end_event)", caller):
        raise VerificationError(
            f"Native event wrappers were not inlined into the caller in windows_amd64 {mode} -o:speed."
        )
    if mode == "static":
        if (
            re.search(r"^\s*call[ql]?\s+_?PerformanceAPI_EndEvent\b", caller, re.MULTILINE) is None
            or re.search(r"^\s*jmp[ql]?\s+_?PerformanceAPI_EndEvent\b", caller, re.MULTILINE)
        ):
            raise VerificationError("Final static EndEvent is not a non-tail call.")
    elif (
        re.search(r"^\s*call[ql]?\s+\*", caller, re.MULTILINE) is None
        or re.search(r"^\s*jmp[ql]?\s+\*", caller, re.MULTILINE)
    ):
        raise VerificationError("Final dynamic EndEvent is not a non-tail indirect call.")


class ElevationCancelled(VerificationError):
    """The operator cancelled elevation; do not prompt again in this run."""




class _ProcessStartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD), ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD), ("y_size", wintypes.DWORD),
        ("x_chars", wintypes.DWORD), ("y_chars", wintypes.DWORD),
        ("fill", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE),
    ]


class _ProcessStartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _ProcessStartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


class _ProcessJobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ProcessJobLimits(ctypes.Structure):
    _fields_ = [("basic", _ProcessJobBasicLimits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _ProcessJobAccounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
        ("page_faults", wintypes.DWORD), ("total", wintypes.DWORD),
        ("active", wintypes.DWORD), ("terminated", wintypes.DWORD),
    ]


class _ProcessTokenUser(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


class _ProcessWindows:
    """Small, explicitly typed surface of documented Windows APIs."""

    def __init__(self) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        h, d, p = wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p
        definitions = {
            "CreateJobObjectW": ([p, wintypes.LPCWSTR], h),
            "SetInformationJobObject": ([h, ctypes.c_int, p, d], wintypes.BOOL),
            "QueryInformationJobObject": ([h, ctypes.c_int, p, d, p], wintypes.BOOL),
            "AssignProcessToJobObject": ([h, h], wintypes.BOOL),
            "TerminateJobObject": ([h, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([h], wintypes.BOOL),
            "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, p, p,
                                wintypes.BOOL, d, p, wintypes.LPCWSTR, p,
                                ctypes.POINTER(_ProcessInformation)], wintypes.BOOL),
            "ResumeThread": ([h], d),
            "TerminateProcess": ([h, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([h, d], d),
            "GetExitCodeProcess": ([h, ctypes.POINTER(d)], wintypes.BOOL),
            "GetCurrentProcess": ([], h),
            "GetProcessTimes": ([h, ctypes.POINTER(wintypes.FILETIME),
                                 ctypes.POINTER(wintypes.FILETIME),
                                 ctypes.POINTER(wintypes.FILETIME),
                                 ctypes.POINTER(wintypes.FILETIME)], wintypes.BOOL),
            "OpenProcess": ([d, wintypes.BOOL, d], h),
            "CreateEventW": ([p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], h),
            "OpenEventW": ([d, wintypes.BOOL, wintypes.LPCWSTR], h),
            "SetEvent": ([h], wintypes.BOOL),
            "LocalFree": ([p], p),
            "InitializeProcThreadAttributeList": ([p, d, d, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            "UpdateProcThreadAttribute": ([p, d, ctypes.c_size_t, p, ctypes.c_size_t, p, p], wintypes.BOOL),
            "DeleteProcThreadAttributeList": ([p], None),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        definitions = {
            "OpenProcessToken": ([h, d, ctypes.POINTER(h)], wintypes.BOOL),
            "GetTokenInformation": ([h, ctypes.c_int, p, d, ctypes.POINTER(d)], wintypes.BOOL),
            "ConvertSidToStringSidW": ([p, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.advapi, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def check(result: object, operation: str) -> None:
        if not result:
            error = ctypes.get_last_error()
            raise VerificationError(f"{operation}: {ctypes.FormatError(error).strip()} ({error})")

    def signaled(self, handle: int, milliseconds: int = 0) -> bool:
        status = self.kernel.WaitForSingleObject(handle, milliseconds)
        if status == 0:
            return True
        if status == 258:
            return False
        self.check(False, "WaitForSingleObject")
        return False

    def creation_time(self, handle: int) -> int:
        fields = [wintypes.FILETIME() for _ in range(4)]
        self.check(self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in fields)),
                   "GetProcessTimes")
        return fields[0].dwLowDateTime | (fields[0].dwHighDateTime << 32)

    def current_user(self) -> tuple[bool, str]:
        token = wintypes.HANDLE()
        self.check(self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x8,
                                               ctypes.byref(token)), "OpenProcessToken")
        try:
            elevated, needed = wintypes.DWORD(), wintypes.DWORD()
            self.check(self.advapi.GetTokenInformation(token, 20, ctypes.byref(elevated),
                                                       ctypes.sizeof(elevated), ctypes.byref(needed)),
                       "GetTokenInformation(TokenElevation)")
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if not needed.value:
                self.check(False, "GetTokenInformation(TokenUser size)")
            buffer = ctypes.create_string_buffer(needed.value)
            self.check(self.advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(needed)),
                       "GetTokenInformation(TokenUser)")
            user = ctypes.cast(buffer, ctypes.POINTER(_ProcessTokenUser)).contents
            sid = wintypes.LPWSTR()
            self.check(self.advapi.ConvertSidToStringSidW(user.sid, ctypes.byref(sid)),
                       "ConvertSidToStringSidW")
            try:
                return bool(elevated.value), sid.value
            finally:
                self.kernel.LocalFree(ctypes.cast(sid, ctypes.c_void_p))
        finally:
            self.kernel.CloseHandle(token)


def _process_environment(overrides: Mapping[str, str | None] | None) -> object:
    values = dict(os.environ)
    for name, value in (overrides or {}).items():
        if not name or "=" in name or "\0" in name or (value is not None and "\0" in value):
            raise VerificationError("Invalid child environment entry")
        for previous in list(values):
            if previous.casefold() == name.casefold():
                del values[previous]
        if value is not None:
            values[name] = value
    # create_unicode_buffer contributes the second terminating NUL. An explicit
    # empty value is emitted as NAME=; an unset value has no entry at all.
    block = "\0".join(f"{name}={value}" for name, value in sorted(values.items(), key=lambda pair: pair[0].upper())) + "\0"
    return ctypes.create_unicode_buffer(block)


class _ProcessJob:
    """A suspended child enters its non-breakaway job before executing any code."""

    def __init__(self, api: _ProcessWindows, args: Sequence[str | Path], cwd: Path,
                 log: Path, environment: Mapping[str, str | None] | None = None,
                 command_line: str | None = None) -> None:
        self.api, self.process, self.job = api, None, None
        arguments = [str(arg) for arg in args]
        if not arguments or any("\0" in value for value in arguments):
            raise VerificationError("Invalid child command")
        line = ctypes.create_unicode_buffer(command_line or subprocess.list2cmdline(arguments))
        env = _process_environment(environment)
        info = _ProcessInformation()
        self.job = api.kernel.CreateJobObjectW(None, None)
        api.check(self.job, "CreateJobObjectW")
        try:
            limits = _ProcessJobLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            api.check(api.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits),
                                                         ctypes.sizeof(limits)), "SetInformationJobObject")
            with log.open("wb", buffering=0) as output, open(os.devnull, "rb", buffering=0) as input_file:
                out_handle = msvcrt.get_osfhandle(output.fileno())
                in_handle = msvcrt.get_osfhandle(input_file.fileno())
                os.set_handle_inheritable(out_handle, True)
                os.set_handle_inheritable(in_handle, True)
                startup = _ProcessStartupInfoEx()
                startup.startup.cb = ctypes.sizeof(startup)
                startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
                startup.startup.stdin = in_handle
                startup.startup.stdout = startup.startup.stderr = out_handle
                size = ctypes.c_size_t()
                api.kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                if not size.value:
                    api.check(False, "InitializeProcThreadAttributeList(size)")
                storage = ctypes.create_string_buffer(size.value)
                startup.attributes = ctypes.cast(storage, ctypes.c_void_p)
                api.check(api.kernel.InitializeProcThreadAttributeList(storage, 1, 0, ctypes.byref(size)),
                          "InitializeProcThreadAttributeList")
                try:
                    handles = (wintypes.HANDLE * 2)(in_handle, out_handle)
                    api.check(api.kernel.UpdateProcThreadAttribute(storage, 0, 0x20002, handles,
                                                                   ctypes.sizeof(handles), None, None),
                              "UpdateProcThreadAttribute(handle list)")
                    flags = 0x4 | 0x400 | 0x200 | 0x80000
                    api.check(api.kernel.CreateProcessW(None, line, None, None, True, flags, env,
                                                         str(cwd), ctypes.byref(startup), ctypes.byref(info)),
                              f"CreateProcessW({arguments[0]})")
                finally:
                    api.kernel.DeleteProcThreadAttributeList(storage)
                self.process = info.process
                api.check(api.kernel.AssignProcessToJobObject(self.job, self.process),
                          "AssignProcessToJobObject")
                if api.kernel.ResumeThread(info.thread) == 0xFFFFFFFF:
                    api.check(False, "ResumeThread")
        except BaseException:
            # Assignment itself may fail. The child is still suspended and has
            # created no descendants, so terminating this held handle is safe.
            if info.process:
                self.process = info.process
                api.kernel.TerminateProcess(info.process, 1)
                api.kernel.WaitForSingleObject(info.process, 30000)
            self.close()
            raise
        finally:
            if info.thread:
                api.kernel.CloseHandle(info.thread)

    def empty(self) -> bool:
        info = _ProcessJobAccounting()
        self.api.check(self.api.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(info),
                                                                 ctypes.sizeof(info), None),
                       "QueryInformationJobObject")
        return info.active == 0

    def wait_empty(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.empty():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.025)
        return True

    def stop(self) -> None:
        self.api.check(self.api.kernel.TerminateJobObject(self.job, 1), "TerminateJobObject")
        if not self.wait_empty(30.0):
            raise VerificationError("Owned child tree remained alive after termination")

    def result(self) -> int:
        code = wintypes.DWORD()
        self.api.check(self.api.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)),
                       "GetExitCodeProcess")
        # MSVC can leave background helpers after a successful compiler exit.
        # Stop only our job and verify cleanup before accepting the exit status.
        if not self.wait_empty(1.0):
            self.stop()
        return code.value

    def close(self) -> None:
        if self.process:
            self.api.kernel.CloseHandle(self.process)
            self.process = None
        if self.job:
            self.api.kernel.CloseHandle(self.job)
            self.job = None


def _process_wait(child: _ProcessJob, timeout: float, *, cancel_event: int | None = None,
                  parent: int | None = None, capture: bool = False) -> int:
    deadline = time.monotonic() + timeout
    try:
        while not child.api.signaled(child.process, 50):
            if cancel_event and child.api.signaled(cancel_event):
                raise KeyboardInterrupt("Capture cancellation requested")
            if parent and child.api.signaled(parent):
                raise KeyboardInterrupt("Capture driver exited; stopping its owned capture")
            if time.monotonic() >= deadline:
                raise VerificationError(f"Command timed out after {timeout:g} seconds")
        return child.result()
    except BaseException as failure:
        try:
            if cancel_event:
                child.api.check(child.api.kernel.SetEvent(cancel_event), "SetEvent(capture cancellation)")
            if capture:
                # The helper gets sixty seconds for the bounded CLI to stop ETW.
                # The driver allows another thirty seconds for helper job cleanup.
                grace = 60.0 if parent else 95.0
                end = time.monotonic() + grace
                while not child.api.signaled(child.process, 50) and time.monotonic() < end:
                    pass
                if not child.api.signaled(child.process):
                    print("Capture exceeded cleanup grace; ETW session cleanup cannot be verified.", file=sys.stderr)
            if not child.empty():
                child.stop()
        except BaseException as cleanup_error:
            if isinstance(failure, KeyboardInterrupt):
                raise KeyboardInterrupt(f"Cancelled; owned cleanup could not be verified: {cleanup_error}") from cleanup_error
            raise VerificationError(f"Owned process cleanup could not be verified: {cleanup_error}") from cleanup_error
        raise


def _process_read_log(log: Path) -> str:
    # Native tools predominantly emit UTF-8; replace invalid diagnostics rather
    # than losing the process exit status to a decoding error.
    return log.read_bytes().decode("utf-8-sig", errors="replace")


def _process_run_logged(c: Config, cell: Path, args: Sequence[str | Path], *,
                        expected: str = "", expect_failure: bool = False,
                        working_dir: Path | None = None,
                        environment: Mapping[str, str | None] | None = None,
                        timeout: float = 900.0, command_line: str | None = None,
                        cancel_event: int | None = None, capture: bool = False) -> str:
    if timeout <= 0:
        raise VerificationError("Command timeout must be positive")
    c.command_count += 1
    log = cell / f"command-{c.command_count}.log"
    print(subprocess.list2cmdline([str(arg) for arg in args]), flush=True)
    api = _ProcessWindows()
    child = _ProcessJob(api, args, working_dir or cell, log, environment, command_line)
    try:
        code = _process_wait(child, timeout, cancel_event=cancel_event, capture=capture)
    finally:
        child.close()
        text = _process_read_log(log)
        if text:
            print(text, end="" if text.endswith("\n") else "\n", flush=True)
    if code == 0xC000013A or (capture and code == 130):
        raise KeyboardInterrupt(f"Native capture/command cancellation; log: {log}")
    if capture and (code in (1223, 0x800704C7) or
                    (code != 0 and re.search(r"\b(?:0x800704c7|ERROR_CANCELLED)\b", text, re.IGNORECASE))):
        raise ElevationCancelled(f"Capture elevation was cancelled; no further elevation will be attempted; log: {log}")
    if (code == 0) == expect_failure:
        requirement = "a nonzero exit" if expect_failure else "exit zero"
        raise VerificationError(f"Expected {requirement}; exit={code}; log: {log}")
    if re.search(r"LNK2038|LNK4098|mismatch detected|conflicts with use of other libs", text, re.IGNORECASE):
        raise VerificationError(f"CRT conflict: {log}")
    if expected and expected not in text:
        raise VerificationError(f"Missing expected output {expected!r}: {log}")
    if expected == "Superluminal smoke passed" and not re.search(r"^Superluminal smoke passed\r?$", text, re.MULTILINE):
        raise VerificationError(f"Smoke success marker was not a complete output line: {log}")
    return text


def run_checked(c: Config, cell: Path, args: Sequence[str | Path], *, expected: str = "",
                expect_failure: bool = False, working_dir: Path | None = None,
                environment: Mapping[str, str | None] | None = None,
                timeout: float = 900.0) -> str:
    return _process_run_logged(c, cell, args, expected=expected, expect_failure=expect_failure,
                               working_dir=working_dir, environment=environment, timeout=timeout)


def invoke_msvc(c: Config, cell: Path, command: str) -> str:
    if c.developer_setup is None:
        raise VerificationError("MSVC developer setup has not been resolved")
    script = cell / f"msvc-{uuid.uuid4().hex}.cmd"
    setup = str(c.developer_setup).replace("%", "%%")
    write_text(script, f'@echo off\r\ncall "{setup}" x64 >nul\r\n'
                       'if errorlevel 1 exit /b %errorlevel%\r\n'
                       f'{command}\r\nexit /b %errorlevel%\r\n')
    shell = os.environ.get("ComSpec", "cmd.exe")
    # cmd's /s /c quoting is not the CRT argv quoting used for every other child.
    line = f'{subprocess.list2cmdline([shell])} /d /s /c ""{script}""'
    try:
        return _process_run_logged(c, cell, [shell, "/d", "/s", "/c", script], command_line=line)
    finally:
        script.unlink(missing_ok=True)


def initialize_capture(c: Config) -> None:
    elevated, sid = _ProcessWindows().current_user()
    if elevated:
        raise VerificationError("Run Capture from a non-elevated developer shell; only capture is elevated")
    if c.sudo is None or c.profiler is None:
        raise VerificationError("Capture requires Windows sudo.exe and SuperluminalCmd.exe")
    permissions = new_cell(c, "capture-permissions")
    # Python's protected tempfile ACL needs an inheritable current-user ACE for
    # elevated-created outputs. The owner grants this on the owned tree only;
    # no sudo prompt, broad principal, system/SDK path, or GUI setting is involved.
    run_checked(c, permissions, ["icacls.exe", c.run, "/grant", f"*{sid}:(OI)(CI)F"])
    cell = new_cell(c, "capture-checker")
    checker = cell / "capture-check.exe"
    command = (f'cl.exe /nologo /EHsc /std:c++17 /Fo"{cell / "capture-check.obj"}" '
               f'/Fe"{checker}" "{c.repo / "tools" / "capture_check.cpp"}" '
               f'/link /PDB:"{cell / "capture-check.pdb"}" advapi32.lib')
    invoke_msvc(c, cell, command)
    c.capture_checker = checker


class _ProcessCaptureEvents:
    def __init__(self, api: _ProcessWindows, name: str, *, create: bool) -> None:
        if not re.fullmatch(r"Local\\SuperluminalVerify-[0-9a-f]{32}", name):
            raise VerificationError("Invalid owned capture event name")
        self.api, self.name = api, name
        self.cancel = self.started = self.done = None
        try:
            for field, suffix in (("cancel", ""), ("started", "-started"), ("done", "-done")):
                if create:
                    handle = api.kernel.CreateEventW(None, True, False, name + suffix)
                    error = ctypes.get_last_error()
                else:
                    handle = api.kernel.OpenEventW(0x100000 | 0x2, False, name + suffix)
                    error = 0
                api.check(handle, f"{'Create' if create else 'Open'}EventW({field})")
                setattr(self, field, handle)
                if create and error == 183:
                    raise VerificationError("Capture event already exists; refusing ambiguous ownership")
        except BaseException:
            self.close()
            raise

    def request_stop(self) -> None:
        self.api.check(self.api.kernel.SetEvent(self.cancel), "SetEvent(capture cancellation)")

    def finish(self, required: bool) -> None:
        self.request_stop()
        started = self.api.signaled(self.started)
        if started and not self.api.signaled(self.done, 95000):
            raise VerificationError("Elevated capture cleanup acknowledgement missing; cleanup cannot be verified")
        if required and not started:
            raise VerificationError("Capture helper never acknowledged ownership")

    def close(self) -> None:
        for field in ("cancel", "started", "done"):
            handle = getattr(self, field)
            if handle:
                self.api.kernel.CloseHandle(handle)
                setattr(self, field, None)


def internal_capture_main(args: list[str]) -> int | None:
    if not args or args[0] != "--internal-capture-launch":
        return None
    api = _ProcessWindows()
    events = None
    parent = None
    child = None
    started = False
    try:
        if len(args) < 2:
            raise VerificationError("Missing owned capture event name")
        events = _ProcessCaptureEvents(api, args[1], create=False)
        if len(args) < 7:
            raise VerificationError("Invalid internal launch arguments")
        pid, expected_creation = int(args[2]), int(args[3])
        if not 0 < pid <= 0xFFFFFFFF or expected_creation <= 0:
            raise VerificationError("Invalid capture driver identity")
        parent = api.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        api.check(parent, "OpenProcess(capture driver)")
        if api.creation_time(parent) != expected_creation or api.signaled(parent):
            raise VerificationError("Capture driver identity mismatch; refusing stale PID")
        # Publish ownership before testing cancellation. If the parent does not
        # observe started, its cancellation prevents a later helper from spawning.
        api.check(api.kernel.SetEvent(events.started), "SetEvent(capture started)")
        started = True
        if api.signaled(events.cancel):
            raise KeyboardInterrupt("Capture cancelled before launch")
        cell = Path(args[4]).resolve(strict=True)
        log = cell / "capture.child.log"
        child = _ProcessJob(api, args[5:], cell, log)
        try:
            return _process_wait(child, 900.0, cancel_event=events.cancel, parent=parent, capture=True)
        finally:
            child.close()
            child = None
            text = _process_read_log(log)
            if text:
                print(text, end="" if text.endswith("\n") else "\n", flush=True)
    except KeyboardInterrupt as error:
        print(f"Internal capture cancelled: {error}", file=sys.stderr, flush=True)
        return 130
    except (VerificationError, OSError, ValueError) as error:
        print(f"Internal capture failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if child:
            child.close()
        if parent:
            api.kernel.CloseHandle(parent)
        if events:
            try:
                if started:
                    api.check(api.kernel.SetEvent(events.done), "SetEvent(capture cleanup done)")
            finally:
                events.close()


def _process_owned_path(cell: Path, candidate: Path) -> Path:
    root = cell.resolve(strict=True)
    target = candidate.resolve(strict=True)
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise VerificationError(f"Capture output is outside its owned cell: {candidate}") from error
    if not relative.parts:
        raise VerificationError(f"Capture output cannot be the cell itself: {candidate}")
    return target


def _process_capture_files(root: Path, suffix: str) -> list[Path]:
    def walk_error(error: OSError) -> None:
        raise error

    files = []
    # os.walk does not follow symlink directories; reject all reparse points too,
    # including junctions, instead of accidentally walking outside owned scratch.
    for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories + filenames:
            candidate = Path(directory) / name
            if candidate.lstat().st_file_attributes & 0x400:
                raise VerificationError(f"Reparse point in owned capture output: {candidate}")
        files.extend(Path(directory) / name for name in filenames if name.lower().endswith(suffix))
    return sorted(files)


def start_capture(c: Config, cell: Path, executable: Path, mode: str, dll: Path, label: str) -> None:
    if c.sudo is None or c.profiler is None or c.capture_checker is None:
        raise VerificationError("Capture prerequisites have not completed")
    run_smoke(c, cell, executable, mode, dll)
    api = _ProcessWindows()
    events = _ProcessCaptureEvents(api, "Local\\SuperluminalVerify-" + uuid.uuid4().hex, create=True)
    try:
        creation = api.creation_time(api.kernel.GetCurrentProcess())
        args = [str(c.sudo), "--disable-input", "--chdir", str(cell), sys.executable,
                "-X", "utf8", str(c.self), "--internal-capture-launch", events.name, str(os.getpid()),
                str(creation), str(cell), str(c.profiler), "run", "windows", "--max-duration", "15",
                "--capture-path", str(cell / "smoke.etl"), str(executable), "--capture"]
        if mode == "dynamic":
            args.extend(["--dll", str(dll)])
        try:
            text = _process_run_logged(c, cell, args, cancel_event=events.cancel, capture=True)
        except BaseException as failure:
            try:
                events.finish(required=False)
            except BaseException as cleanup_error:
                if isinstance(failure, (KeyboardInterrupt, ElevationCancelled)):
                    raise type(failure)(f"{failure}; capture cleanup could not be verified: {cleanup_error}") from cleanup_error
                raise
            raise
        events.finish(required=True)
    finally:
        events.close()
    traces = [trace for trace in _process_capture_files(cell, ".etl")
              if str(trace) in text or trace.name in text]
    if not traces:
        raise VerificationError(f"Capture command did not report an existing ETL for {label}")
    records = []
    for trace in traces:
        trace = _process_owned_path(cell, trace)
        checked = run_checked(c, cell, [c.capture_checker, trace], expected="Capture passed:")
        identity = re.search(r"^Capture passed: pid=(\d+) main=(\d+) worker=(\d+);", checked, re.MULTILINE)
        if not identity:
            raise VerificationError(f"Raw ETW checker did not report process/thread identity for {label}")
        pid, main_thread, worker_thread = map(int, identity.groups())
        # No symbol/process-selection switches: inherit the GUI's saved settings.
        resolved = run_checked(c, cell, [c.profiler, "resolve", "windows", trace])
        match = re.search(r"Session was written to '([^']+)'", resolved)
        if not match:
            raise VerificationError(f"Resolver did not report a session path for {label}")
        session_path = Path(match[1])
        if not session_path.is_absolute():
            session_path = cell / session_path
        session_path = _process_owned_path(cell, session_path)
        if session_path.is_dir():
            sessions = _process_capture_files(session_path, ".session")
        elif session_path.is_file():
            sessions = [session_path]
        else:
            raise VerificationError(f"Resolver reported a nonexistent session path for {label}")
        if not sessions:
            raise VerificationError(f"Resolved session directory is empty for {label}")
        for session in sessions:
            records.append(CaptureRecord(label, trace, _process_owned_path(cell, session),
                                         pid, main_thread, worker_thread))
    c.captures.extend(records)
    print(f"Capture ready: {label}")
    for record in records:
        print(record.trace)
        print(record.session)


if __name__ == "__main__":
    raise SystemExit(main())
