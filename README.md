# superluminal-perf-odin

Odin bindings for the Superluminal Performance API 3.0, with an exact C ABI
package (`raw`) and allocation-free native instrumentation
(`superluminal_perf`). Runtime DLL loading is the default; static linking is
an explicit alternative.

## Requirements and portability

- Bindings target Windows x64 (`windows_amd64`) and x86 (`windows_i386`).
  Automated verification builds and runs optimized x64 code only.
  Windows ARM targets are not supported.
- Obtain the Performance API SDK from your Superluminal installation.
  SDK headers and binaries are not included in this repository.
- Use a matching-architecture API 3.0 DLL or static archive. DLL loading
  requests exactly version `0x00030000`; it does not negotiate older APIs.
- Disabled instrumentation needs no SDK and emits no profiling calls.
  Non-Windows targets exclude Windows-only implementation files. This permits
  cross-platform source builds, not native Linux profiling.
- Capture and inspection require Superluminal, suitable Windows tracing
  privileges, and an activated profiler license.

## Quick start: runtime DLL

Add this repository as an Odin collection:

```text
odin build app -o:speed -collection:superluminal_perf=<repository>
```

Place the matching-architecture `PerformanceAPI.dll` beside your executable,
or set `SUPERLUMINAL_DLL_DIR` to its absolute containing directory. Initialize
before starting instrumented workers:

```odin
package main

import perf "superluminal_perf:"

main :: proc() {
    if perf.init() != .None {
        panic("Could not initialize Superluminal")
    }
    defer {
        if !perf.shutdown() {
            panic("Could not unload Superluminal")
        }
    }

    perf.set_current_thread_name("Odin Main")
    {
        perf.scope("Frame", data = "frame=0",
                   color = perf.make_color(0x12, 0x34, 0x56))
        // Work in this lexical scope.
    }
}
```

Dynamic initialization selects exactly one location, in this order:

1. A nonempty `init(dll_path)` argument: an absolute UTF-8 file path without
   embedded NUL. It overrides environment configuration.
2. A nonempty `SUPERLUMINAL_DLL_DIR`: an absolute directory containing
   `PerformanceAPI.dll`, not an SDK root, DLL filename, or path list.
3. Otherwise, `PerformanceAPI.dll` in the **executable's directory**.

For example, in PowerShell before launching the application:

```powershell
$env:SUPERLUMINAL_DLL_DIR = 'C:\Tools\Superluminal\Performance\API\dll\x64'
```

An unset or empty variable selects the executable directory. Spaces and
Unicode directory names are supported. Explicit paths and nonempty
environment overrides are authoritative: invalid paths, missing DLLs,
architecture errors, and API negotiation failures are returned without
falling back to another location. Select the DLL architecture that matches
the application, not necessarily the operating system.

Dynamic builds do not require the SDK collection at build time. Discovery
does not search the current working directory, PATH, or installed SDKs.
The loader uses
`LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`.
Automatic path discovery uses a bounded UTF-16 stack buffer; the `allocator`
argument to `init` is used only for explicit UTF-8 path conversion.

Manual pairing is also available:

```odin
perf.begin_event("Frame", data = frame_data)
defer perf.end_event()
```

An automatic scope ends at the enclosing lexical scope's exit, including
early returns. Never manually end it. Use `perf.scope(#procedure)` for the
caller's procedure name. Begin and end must pair in the same function.

## Lifecycle and ownership

```odin
init :: proc(dll_path: string = "", allocator := context.allocator) -> Error
is_initialized :: proc() -> bool
shutdown :: proc() -> bool
```

|Configuration|Initialization|Shutdown|
|---|---|---|
|Enabled dynamic|Explicit `init`; initially uninitialized|Unloads the owned DLL|
|Enabled static|Always initialized; `init` returns `.None`|Returns true; no unload|
|Disabled|Never initialized; `init` returns `.Disabled`|Returns true|

Dynamic initialization publishes state only after loading and validating all
eleven function pointers. A second successful initialization attempt returns
`.Already_Initialized` without replacing the first module. Failure leaves the
package uninitialized and allows retry. A failed DLL load never silently
disables instrumentation.

Serialize `init` and `shutdown` externally. Keep initialized state unchanged
while workers use the API, and finish all events, scopes, and worker calls
before shutdown. Event calls use no locks or reference counting. Successful
shutdown clears state; repeated shutdown succeeds. If unloading fails,
shutdown returns false and preserves ownership. Do not load or unload from
`DllMain`.

`Error` has these values:

|Value|Meaning|
|---|---|
|`None`|Success|
|`Disabled`|Instrumentation compiled out|
|`Already_Initialized`|The native dynamic module is already initialized|
|`Invalid_Path`|Invalid explicit file path or environment directory; relative or oversized discovered paths|
|`Path_Conversion_Failed`|Explicit UTF-8 path conversion or its allocation failed|
|`Load_Failed`|Windows path discovery or DLL loading failed, including missing files, architecture, or dependency errors|
|`Missing_Get_API`|The DLL does not export `PerformanceAPI_GetAPI`|
|`Version_Mismatch`|The export rejected the requested version|
|`Invalid_Function_Table`|The returned table has missing functions|

## Native instrumentation API

```odin
make_color :: proc(r, g, b: u8) -> u32
set_current_thread_name :: proc(name: string)
begin_event :: proc(id: string, data: string = "", color: u32 = DEFAULT_COLOR)
begin_event_wide :: proc(id: []u16, data: []u16 = nil, color: u32 = DEFAULT_COLOR)
end_event :: proc()
scope :: proc(id: string, data: string = "", color: u32 = DEFAULT_COLOR)
register_fiber :: proc(fiber_id: u64)
unregister_fiber :: proc(fiber_id: u64)
begin_fiber_switch :: proc(current_fiber_id, new_fiber_id: u64)
end_fiber_switch :: proc(fiber_id: u64)
```

These wrappers request forced inlining and are excluded from automatic
instrumentation. Verification uses `-o:speed`.
Event and naming calls use the explicit-length `_N` APIs without string
conversion, cloning, caching, or Odin allocation. The DLL's internal
allocation behavior is outside this guarantee.

- Supply valid UTF-8 strings or UTF-16 slices. Lengths are UTF-8 **bytes** or
  UTF-16 **code units**, including both units of a surrogate pair.
- Inputs do not need NUL terminators. Empty required strings use a stable
  empty buffer; absent event data is passed as nil with length zero.
- Each input length must fit in `u16`. Longer inputs panic with
  `Superluminal string length exceeds 65535`, even with `-disable-assert`.
  ETW has separate payload-size limits; representable lengths do not
  guarantee a maximum-sized event can be recorded.
- Enabled dynamic instrumentation before initialization panics with
  `Superluminal is not initialized`.
- Disabled calls perform no validation or SDK calls. `make_color` is always
  usable without initialization.
- Keep event ID and color stable for a scope; data may vary. The library
  does not retain copies or enforce stability with a cache.
- Colors are `0xRRGGBBFF`, not `0xRRGGBB00`; `DEFAULT_COLOR` is `0xFFFFFFFF`.

Fiber IDs must consistently identify the actual fibers. Register fibers
before use, call `begin_fiber_switch` before the actual switch, and call
`end_fiber_switch` when a fiber resumes. Unregister completed fibers before
disposing of them. The smoke example demonstrates Windows fiber ownership
and switching on a dedicated worker thread.

## Compile-time configuration

The native and raw packages share these definitions:

|Odin define|Default|Accepted values|
|---|---|---|
|`SUPERLUMINAL_ENABLED`|`ODIN_OS == .Windows`|`true`, `false`|
|`SUPERLUMINAL_LINK_MODE`|`dynamic`|`dynamic`, `static`|

`ENABLED` and `LINK_MODE` expose the selected values. Unknown mode strings
are compile-time errors. Enabled unsupported targets fail with
`Superluminal requires a supported Windows target`. Only the MT host CRT is
supported; there is no CRT selection define.

```text
odin build app -o:speed -collection:superluminal_perf=<repository> -define:SUPERLUMINAL_ENABLED=false
```

### Static linking with MT

Supply the external API directory, containing `include` and `lib`, as the
`superluminal_sdk` collection:

```text
odin build app -o:speed -collection:superluminal_perf=<repository> -collection:superluminal_sdk=<API-root> -define:SUPERLUMINAL_LINK_MODE=static
```

The binding selects `lib/<x64-or-x86>/PerformanceAPI_MT.lib`, a static
archive, not a DLL import library. It matches Odin's default static release
CRT (`libcmt`); no CRT override linker flags are needed.

Preserve transitive SDK library directives. Do not use bare `/NODEFAULTLIB`,
`-no-crt`, `/FORCE:MULTIPLE`, or mismatch suppression to hide CRT conflicts.
Avoid introducing `core:c/libc` imports that explicitly request a conflicting
Windows CRT.

Runtime DLL mode has one SDK DLL per architecture. The DLL owns its CRT
dependencies independently of the MT host. Odin `-debug` selects debug
information, not a debug CRT; `-build-mode:dll` or `lib` selects the
application output type, not SDK integration mode.

## Raw C ABI

```odin
import raw "superluminal_perf:raw"
```

`raw` is independently usable. It exports API version constants,
`DEFAULT_COLOR`, the ordered eleven-pointer `Functions` table, named
`proc "c"` types for every operation, and `Get_API_Proc`.

|Table field|Enabled static procedure|
|---|---|
|`SetCurrentThreadName`|`SetCurrentThreadName`|
|`SetCurrentThreadNameN`|`SetCurrentThreadName_N`|
|`BeginEvent`|`BeginEvent`|
|`BeginEventN`|`BeginEvent_N`|
|`BeginEventWide`|`BeginEvent_Wide`|
|`BeginEventWideN`|`BeginEvent_Wide_N`|
|`EndEvent`|`EndEvent`|
|`RegisterFiber`|`RegisterFiber`|
|`UnregisterFiber`|`UnregisterFiber`|
|`BeginFiberSwitch`|`BeginFiberSwitch`|
|`EndFiberSwitch`|`EndFiberSwitch`|

Static procedures link with the `PerformanceAPI_` prefix and exist only in
enabled static builds. Dynamic consumers call `module.functions` instead.
Raw unsuffixed narrow functions accept NUL-terminated `cstring`; wide
functions accept NUL-terminated `[^]u16`. The `N` forms accept pointers and
explicit `u16` lengths with the same byte/code-unit convention as above.

`EndEvent` returns `Suppress_Tail_Call_Optimization`, an eight-byte-aligned
struct containing three signed `i64` values. Preserve this return type even
when discarding the result; declaring it void is ABI-incorrect. Its contents
are not meaningful application data.

```odin
Module :: struct {
    handle:    rawptr,
    functions: Functions,
}

load :: proc() -> (module: Module, err: Error)
load_from :: proc(path: [^]u16) -> (module: Module, err: Error)
free :: proc(module: ^Module) -> bool
```

`load()` uses the environment/executable-directory discovery described above.
`load_from` still requires a NUL-terminated absolute Windows file path;
nil or empty paths are invalid. Both return a completely zero module on
failure, releasing any handle acquired during validation.
Each module has one owner: copying the struct does not create another
ownership reference. Borrowed tables and pointers become invalid on unload.
`free(nil)` returns false; freeing a zero module succeeds. Successful free
clears both fields; failure retains the handle and table.

## Symbols for profiling

Build the profiled application with both optimization and debug information:

```text
odin build app -debug -o:speed -pdb-name:app.pdb
```

In the inspected Windows Odin toolchain, `-debug` enables LLVM CodeView
information in the object files and passes `/DEBUG` to MSVC `link.exe`.
Microsoft documents bare [`/DEBUG` as equivalent to `/DEBUG:FULL`](https://learn.microsoft.com/en-us/cpp/build/reference/debug-generate-debug-info).
This is not `/DEBUG:FASTLINK`, which Superluminal does not support. The
MSVC compiler switches `/Z7` and `/Zi` are not Odin compiler options;
Odin emits its own CodeView records rather than invoking `cl.exe`.
`-o:speed` alone does not enable these debug records, and `-pdb-name` alone
is not a substitute for `-debug`.

The capture and optimized-symbol builds in `tools/verify.py` use
`-debug -o:speed` and a per-build PDB path. Keep the matching EXE/DLL and
PDB from the same build available to the profiler. Use `-show-system-calls`
to inspect the actual linker invocation when changing toolchains.

## Discovery and verification

Normal consumers build directly with Odin. Verification uses the standalone
`tools/verify.py` driver and Python's standard library; no PowerShell driver,
third-party Python packages, or additional Odin verification executable is
required. Dependency discovery does not persist machine paths in project files.

|Parameter|Environment variable|
|---|---|
|`--odin`|`ODIN`|
|`--odinfmt`|`ODINFMT`|
|`--sdk-root`|`SUPERLUMINAL_SDK`|
|`--superluminal-cmd`|`SUPERLUMINAL_CMD`|
|`--vcvarsall`|None; otherwise located from developer-shell MSVC tools|

Explicit parameters precede environment values; invalid supplied values are
errors, not permission to fall back. Executables are then sought on PATH.
Odin and the formatter may also be found in immediate sibling checkouts;
multiple candidates require an explicit choice. SDK discovery checks
Superluminal uninstall registrations and Program Files candidates,
accepting either an API root or an installation parent. Candidates must
contain the expected header and architecture-specific libraries. Ambiguous
SDK installations require `--sdk-root`. The capture executable can be found
on PATH or adjacent to the selected API root. Discovery does not alter PATH,
registry entries, provider registration, licensing, or DLL search settings.

Create a project-local Python environment with uv; invoke its interpreter
directly rather than a global Python:

```bat
uv venv .venv --python 3.14
.venv\Scripts\python.exe tools\verify.py --level Contracts
.venv\Scripts\python.exe tools\verify.py --level Runtime
.venv\Scripts\python.exe tools\verify.py --level Capture
```

`Runtime` is the default. Each level includes the lower levels. Runtime and
Capture require x64 MSVC tools and the Windows SDK. Use a Windows developer
shell with `cl.exe` and `dumpbin.exe` on PATH, or pass the installed
`vcvarsall.bat` explicitly. Supply the other dependency parameters when
discovery is unavailable or ambiguous:

```bat
.venv\Scripts\python.exe tools\verify.py --level Capture --odin "<odin.exe>" --odinfmt "<odinfmt.exe>" --sdk-root "<API directory>" --superluminal-cmd "<SuperluminalCmd.exe>" --vcvarsall "<vcvarsall.bat>"
```

Each command runs in a fresh process with its own working directory,
environment, log, deadline, and owned process tree. The driver preserves
nonzero native exit codes, checks required output, and cleans only its own
processes. Independent phases continue after a failure to report the complete
result; a failed or skipped required phase keeps the final status nonzero.
Cancellation stops the run rather than retrying a failing command.

The verification contract covers:

The runner does not build or execute x86 code. It still uses the SDK's x86
DLL as an incompatible input to the x64 loader's rejection test; it does not
require x86 MSVC tools or the x86 static archive.

All Odin build and contract-test invocations explicitly use `-o:speed`.
Symbol and capture builds additionally use `-debug`. Linux verification is
type checking only, without code generation.

- C++/Odin ABI agreement on x64, including all field offsets
  and the struct return; deterministic public API contract tests.
- Real DLL loading, errors, retry, ownership, initialization, UTF conversion,
  string boundaries, and process-level panics with assertions disabled.
- Process-isolated automatic DLL discovery: executable directory versus CWD,
  Unicode environment directories, empty variables, explicit/environment
  precedence, invalid overrides without fallback, and exclusion of PATH.
- Four enabled x64 build-and-run cells: static MT and dynamic loading,
  each with single and separate modules at `-o:speed`.
  Every host uses Odin's default MT CRT.
- Allocation-free event calls using Odin panic allocators, CRT directives
  and dependencies, optimized ABI code generation, and optimized builds
  retaining debug information.
- Disabled Windows execution, disabled Linux checking, and source-only
  relocation to a path containing spaces and non-ASCII characters.
- Two real x64 captures: static MT and dynamic mode, with
  exact event counts, scope nesting, early returns, thread/fiber attribution,
  UTF-8/UTF-16 text, data, and color.

`tools/capture_check.cpp` consumes raw ETW records with `OpenTraceW` and
`ProcessTrace`. The runner builds it in its temporary directory using MSVC.
It verifies exact UTF-8 bytes and UTF-16 code units rather than relying on
locale-dependent `tracerpt` rendering. It also checks all fourteen scopes,
nesting, names, data, colors, thread/fiber attribution, and lost-event counters.
Capturing and resolving use separate CLI processes. Resolution preserves the
capture metadata's process selection and inherits the current user's persisted
Superluminal symbol settings: cache directory, symbol servers, search locations,
and PDB parser. Configure these in the GUI before running verification. The
runner does not disable remote servers, add local symbol paths, or substitute
a per-cell cache. Symbol resolution can take several minutes and is not bounded
by the fifteen-second capture duration.

Run Capture from a **non-elevated** Windows shell with Windows `sudo`
enabled. Only the capture helper and its CLI child are elevated through
`sudo --disable-input`; builds, tests, symbol resolution, and inspection retain
normal privileges. The helper uses the same project-local Python interpreter.
The driver grants the invoking user access to elevated-generated files with
`icacls.exe` on its own new temporary directory, without elevating that ACL
operation. It never changes SDK, installation, or system-directory permissions.
Cancelling UAC prevents further elevation attempts in that invocation.

Capture acceptance is script-driven, with no MCP connection, operator review,
or confirmation prompt. The driver invokes `SuperluminalCmd run`, the raw ETW
checker, `resolve`, and then serialized `llm` queries. It checks the resolved
process and thread IDs against the raw capture, exact event counts across all
function records, frame/child intervals and call-graph nesting, Unicode names,
worker attribution, and symbolized fiber-entry ancestry. Raw ETW checks retain
the exact data, color, encoding, and fiber-lifecycle assertions.

CLI analysis uses one owned worker job for the entire query sequence: the CLI
daemon survives individual clients, and each opened session is closed in
`finally`. Closing a session also releases its function, instance, and graph
queries. Commands are serialized; do not issue concurrent `llm` requests against
the same daemon while verification runs. Native exit codes and the CLI JSON
`Success`/session-open results must all indicate success. Missing tracing
privileges or an activated analysis license is a blocker, not a passing result.
Windows UAC may still require approval for the two capture operations.

The runner owns a unique temporary directory and removes its build outputs,
fixtures, captures, and logs on success or failure. Windows Job Objects bound
native child-process lifetimes; the elevated helper also observes cancellation
and parent-process termination. Cleanup failure is a verification failure.
The project `.venv` and configured shared symbol cache are retained, not removed
as verification scratch. The `.venv` is also excluded from source-relocation
checks.
Do not publish test binaries or traces as release artifacts: static-linked
binaries contain SDK code. These commands and criteria describe verification
to perform, not a claim that it has passed.

Contract tests are also separately runnable:

```bat
odin test ./tests/contracts -o:speed
```

Without `SUPERLUMINAL_TEST_DLL`, real-DLL checks are skipped rather than
passed. Set it to an absolute matching-architecture SDK DLL path for those
checks. The smoke example supports `--dll <absolute-path>`, `--capture`,
`--overflow-utf8`, `--overflow-utf16`, and `--uninitialized`. A successful
normal or capture run prints `Superluminal smoke passed` only after worker
completion and shutdown.

## License

Authored code and tooling use the repository's [MIT license](LICENSE).
`raw/types.odin` retains the applicable BSD notice for declarations derived
from Superluminal's public header. The external SDK remains subject to its
own license and is not redistributed here.
