# `fsspec-cli` async execution boundary

<!-- pyml disable line-length -->

Researched: 2026-07-15

Question: [shinybrar/vosfs#90](https://github.com/shinybrar/vosfs/issues/90)

Status: **Research complete; three human decisions remain open. No production code is added by this ticket.**

## Source baseline

| Component | Version / commit | Role in this question |
| --- | --- | --- |
| fsspec | [`2026.6.0` / `a2457004d03e0312f715f90f58873de5ab195a37`](https://github.com/fsspec/filesystem_spec/tree/a2457004d03e0312f715f90f58873de5ab195a37) | Async filesystem contract, sync bridge, and sync-to-async wrapper. |
| Typer | [`0.27.0` / `60af34b60ab2650a74af32c6ce340c5cfbceb3d8`](https://github.com/fastapi/typer/tree/60af34b60ab2650a74af32c6ce340c5cfbceb3d8) | Locked host-facing command application; this release vendors an adapted Click 8.3.1 core. |
| Click | [`8.4.2` / `b2e30a175449cfda909ee4fbf4a29a6a071cad53`](https://github.com/pallets/click/tree/b2e30a175449cfda909ee4fbf4a29a6a071cad53) | Upstream reference; its callback and teardown semantics corroborate Typer's vendored core. |
| CPython | [`3.13.5` / `6cb20a219a860eaf687b2d968b41c480c7461909`](https://github.com/python/cpython/tree/6cb20a219a860eaf687b2d968b41c480c7461909) | `asyncio` runner and nested-loop behavior. |
| `vosfs` | [`3860fcf7d0156a5c6b0c206ca4753443054f699f`](https://github.com/shinybrar/vosfs/tree/3860fcf7d0156a5c6b0c206ca4753443054f699f) | Top-priority native-async filesystem and concrete resource lifecycle. |

The installed local fsspec and Click versions were also inspected. The links above are immutable upstream source evidence.

## Executive finding

One backend-agnostic **async command core is viable**. For plain `ls`, it can await the selected filesystem's `_info(path)` and `_ls(path, detail=False)` coroutine hooks and keep every locked result-shape, ordering, diagnostic, and output rule. It need not inspect a backend class or protocol.

Raw synchronous filesystems are not part of that surface. Local and Memory may participate only through an explicit async adapter supplied by the host. fsspec's `AsyncFileSystemWrapper` can provide that adapter by moving each synchronous call to `asyncio.to_thread`, but fsspec labels it experimental. Automatic wrapping inside `fsspec-cli` would silently choose experimental thread and lifecycle behavior for the host, so it is not recommended.

There is one hard contradiction among the current locks:

1. Typer 0.27.0's vendored, adapted Click 8.3.1 core invokes callbacks synchronously and does not await a returned coroutine; upstream Click 8.4.2 independently corroborates the same behavior.
2. `asyncio.run()` is therefore needed at some synchronous framework boundary unless the public Typer seam changes, but it owns a new loop and cannot run inside another loop in the same thread.
3. The injected filesystems are currently host-owned live instances, so `fsspec-cli` must not close them.
4. `vosfs` lazily creates loop-associated HTTP clients and requires `await fs.aclose()`; its async instance deliberately rejects synchronous `close()`.

Consequently, the sole seam `App(filesystems).typer_app`, literal end-to-end async execution, host-owned reusable instances, and deterministic resource cleanup cannot all hold at once. A thin synchronous Typer adapter plus an explicit async lifecycle seam is the recommended resolution, but that relaxes the existing “sole stable seam” wording. The alternative is to transfer lifecycle ownership or inject factories/context managers instead of live instances.

## Primary-source facts

### 1. The fsspec async surface is the underscore coroutine surface

fsspec documents that async implementations derive from `AsyncFileSystem`, advertise `async_impl`, and expose `async def` variants under underscore-prefixed names. It explicitly says `asynchronous=True` is the mode for calling those methods directly with `await`. It also warns that async resource creation should be deferred and resource destruction may need to be awaited. ([async overview](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/docs/source/async.rst#L6-L21), [async construction and cleanup](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/docs/source/async.rst#L65-L87))

`AsyncFileSystem` lists `_ls` and `_info` among the expected coroutine hooks, sets `async_impl=True`, and records whether an instance was constructed in asynchronous mode. `asynchronous=False` attaches the instance to fsspec's background loop; `asynchronous=True` leaves that sync-bridge loop unset. ([expected hooks](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py#L302-L340), [mode and loop state](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py#L343-L357), [`_info` and `_ls`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py#L742-L746))

The public names such as `info` and `ls` on an async implementation are synchronous mirrors. fsspec creates them with `sync_wrapper`, which submits a coroutine to another loop and blocks the calling thread for its result. That bridge rejects a call made from the same running loop. ([sync bridge](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py#L63-L120), [sync method mirroring](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/asyn.py#L971-L1004))

**Fact-derived boundary:** production command logic that is async-only must await `_info`, `_ls`, and later command-specific underscore hooks. It must not call `info`, `ls`, `fsspec.asyn.sync`, `sync_wrapper`, or `get_loop`.

The underscore naming means this is a deliberately selected, version-tested fsspec interface, not a universal promise for all `AbstractFileSystem` implementations. The per-command compatibility matrix must retain the exact fsspec version used for each result.

### 2. Raw Local and Memory are synchronous; the official wrapper uses threads

`AbstractFileSystem` defaults `async_impl=False`. Both `LocalFileSystem` and `MemoryFileSystem` derive directly from it and implement ordinary `def info` and `def ls` methods. ([base flags](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L103-L119), [Local methods](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/local.py#L19-L78), [Memory methods](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L17-L43), [Memory `info`](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/memory.py#L149-L169))

`AsyncFileSystemWrapper` derives from `AsyncFileSystem`, retains the supplied sync instance, and installs underscore coroutine wrappers for its public synchronous methods. Each wrapper awaits `asyncio.to_thread`; an optional semaphore limits concurrent calls. If `asynchronous` is omitted, the wrapper guesses its mode from whether construction occurs under a running loop. ([wrapper implementation](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/implementations/asyn_wrapper.py#L11-L97))

fsspec calls the wrapper experimental, says it is for interfaces that expect `AsyncFileSystem`, and makes no speed claim. ([wrapper status and limitations](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/docs/source/async.rst#L156-L188)) Python documents `asyncio.to_thread` as a way to await otherwise blocking I/O without blocking the event-loop thread. ([`asyncio.to_thread`](https://github.com/python/cpython/blob/6cb20a219a860eaf687b2d968b41c480c7461909/Doc/library/asyncio-task.rst#L1001-L1049))

**Fact-derived boundary:** a wrapped Local or Memory instance gives the CLI an awaitable surface, but the backend remains synchronous work executed in a worker thread. “Async-only CLI” can truthfully mean that the command core never blocks the event-loop thread; it cannot mean that every supported backend performs native non-blocking I/O.

### 3. Typer and Click do not await command callbacks

Typer 0.27.0 does not dispatch through the independently installed Click 8.4.2 core. It imports its own `_click` package, explicitly described as adapted from Click 8.3.1. ([Typer import](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/main.py#L18-L24), [vendored-core provenance](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/_click/__init__.py#L1-L9))

Typer converts a registered callback into an ordinary wrapper and directly returns `callback(**use_params)`. Its vendored `Context.invoke` then directly calls the wrapper, and vendored `Command.invoke` returns that result. None detects or awaits an awaitable. ([Typer callback wrapper](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/main.py#L1496-L1527), [vendored callback invocation](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/_click/core.py#L474-L489), [vendored command dispatch](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/_click/core.py#L734-L746))

Typer's `add_typer` support composes one Typer application beneath another; during materialization it recursively rebuilds groups and builds each command callback independently. An unnamed mounted group may even lose its callback. The async bridge therefore belongs in each concrete command adapter, not in `Typer.__call__` or a group callback. ([Typer composition](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/docs/tutorial/subcommands/add-typer.md#L47-L69), [recursive materialization](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/main.py#L1283-L1357), [per-command callback construction](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/main.py#L1392-L1434))

Typer's vendored context teardown is synchronous: `Context.close` drains a regular `ExitStack`, not an `AsyncExitStack`. External Click 8.4.2 has the same direct callback and synchronous-teardown behavior. ([vendored context teardown](https://github.com/fastapi/typer/blob/60af34b60ab2650a74af32c6ce340c5cfbceb3d8/typer/_click/core.py#L265-L376), [Click 8.4.2 callback invocation](https://github.com/pallets/click/blob/b2e30a175449cfda909ee4fbf4a29a6a071cad53/src/click/core.py#L846-L907), [Click 8.4.2 teardown](https://github.com/pallets/click/blob/b2e30a175449cfda909ee4fbf4a29a6a071cad53/src/click/core.py#L675-L708))

**Hard consequence:** an `async def` Typer callback is not a working async integration. With the locked Typer version and dependency boundary, some synchronous adapter must enter async execution, or the public interface must change.

### 4. Python's normal runner owns one loop and forbids nesting

`asyncio.run` creates a new event loop, finalizes async generators, shuts down the default executor, and closes the loop. It fails when another event loop is already running in the same thread. ([runner implementation](https://github.com/python/cpython/blob/6cb20a219a860eaf687b2d968b41c480c7461909/Lib/asyncio/runners.py#L160-L195), [runner contract](https://github.com/python/cpython/blob/6cb20a219a860eaf687b2d968b41c480c7461909/Doc/library/asyncio-runner.rst#L22-L50))

`asyncio.Runner` permits several top-level coroutine calls on one owned loop, but `Runner.run` has the same same-thread running-loop prohibition and closes its loop, async generators, and executor when the context exits. ([Runner lifecycle](https://github.com/python/cpython/blob/6cb20a219a860eaf687b2d968b41c480c7461909/Lib/asyncio/runners.py#L20-L94), [Runner close](https://github.com/python/cpython/blob/6cb20a219a860eaf687b2d968b41c480c7461909/Lib/asyncio/runners.py#L64-L79))

`Runner` was added in Python 3.11, while this repository currently supports Python 3.10. Unless the independent `fsspec-cli` project chooses a newer floor, a production boundary cannot depend on `Runner`; `asyncio.run` is the portable built-in runner. ([Runner version](https://github.com/python/cpython/blob/6cb20a219a860eaf687b2d968b41c480c7461909/Doc/library/asyncio-runner.rst#L74-L116))

Moving the adapter to a new thread would avoid the literal nested-loop exception, but a synchronous Click callback would still block its caller while waiting. It would also move injected filesystem work to a loop that may not own the filesystem's resources. That is not a generic async-host solution.

### 5. `vosfs` proves that loop and resource ownership matter

`VOSpaceFileSystem` is a native `AsyncFileSystem`; it implements `_info` and `_ls` as coroutines. It defaults `asynchronous=False`, so an async-only host must explicitly construct it with `asynchronous=True`. ([class and constructor](https://github.com/shinybrar/vosfs/blob/3860fcf7d0156a5c6b0c206ca4753443054f699f/src/vosfs/filesystem.py#L53-L107), [plain-list hooks](https://github.com/shinybrar/vosfs/blob/3860fcf7d0156a5c6b0c206ca4753443054f699f/src/vosfs/filesystem.py#L245-L264))

Its HTTP clients are lazy and explicitly scoped per filesystem instance and event loop. `aclose()` closes them and permanently marks the pool closed. `VOSpaceFileSystem.aclose()` also evicts the instance from fsspec's cache; synchronous `close()` rejects an `asynchronous=True` instance and tells the caller to await `aclose()`. ([lazy loop-scoped pool](https://github.com/shinybrar/vosfs/blob/3860fcf7d0156a5c6b0c206ca4753443054f699f/src/vosfs/transport.py#L47-L105), [pool cleanup](https://github.com/shinybrar/vosfs/blob/3860fcf7d0156a5c6b0c206ca4753443054f699f/src/vosfs/transport.py#L162-L174), [filesystem cleanup](https://github.com/shinybrar/vosfs/blob/3860fcf7d0156a5c6b0c206ca4753443054f699f/src/vosfs/filesystem.py#L856-L876))

fsspec's instance cache can retain filesystem instances after user references disappear; async instances are cached by thread identity, not event-loop identity. `skip_instance_cache=True` avoids that reuse, while `clear_instance_cache` clears the class-wide cache. ([cache identity and retention](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L36-L100), [cache opt-out](https://github.com/fsspec/filesystem_spec/blob/a2457004d03e0312f715f90f58873de5ab195a37/fsspec/spec.py#L131-L154))

`App` can validate mode and coroutine shape, but it cannot generically prove that an injected live instance has never realized a resource on some prior loop. Thread-keyed instance caching makes that missing evidence material.

**Hard consequence:** a Typer callback cannot create a temporary loop, use a host-owned reusable `VOSpaceFileSystem`, close the loop, and leave deterministic cleanup to the host. The host no longer has the owning loop on which to await cleanup. Closing the filesystem inside the callback instead transfers lifecycle ownership to `fsspec-cli` and makes the injected instance unusable for later host work.

## Recommended compatibility boundary

The following recommendations are independent of the unresolved runner/lifecycle choice.

### App construction

For each supplied mapping value, `App` should validate only generic async shape and mode:

1. it is an `AsyncFileSystem` instance (and therefore also the already-locked `AbstractFileSystem` type);
2. `async_impl is True`;
3. `asynchronous is True`.

For plain `ls`, the required hooks are `_info` and `_ls`. `AsyncFileSystem` supplies coroutine-shaped base hooks, so constructor inspection cannot prove operational support: inherited hooks may still raise `NotImplementedError`. The real awaited call remains capability evidence and maps to the locked per-operand `unsupported operation` result.

An instance with `async_impl=True` but `asynchronous=False` must be rejected at `App` construction. Letting it through would permit its public synchronous mirrors and fsspec background loop to leak back into production behavior.

The validation must not inspect `protocol`, compare a concrete backend class, import Local, Memory, or `vosfs`, or maintain a backend capability registry. New commands should identify their required coroutine hooks and retain real awaited calls, result-shape validation, and runtime exceptions as the compatibility evidence.

### Native and adapted instances

- A native async implementation is accepted when the generic checks pass.
- A raw synchronous instance is rejected.
- A host may explicitly supply `AsyncFileSystemWrapper(raw_fs, asynchronous=True)`. Once supplied, it is treated exactly like any other async filesystem; `fsspec-cli` does not branch on the wrapper class.
- `fsspec-cli` should not automatically create that wrapper. The wrapper is experimental, thread-affinity and thread-safety are backend properties, and implicit wrapping would make the library choose those risks for the host.
- The tested command matrix should distinguish `native async` from `adapted async`; both may be tested, but an adapted result is not evidence of native async support or improved performance.

### Command execution

The async plain-`ls` core should preserve the locked sequence:

```python
info = await filesystem._info(path)
if info["type"] == "directory":
    names = await filesystem._ls(path, detail=False)
```

This is interface pseudocode, not production implementation. All existing preflight, validation, buffering, continuation, diagnostics, sorting, and output rules remain unchanged. Operand processing remains in argument order unless a later profile explicitly permits concurrency.

The command core must never call a public filesystem operation, `fsspec.asyn.sync`, `sync_wrapper`, `get_loop`, `asyncio.run`, or `Runner.run`. The runner belongs in a zero-command-logic synchronous adapter attached to each concrete Typer command.

## Backend implications

| Filesystem | Raw instance | Async-compatible form | Research conclusion |
| --- | --- | --- | --- |
| Local | Reject: sync `info`/`ls`, `async_impl=False`. | Host-supplied `AsyncFileSystemWrapper(LocalFileSystem(), asynchronous=True)`. | Plain `ls` remains testable through awaited thread offload. Matrix status must say `adapted async`; wrapper is experimental. |
| Memory | Reject: sync `info`/`ls`, `async_impl=False`. | Host-supplied `AsyncFileSystemWrapper(MemoryFileSystem(), asynchronous=True)`. | Same boundary as Local. Global in-process store semantics do not become native async semantics. |
| `vosfs` | Reject default `asynchronous=False`; accept native instance constructed with `asynchronous=True`. | `VOSpaceFileSystem(..., asynchronous=True)`; a disposable host may also choose `skip_instance_cache=True`. | Native async plain `ls` is viable. The owning loop must remain available through `await aclose()`, so runner ownership cannot be hand-waved. |
| Other fsspec backend | Reject raw sync or wrong-mode instances. | Native async or explicit host-supplied async adapter that passes generic checks. | Compatibility is command-, backend-, and version-tested; never universal. |

## Event-loop and lifecycle choices

### Option A — preserve host ownership and add an async lifecycle seam (**recommended**)

Keep `App(filesystems).typer_app` as the Typer composition seam, but no longer call it the sole stable integration seam. Add an async invocation/lifecycle surface that a host can await on the same loop that owns its filesystems. The host remains responsible for closing its preconfigured instances. A thin synchronous Typer callback may use `asyncio.run` only for a one-shot configuration whose injected instance is still loop-neutral and whose async resource realization, use, and host-approved cleanup all occur inside that owned runner.

This best preserves async-only command logic, reusable host-owned `vosfs` instances, and true async host embedding. It does require a new stable async seam or a host-supplied async lifecycle callback/factory.

### Option B — transfer disposable instance ownership to `App`

Keep a synchronous Typer adapter that owns one runner and require injected instances to be disposable for one invocation. `App` must then receive a generic, awaitable cleanup contract and close every resource before its runner closes. A plain `Mapping[str, AbstractFileSystem]` is insufficient because fsspec does not define one universal `aclose()` method.

This requires changing the injection contract to async context managers/factories or adding explicit cleanup callbacks. It contradicts the current host-owned-live-instance wording.

### Option C — retain only `typer_app` and use a background-loop sync bridge (**not recommended**)

The Click callback could submit command coroutines to another loop and wait, mirroring fsspec's sync facade. This preserves a synchronous Typer surface but violates the locked async-only direction, blocks the callback thread, complicates nested hosts, and makes loop association of injected instances ambiguous.

### Option D — call `asyncio.run` around borrowed host instances and do not close them (**invalid for `vosfs`**)

This is superficially simple but closes the loop before the host can deterministically dispose of loop-associated resources. It must not be selected.

## Running-loop behavior

If a synchronous Typer adapter is approved, ordinary `add_typer` composition remains supported because composition itself does not run a loop. Invocation from a normal shell or Click test runner can enter the adapter's loop.

Invocation of that synchronous Typer callback from a thread that already runs an event loop cannot be made transparently async by `asyncio.run` or `Runner.run`. The adapter should detect this before creating a coroutine and return one stable configuration/usage diagnostic. An async host must instead await the proposed async seam. Offloading the entire synchronous CLI invocation to a worker thread is viable only under a complete worker-owned lifecycle profile: every injected instance must be loop-neutral beforehand, all async resources must be realized and used on the worker's loop, and host-approved cleanup must finish there before that loop closes. The library must not apply `nest_asyncio`, start a hidden per-call thread, or submit a coroutine back to the caller's own blocked loop.

## Human decisions required before this becomes a locked contract

1. **Typer boundary:** Does “all CLI work is async” permit one synchronous framework adapter because Typer/Click do not await callbacks? **Recommendation: yes; define async-only as all command orchestration and filesystem I/O beneath that adapter.** If no, `App(filesystems).typer_app` must be replaced or backed by a different async-capable command framework.
2. **Resource ownership:** Which current lock may change? **Recommendation: preserve host-owned live instances and add an async invocation/lifecycle seam (Option A).** Alternative: transfer disposable instance ownership and replace the plain mapping with factories/context managers plus cleanup (Option B).
3. **Sync-backend adaptation:** Should raw sync instances be rejected while host-supplied `AsyncFileSystemWrapper(..., asynchronous=True)` instances are accepted and marked `adapted async`? **Recommendation: yes; never auto-wrap.**

These decisions are coupled enough that they should be answered together. Locking only the runner while leaving resource ownership implicit would produce an unusable `vosfs` lifecycle.

## Implementation handoff after the decisions

The later tracer should prove, test-first:

- raw Local, raw Memory, and wrong-mode native async instances fail at `App` construction before any command call;
- wrapped Local, wrapped Memory, and `VOSpaceFileSystem(asynchronous=True)` pass the same structural validation;
- one handler awaits `_info` and `_ls` and never touches their public sync mirrors;
- `NotImplementedError` and incompatible result shapes retain the locked command outcomes;
- neither production code nor tests branch on backend type or protocol;
- ordinary Typer invocation and parent `add_typer` composition preserve the locked output and exit behavior;
- invocation under an already-running loop follows the selected explicit contract, with no raw nested-loop traceback or un-awaited coroutine warning;
- resource cleanup is awaited on the owning loop, `vosfs` is closed exactly once by its selected owner, and no fsspec instance-cache entry leaks a loop-bound closed instance; and
- the compatibility matrix records native versus adapted async evidence with exact versions.

## Scope boundary

This research does not select an async CLI framework, add a console script, construct or authenticate filesystems, alter `vosfs`, or implement commands. It does not claim every fsspec backend is async-compatible. It defines the evidence-backed boundary that issue #83 must use when sequencing the production tracer.
